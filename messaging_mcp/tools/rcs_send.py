"""Rich RCS sends: text with buttons, a card, or a carousel of cards."""

from typing import Any

from fastmcp.exceptions import ToolError

from messaging_mcp import access, api, rcs_content, sending
from messaging_mcp.app import mcp
from messaging_mcp.audience import load_audience
from messaging_mcp.confirm import propose_or_apply

RCS_PATH = "/rcs/2/messages"
_CAPABILITY_BATCH = 500


def rcs_body(
    sender: str,
    built: dict[str, Any],
    numbers: list[str],
    reference: str,
    bulk_id: str,
    send_at: str | None,
    sms_fallback_text: str | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {"campaignReferenceId": reference}
    if sms_fallback_text:
        options["smsFailover"] = {"text": sms_fallback_text}
    return {
        "messages": [
            {
                "sender": sender,
                "destinations": [{"to": number} for number in numbers],
                "content": built,
                "options": options,
            }
        ],
        "options": sending.schedule_options(bulk_id, send_at),
    }


def _prepare(
    sender: str, content: dict[str, Any], sms_fallback_text: str | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (what identifies the message, the built API content)."""
    access.require_allowed_user()
    if not sender or not sender.strip():
        raise ToolError("sender is required.")
    built = rcs_content.build(content)
    fallback = (sms_fallback_text or "").strip() or None
    identity = {
        "channel": "RCS",
        "sender": sender.strip(),
        "content": built,
        "sms_fallback_text": fallback,
    }
    return identity, built


def _summary(identity: dict[str, Any], built: dict[str, Any]) -> dict[str, Any]:
    return {
        "sender": identity["sender"],
        "message": rcs_content.describe(built),
        "sms_fallback_text": identity["sms_fallback_text"]
        or "none: people without RCS get nothing",
    }


@mcp.tool
def rcs_preview(
    account: str,
    sender: str,
    content: dict[str, Any],
    sms_fallback_text: str | None = None,
) -> dict[str, Any]:
    """Checks a rich RCS message and shows how it is built. Sends nothing.

    Args:
        account: Account alias from accounts_list.
        sender: RCS sender registered in the account.
        content: The message, see rcs_send_test for the format.
        sms_fallback_text: SMS text for people whose phone has no RCS.
    """
    identity, built = _prepare(sender, content, sms_fallback_text)
    return {"account": account, **_summary(identity, built)}


@mcp.tool
def rcs_send_test(
    account: str,
    sender: str,
    content: dict[str, Any],
    to: list[str],
    sms_fallback_text: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Sends a rich RCS test message to a few numbers. Two steps.

    The applied result contains a test_token that rcs_send_campaign needs.

    Args:
        account: Account alias from accounts_list.
        sender: RCS sender registered in the account.
        content: The message. One of:
            {"type": "TEXT", "text": "...", "buttons": [...]}
            {"type": "CARD", "title": "...", "description": "...",
             "image_url": "https://...", "image_height": "MEDIUM",
             "buttons": [...]}
            {"type": "CAROUSEL", "cards": [{"title": "...",
             "description": "...", "image_url": "https://...",
             "buttons": [...]}, ...], "buttons": [...]}
            A button is {"type": "REPLY", "text": "..."},
            {"type": "OPEN_URL", "text": "...", "url": "https://..."} or
            {"type": "DIAL_PHONE", "text": "...", "phone_number": "46..."}.
            Button text is at most 25 characters. A card has at most 4
            buttons, a carousel 2 to 10 cards. Images must be public
            https links.
        to: One to five phone numbers in international format.
        sms_fallback_text: SMS text for people whose phone has no RCS.
        validate_only: True proposes, False sends the test.
        checksum: Checksum from the proposal. Required when sending.
    """
    identity, built = _prepare(sender, content, sms_fallback_text)
    numbers = sending.clean_test_recipients(to, "RCS")

    def apply() -> dict[str, Any]:
        reference = sending.new_reference("test-rcs")
        api.request(
            account,
            "POST",
            RCS_PATH,
            json=rcs_body(
                identity["sender"], built, numbers, reference, reference, None,
                identity["sms_fallback_text"],
            ),
        )
        return {
            "campaign_reference_id": reference,
            "test_token": sending.test_token(account, "RCS", identity),
        }

    return propose_or_apply(
        account=account,
        action="send_test_rcs",
        request={"to": numbers, "content": identity},
        current=None,
        summary={"sends_test_to": numbers, **_summary(identity, built)},
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
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Sends a rich RCS message to a tag or a segment. Two steps.

    Nothing is sent by the first call. It returns the message, the number of
    recipients and the send time to show the user. Only after an explicit
    yes may the second call be made. A test send (rcs_send_test) of exactly
    the same message, sender and SMS fallback must have been made first.

    Args:
        account: Account alias from accounts_list.
        sender: RCS sender registered in the account.
        content: The message, in the same format as for rcs_send_test.
        audience_kind: 'tag' or 'segment'.
        audience_value: The tag name, or the segment ID.
        test_token: Token from the applied rcs_send_test.
        sms_fallback_text: SMS text for people whose phone has no RCS.
            Without it they get nothing.
        send_at: When to send, with time zone, e.g. 2026-12-01T09:00:00+01:00.
        validate_only: True proposes, False sends.
        checksum: Checksum from the proposal. Required when sending.
    """
    identity, built = _prepare(sender, content, sms_fallback_text)
    scheduled = sending.parse_send_at(send_at)
    sending.require_test_token(account, "RCS", identity, test_token)
    audience = load_audience(account, audience_kind, audience_value, "RCS")
    if not audience.recipients:
        raise ToolError("The audience has no one with a phone number.")

    def apply() -> dict[str, Any]:
        reference = sending.new_reference("rcs")
        return sending.send_in_batches(
            [r.address for r in audience.recipients],
            reference,
            lambda batch, bulk_id: api.request(
                account,
                "POST",
                RCS_PATH,
                json=rcs_body(
                    identity["sender"], built, batch, reference, bulk_id,
                    scheduled, identity["sms_fallback_text"],
                ),
            ),
        )

    return propose_or_apply(
        account=account,
        action="send_rcs_campaign",
        request={
            "content": identity,
            "audience": [audience.kind, audience.value],
            "send_at": scheduled,
        },
        current={"audience": audience.fingerprint()},
        summary={
            **_summary(identity, built),
            **audience.summary(),
            "send_time_utc": scheduled or "immediately after confirmation",
            "cost": "Shown by reports_delivery after sending.",
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
