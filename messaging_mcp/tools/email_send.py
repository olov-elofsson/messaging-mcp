"""Email sends with a template: test first, then a tag or a segment."""

import json
from typing import Any

from fastmcp.exceptions import ToolError

from messaging_mcp import api, sending
from messaging_mcp.app import mcp
from messaging_mcp.audience import Recipient, load_audience
from messaging_mcp.confirm import propose_or_apply
from messaging_mcp.tools.templates import fetch_template

_PATH = "/email/4/messages"


def _content(
    account: str,
    template_id: int,
    sender: str | None,
    subject: str | None,
) -> dict[str, Any]:
    """Resolves what would be sent. Also the basis of the test token."""
    template = fetch_template(account, template_id)
    resolved_sender = sender or template.get("from")
    resolved_subject = subject or template.get("subject")
    if not resolved_sender:
        raise ToolError(
            "The template has no sender. Pass sender, e.g. 'Name <a@domain>'."
        )
    if not resolved_subject:
        raise ToolError("The template has no subject. Pass subject.")
    return {
        "template_id": int(template_id),
        "template_name": template.get("name"),
        "template_updated_at": template.get("updatedAt"),
        "sender": resolved_sender,
        "subject": resolved_subject,
        "has_unsubscribe_link": "unsubscribe"
        in (template.get("html") or "").lower(),
    }


def _destination(recipient: Recipient) -> dict[str, Any]:
    entry: dict[str, Any] = {"destination": recipient.address}
    placeholders = {
        key: value
        for key, value in (
            ("firstName", recipient.first_name),
            ("lastName", recipient.last_name),
        )
        if value
    }
    if placeholders:
        entry["placeholders"] = json.dumps(placeholders, ensure_ascii=False)
    # One group per recipient, so nobody sees anybody else's address.
    return {"to": [entry]}


def _body(
    content: dict[str, Any],
    recipients: list[Recipient],
    reference: str,
    bulk_id: str,
    send_at: str | None,
    subject_prefix: str = "",
) -> dict[str, Any]:
    return {
        "messages": [
            {
                "sender": content["sender"],
                "destinations": [_destination(r) for r in recipients],
                "content": {
                    "templateId": str(content["template_id"]),
                    "subject": subject_prefix + content["subject"],
                },
                "options": {"campaignReferenceId": reference},
            }
        ],
        "options": sending.schedule_options(bulk_id, send_at),
    }


def _warnings(content: dict[str, Any]) -> list[str]:
    if content["has_unsubscribe_link"]:
        return []
    return ["The template has no unsubscribe link. Marketing emails need one."]


@mcp.tool
def email_send_test(
    account: str,
    template_id: int,
    to: list[str],
    sender: str | None = None,
    subject: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Sends a test email with a template to a few addresses. Two steps.

    The subject gets the prefix [TEST]. The applied result contains a
    test_token that email_send_campaign requires.

    Args:
        account: Account alias from accounts_list.
        template_id: Template ID from templates_list.
        to: One to five test addresses, normally the user's own.
        sender: Overrides the template's sender.
        subject: Overrides the template's subject.
        validate_only: True proposes, False sends the test.
        checksum: Checksum from the proposal. Required when sending.
    """
    recipients = sending.clean_test_recipients(to, "EMAIL")
    content = _content(account, template_id, sender, subject)

    def apply() -> dict[str, Any]:
        reference = sending.new_reference("test-email")
        api.request(
            account,
            "POST",
            _PATH,
            json=_body(
                content,
                [Recipient(address=a) for a in recipients],
                reference,
                reference,
                None,
                subject_prefix="[TEST] ",
            ),
        )
        return {
            "campaign_reference_id": reference,
            "test_token": sending.test_token(account, "EMAIL", content),
        }

    return propose_or_apply(
        account=account,
        action="send_test_email",
        request={"to": recipients, "content": content},
        current=None,
        summary={
            "sends_test_to": recipients,
            **{k: content[k] for k in ("template_name", "sender", "subject")},
            "warnings": _warnings(content),
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )


@mcp.tool
def email_send_campaign(
    account: str,
    template_id: int,
    audience_kind: str,
    audience_value: str,
    test_token: str,
    send_at: str | None = None,
    sender: str | None = None,
    subject: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Sends a template to everyone with a tag or in a segment. Two steps.

    Nothing is sent by the first call. It returns the number of recipients,
    sender, subject and send time to show the user. Only after an explicit
    yes may the second call be made. A test send (email_send_test) of the
    same template, sender and subject must have been made first.

    Args:
        account: Account alias from accounts_list.
        template_id: Template ID from templates_list.
        audience_kind: 'tag' or 'segment'.
        audience_value: The tag name, or the segment ID.
        test_token: Token from the applied email_send_test.
        send_at: When to send, with time zone, e.g. 2026-12-01T09:00:00+01:00.
            Leave out to send as soon as the user confirms.
        sender: Overrides the template's sender. Must match the test.
        subject: Overrides the template's subject. Must match the test.
        validate_only: True proposes, False sends.
        checksum: Checksum from the proposal. Required when sending.
    """
    scheduled = sending.parse_send_at(send_at)
    content = _content(account, template_id, sender, subject)
    sending.require_test_token(account, "EMAIL", content, test_token)
    audience = load_audience(account, audience_kind, audience_value, "EMAIL")
    if not audience.recipients:
        raise ToolError("The audience has no one with an email address.")

    def apply() -> dict[str, Any]:
        reference = sending.new_reference("email")
        return sending.send_in_batches(
            audience.recipients,
            reference,
            lambda batch, bulk_id: api.request(
                account,
                "POST",
                _PATH,
                json=_body(content, batch, reference, bulk_id, scheduled),
            ),
        )

    return propose_or_apply(
        account=account,
        action="send_email_campaign",
        request={
            "content": content,
            "audience": [audience.kind, audience.value],
            "send_at": scheduled,
        },
        current={"audience": audience.fingerprint()},
        summary={
            **{k: content[k] for k in ("template_name", "sender", "subject")},
            **audience.summary(),
            "send_time_utc": scheduled or "immediately after confirmation",
            "warnings": _warnings(content),
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )
