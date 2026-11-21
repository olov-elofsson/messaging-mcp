"""SMS and RCS text sends: preview, test first, then a tag or a segment."""

from typing import Any

from fastmcp.exceptions import ToolError

from messaging_mcp import api, sending
from messaging_mcp.app import mcp
from messaging_mcp.audience import load_audience
from messaging_mcp.confirm import propose_or_apply

_SMS_PATH = "/sms/3/messages"
_RCS_PATH = "/messages-api/1/messages"
_NO_PRICE = (
    "Infobip has no price lookup in advance. The price per message is shown "
    "by reports_delivery after sending."
)


def _channel(channel: str) -> str:
    value = (channel or "").strip().upper()
    if value not in ("SMS", "RCS"):
        raise ToolError("channel must be SMS or RCS.")
    return value


def _preview(account: str, channel: str, text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise ToolError("text is required.")
    result: dict[str, Any] = {"channel": channel, "text": text, "characters": len(text)}
    if channel == "SMS":
        answer = api.request(account, "POST", "/sms/1/preview", json={"text": text})
        previews = (answer or {}).get("previews") or [{}]
        result["sms_parts_per_message"] = int(previews[0].get("messageCount") or 1)
        result["characters_left_in_last_part"] = previews[0].get(
            "charactersRemaining"
        )
        result["text_as_delivered"] = previews[0].get("textPreview", text)
    return result


def _body(
    channel: str,
    sender: str,
    text: str,
    numbers: list[str],
    reference: str,
    bulk_id: str,
    send_at: str | None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "sender": sender,
        "destinations": [{"to": number} for number in numbers],
        "options": {"campaignReferenceId": reference},
    }
    if channel == "SMS":
        message["content"] = {"text": text}
    else:
        message["channel"] = "RCS"
        message["content"] = {"body": {"type": "TEXT", "text": text}}
    return {
        "messages": [message],
        "options": sending.schedule_options(bulk_id, send_at),
    }


def _send(account: str, channel: str, body: dict[str, Any]) -> Any:
    path = _SMS_PATH if channel == "SMS" else _RCS_PATH
    return api.request(account, "POST", path, json=body)


def _content(channel: str, sender: str, text: str) -> dict[str, Any]:
    if not sender or not sender.strip():
        raise ToolError("sender is required.")
    return {"channel": channel, "sender": sender.strip(), "text": text}


@mcp.tool
def text_preview(account: str, channel: str, text: str) -> dict[str, Any]:
    """Shows how an SMS or RCS text is delivered. Sends nothing.

    For SMS the answer says how many SMS parts each message is billed as.

    Args:
        account: Account alias from accounts_list.
        channel: SMS or RCS.
        text: The message text.
    """
    return _preview(account, _channel(channel), text)


@mcp.tool
def text_send_test(
    account: str,
    channel: str,
    sender: str,
    text: str,
    to: list[str],
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Sends a test SMS or RCS message to a few numbers. Two steps.

    The applied result contains a test_token that text_send_campaign needs.

    Args:
        account: Account alias from accounts_list.
        channel: SMS or RCS.
        sender: Sender name or number registered in the account.
        text: The message text.
        to: One to five phone numbers in international format, e.g.
            46701234567. Normally the user's own.
        validate_only: True proposes, False sends the test.
        checksum: Checksum from the proposal. Required when sending.
    """
    channel = _channel(channel)
    numbers = sending.clean_test_recipients(to, channel)
    content = _content(channel, sender, text)
    preview = _preview(account, channel, text)

    def apply() -> dict[str, Any]:
        reference = sending.new_reference(f"test-{channel.lower()}")
        _send(
            account,
            channel,
            _body(channel, content["sender"], text, numbers, reference, reference, None),
        )
        return {
            "campaign_reference_id": reference,
            "test_token": sending.test_token(account, channel, content),
        }

    return propose_or_apply(
        account=account,
        action=f"send_test_{channel.lower()}",
        request={"to": numbers, "content": content},
        current=None,
        summary={"sends_test_to": numbers, "sender": content["sender"], **preview},
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )


@mcp.tool
def text_send_campaign(
    account: str,
    channel: str,
    sender: str,
    text: str,
    audience_kind: str,
    audience_value: str,
    test_token: str,
    send_at: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Sends an SMS or RCS text to a tag or a segment. Two steps.

    Nothing is sent by the first call. It returns the text as delivered, the
    number of recipients and, for SMS, the number of billed SMS parts. Show
    all of it to the user. Only after an explicit yes may the second call be
    made. A test send (text_send_test) of the same text and sender must have
    been made first.

    Args:
        account: Account alias from accounts_list.
        channel: SMS or RCS.
        sender: Sender name or number registered in the account.
        text: The message text. Everyone gets the same text.
        audience_kind: 'tag' or 'segment'.
        audience_value: The tag name, or the segment ID.
        test_token: Token from the applied text_send_test.
        send_at: When to send, with time zone, e.g. 2026-12-01T09:00:00+01:00.
            Leave out to send as soon as the user confirms.
        validate_only: True proposes, False sends.
        checksum: Checksum from the proposal. Required when sending.
    """
    channel = _channel(channel)
    scheduled = sending.parse_send_at(send_at)
    content = _content(channel, sender, text)
    sending.require_test_token(account, channel, content, test_token)
    preview = _preview(account, channel, text)
    audience = load_audience(account, audience_kind, audience_value, channel)
    if not audience.recipients:
        raise ToolError("The audience has no one with a phone number.")
    summary: dict[str, Any] = {
        "sender": content["sender"],
        **preview,
        **audience.summary(),
        "send_time_utc": scheduled or "immediately after confirmation",
        "cost": _NO_PRICE,
    }
    if channel == "SMS":
        summary["sms_parts_in_total"] = preview["sms_parts_per_message"] * len(
            audience.recipients
        )

    def apply() -> dict[str, Any]:
        reference = sending.new_reference(channel.lower())
        return sending.send_in_batches(
            [r.address for r in audience.recipients],
            reference,
            lambda batch, bulk_id: _send(
                account,
                channel,
                _body(
                    channel, content["sender"], text, batch, reference, bulk_id, scheduled
                ),
            ),
        )

    return propose_or_apply(
        account=account,
        action=f"send_{channel.lower()}_campaign",
        request={
            "content": content,
            "audience": [audience.kind, audience.value],
            "send_at": scheduled,
        },
        current={"audience": audience.fingerprint()},
        summary=summary,
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )
