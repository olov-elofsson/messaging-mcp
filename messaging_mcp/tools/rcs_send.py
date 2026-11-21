"""RCS sends: text, file, card, carousel or operator template, with buttons.

Texts can be personal ({{firstName}}), people without RCS can get an SMS
instead, and a campaign can be limited to a send window and a speed.
"""

import datetime
from typing import Any

from fastmcp.exceptions import ToolError

from messaging_mcp import access, api, rcs_content, rcs_delivery, sending
from messaging_mcp.app import mcp
from messaging_mcp.audience import Recipient, load_audience
from messaging_mcp.confirm import propose_or_apply

RCS_PATH = "/rcs/2/messages"
_CAPABILITY_BATCH = 500
# A personal campaign sends one message per recipient, so requests are smaller.
_PERSONAL_BATCH = 100
_TEST_VALUES = {"firstName": "Test", "lastName": "Testsson"}
_LAUNCHED = ("LAUNCHED", "LAUNCHED_PARTIAL_SUCCESS", "LAUNCHED_IN_PROGRESS")


def rcs_request(
    sender: str,
    parts: list[tuple[list[str], dict[str, Any], str | None]],
    reference: str,
    bulk_id: str,
    send_at: str | None,
    message_options: dict[str, Any] | None = None,
    speed: dict[str, Any] | None = None,
    tracking: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds one request. Each part is (numbers, content, SMS fallback text)."""
    messages = []
    for numbers, built, fallback in parts:
        options: dict[str, Any] = {"campaignReferenceId": reference}
        options.update(message_options or {})
        if fallback:
            options["smsFailover"] = {"text": fallback}
        messages.append(
            {
                "sender": sender,
                "destinations": [{"to": number} for number in numbers],
                "content": built,
                "options": options,
            }
        )
    request_options = sending.schedule_options(bulk_id, send_at)
    request_options["schedule"].update(speed or {})
    if tracking:
        request_options["tracking"] = tracking
    return {"messages": messages, "options": request_options}


def rcs_body(
    sender: str,
    built: dict[str, Any],
    numbers: list[str],
    reference: str,
    bulk_id: str,
    send_at: str | None,
    sms_fallback_text: str | None = None,
) -> dict[str, Any]:
    return rcs_request(
        sender, [(numbers, built, sms_fallback_text)], reference, bulk_id, send_at
    )


def _identity(
    sender: str, content: dict[str, Any], sms_fallback_text: str | None
) -> dict[str, Any]:
    """Checks the message and returns what a test send is bound to."""
    access.require_allowed_user()
    if not sender or not sender.strip():
        raise ToolError("sender is required.")
    fallback = (sms_fallback_text or "").strip() or None
    # Checks the placeholders and builds once to catch mistakes early.
    rcs_content.placeholders_in({"content": content, "fallback": fallback})
    rcs_content.build(rcs_content.render(content, _TEST_VALUES))
    return {
        "channel": "RCS",
        "sender": sender.strip(),
        "content": content,
        "sms_fallback_text": fallback,
    }


def _for(identity: dict[str, Any], values: dict[str, str | None]) -> tuple[dict[str, Any], str | None]:
    built = rcs_content.build(rcs_content.render(identity["content"], values))
    fallback = identity["sms_fallback_text"]
    if fallback:
        fallback = rcs_content.render({"t": fallback}, values)["t"]
    return built, fallback


def _summary(identity: dict[str, Any], values: dict[str, str | None]) -> dict[str, Any]:
    built, fallback = _for(identity, values)
    placeholders = rcs_content.placeholders_in(
        {"content": identity["content"], "fallback": identity["sms_fallback_text"]}
    )
    summary: dict[str, Any] = {
        "sender": identity["sender"],
        "message": rcs_content.describe(built),
        "sms_fallback_text": fallback or "none: people without RCS get nothing",
    }
    if placeholders:
        summary["personal_fields"] = {
            name: (f"default '{default}'" if default else "no default")
            for name, default in placeholders.items()
        }
    return summary


def _sender_status(account: str, sender: str) -> str | None:
    try:
        answer = api.request(account, "GET", f"/rcs/1/senders/{sender}")
    except ToolError:
        return None
    return (answer or {}).get("senderStatus")


def _values(recipient: Recipient) -> dict[str, str | None]:
    return {"firstName": recipient.first_name, "lastName": recipient.last_name}


def _send_day(scheduled: str | None) -> datetime.date:
    if scheduled:
        moment = datetime.datetime.strptime(scheduled, "%Y-%m-%dT%H:%M:%S.000+0000")
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    else:
        moment = datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(rcs_delivery.LOCAL_ZONE).date()


@mcp.tool
def rcs_preview(
    account: str,
    sender: str,
    content: dict[str, Any],
    sms_fallback_text: str | None = None,
) -> dict[str, Any]:
    """Checks an RCS message and shows how it is built. Sends nothing.

    Personal fields are shown filled in with example values. See
    rcs_send_test for the content format.

    Args:
        account: Account alias from accounts_list.
        sender: RCS sender registered in the account.
        content: The message.
        sms_fallback_text: SMS text for people whose phone has no RCS.
    """
    identity = _identity(sender, content, sms_fallback_text)
    status = _sender_status(account, identity["sender"])
    return {
        "account": account,
        "sender_status": status or "unknown",
        "example_with_values": _TEST_VALUES,
        **_summary(identity, _TEST_VALUES),
    }


@mcp.tool
def rcs_send_test(
    account: str,
    sender: str,
    content: dict[str, Any],
    to: list[str],
    sms_fallback_text: str | None = None,
    test_first_name: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Sends an RCS test message to a few numbers. Two steps.

    The applied result contains a test_token that rcs_send_campaign needs.
    A sender that is not launched yet only reaches its registered test
    numbers (rcs_list_test_numbers).

    Args:
        account: Account alias from accounts_list.
        sender: RCS sender registered in the account.
        content: The message. One of:
            {"type": "TEXT", "text": "...", "buttons": [...]}
            {"type": "FILE", "file_url": "https://...", "thumbnail_url": "https://..."}
            {"type": "CARD", "title": "...", "description": "...",
             "image_url": "https://...", "image_height": "SHORT|MEDIUM|TALL",
             "thumbnail_url": "https://...", "orientation": "VERTICAL|HORIZONTAL",
             "image_side": "LEFT|RIGHT", "buttons": [...]}
            {"type": "CAROUSEL", "card_width": "SMALL|MEDIUM",
             "cards": [{"title": "...", "description": "...",
             "image_url": "https://...", "buttons": [...]}, ...],
             "buttons": [...]}
            {"type": "TEMPLATE", "name": "...", "parameters": {"name": "value"}}
            A button is one of:
            {"type": "REPLY", "text": "..."}
            {"type": "OPEN_URL", "text": "...", "url": "https://...",
             "open_in": "BROWSER|WEBVIEW", "webview_size": "FULL|HALF|TALL"}
            {"type": "DIAL_PHONE", "text": "...", "phone_number": "46..."}
            {"type": "SHOW_LOCATION", "text": "...", "latitude": 59.33,
             "longitude": 18.06, "label": "..."}
            {"type": "REQUEST_LOCATION", "text": "..."}
            {"type": "CREATE_CALENDAR_EVENT", "text": "...",
             "start_time": "2026-12-01T18:00:00+01:00",
             "end_time": "2026-12-01T20:00:00+01:00", "title": "...",
             "description": "..."}
            Button text is at most 25 characters. A card has at most 4
            buttons, a message at most 11, a carousel 2 to 10 cards. Files and
            images must be public https links. Texts may contain
            {{firstName}} and {{lastName}}, with a default after a bar:
            {{firstName|kund}}. A TEMPLATE must be approved for the sender.
        to: One to five phone numbers in international format.
        sms_fallback_text: SMS text for people whose phone has no RCS.
        test_first_name: First name used for {{firstName}} in the test.
        validate_only: True proposes, False sends the test.
        checksum: Checksum from the proposal. Required when sending.
    """
    identity = _identity(sender, content, sms_fallback_text)
    numbers = sending.clean_test_recipients(to, "RCS")
    values = dict(_TEST_VALUES)
    if test_first_name:
        values["firstName"] = test_first_name
    built, fallback = _for(identity, values)

    def apply() -> dict[str, Any]:
        reference = sending.new_reference("test-rcs")
        api.request(
            account,
            "POST",
            RCS_PATH,
            json=rcs_request(
                identity["sender"], [(numbers, built, fallback)], reference,
                reference, None,
            ),
        )
        return {
            "campaign_reference_id": reference,
            "test_token": sending.test_token(account, "RCS", identity),
        }

    return propose_or_apply(
        account=account,
        action="send_test_rcs",
        request={"to": numbers, "content": identity, "values": values},
        current=None,
        summary={"sends_test_to": numbers, **_summary(identity, values)},
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )


@mcp.tool
def rcs_send_campaign(
    account: str,
    sender: str,
    content: dict[str, Any],
    audience_kind: str,
    audience_value: str,
    test_token: str,
    sms_fallback_text: str | None = None,
    send_at: str | None = None,
    validity_hours: int | None = None,
    send_window: dict[str, Any] | None = None,
    max_per_hour: int | None = None,
    track_clicks: bool = False,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Sends an RCS message to a tag or a segment. Two steps.

    Nothing is sent by the first call. It returns the message as the first
    recipient would see it, the number of recipients, the sender's launch
    status and the delivery settings. Only after an explicit yes may the
    second call be made. A test send (rcs_send_test) of exactly the same
    content, sender and SMS fallback must have been made first.

    Args:
        account: Account alias from accounts_list.
        sender: RCS sender registered in the account.
        content: The message, in the same format as for rcs_send_test.
        audience_kind: 'tag' or 'segment'.
        audience_value: The tag name, or the segment ID.
        test_token: Token from the applied rcs_send_test.
        sms_fallback_text: SMS text for people whose phone has no RCS.
            Without it they get nothing. May contain {{firstName}}.
        send_at: When to send, with time zone, e.g. 2026-12-01T09:00:00+01:00.
        validity_hours: How long Infobip keeps trying, 1 to 48 hours.
        send_window: Only deliver within these hours, in Swedish time, e.g.
            {"days": ["MONDAY", "FRIDAY"], "from": "09:00", "to": "18:00"}.
            Days default to Monday to Friday.
        max_per_hour: Spread the send out to at most this many per hour.
        track_clicks: Shorten links and count clicks (shown by
            reports_statistics).
        validate_only: True proposes, False sends.
        checksum: Checksum from the proposal. Required when sending.
    """
    identity = _identity(sender, content, sms_fallback_text)
    scheduled = sending.parse_send_at(send_at)
    sending.require_test_token(account, "RCS", identity, test_token)
    message_options = rcs_delivery.message_options(
        validity_hours, send_window, _send_day(scheduled)
    )
    request_options = rcs_delivery.request_options(max_per_hour, track_clicks)
    audience = load_audience(account, audience_kind, audience_value, "RCS")
    if not audience.recipients:
        raise ToolError("The audience has no one with a phone number.")
    placeholders = rcs_content.placeholders_in(
        {"content": identity["content"], "fallback": identity["sms_fallback_text"]}
    )
    for name, default in placeholders.items():
        missing = [r for r in audience.recipients if not _values(r).get(name)]
        if missing and default is None:
            raise ToolError(
                f"{len(missing)} recipients have no {name}. Give a default, e.g. "
                f"{{{{{name}|kund}}}}, or remove the placeholder."
            )
    # Builds every recipient's message now, so a too long text fails here.
    personal = {
        r.address: _for(identity, _values(r)) for r in audience.recipients
    } if placeholders else None
    status = _sender_status(account, identity["sender"])
    warnings = []
    if status not in _LAUNCHED:
        warnings.append(
            f"The sender's status is {status or 'unknown'}. Until it is launched, "
            "only its test numbers receive RCS."
        )
    if not identity["sms_fallback_text"]:
        warnings.append("Without sms_fallback_text, people without RCS get nothing.")

    def send_batch(batch: list[str], bulk_id: str, reference: str) -> Any:
        if personal:
            parts = [([n], *personal[n]) for n in batch]
        else:
            built, fallback = _for(identity, {})
            parts = [(batch, built, fallback)]
        return api.request(
            account,
            "POST",
            RCS_PATH,
            json=rcs_request(
                identity["sender"], parts, reference, bulk_id, scheduled,
                message_options, request_options["speed"], request_options["tracking"],
            ),
        )

    def apply() -> dict[str, Any]:
        reference = sending.new_reference("rcs")
        return sending.send_in_batches(
            [r.address for r in audience.recipients],
            reference,
            lambda batch, bulk_id: send_batch(batch, bulk_id, reference),
            batch_size=_PERSONAL_BATCH if personal else sending.BATCH_SIZE,
        )

    first = audience.recipients[0]
    return propose_or_apply(
        account=account,
        action="send_rcs_campaign",
        request={
            "content": identity,
            "audience": [audience.kind, audience.value],
            "send_at": scheduled,
            "message_options": message_options,
            "request_options": request_options,
        },
        current={"audience": audience.fingerprint()},
        summary={
            **_summary(identity, _values(first)),
            "shown_as_for": first.address if placeholders else None,
            **audience.summary(),
            "sender_status": status or "unknown",
            "send_time_utc": scheduled or "immediately after confirmation",
            "delivery": rcs_delivery.describe(
                validity_hours, send_window, max_per_hour, track_clicks
            ),
            "cost": "Shown by reports_delivery after sending.",
            "warnings": warnings,
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )


@mcp.tool
def rcs_check_capability(
    account: str, sender: str, audience_kind: str, audience_value: str
) -> dict[str, Any]:
    """Counts how many in a tag or segment can receive RCS. Sends nothing.

    Use it before an RCS campaign to see how many would need the SMS
    fallback.

    Args:
        account: Account alias from accounts_list.
        sender: RCS sender registered in the account.
        audience_kind: 'tag' or 'segment'.
        audience_value: The tag name, or the segment ID.
    """
    audience = load_audience(account, audience_kind, audience_value, "RCS")
    numbers = [r.address for r in audience.recipients]
    codes: dict[str, int] = {}
    for start in range(0, len(numbers), _CAPABILITY_BATCH):
        answer = api.request(
            account,
            "POST",
            "/rcs/2/capability-check/query",
            json={
                "sender": sender,
                "phoneNumbers": numbers[start : start + _CAPABILITY_BATCH],
            },
        )
        for result in (answer or {}).get("capabilityCheckResults") or []:
            code = str(result.get("code") or "UNKNOWN_CODE")
            codes[code] = codes.get(code, 0) + 1
    enabled = codes.get("ENABLED", 0)
    return {
        "audience": f"{audience.kind}: {audience.label}",
        "numbers_checked": len(numbers),
        "can_receive_rcs": enabled,
        "cannot_receive_rcs": len(numbers) - enabled,
        "by_result": codes,
    }
