"""RCS templates that operators review before they can be sent.

Some operators only accept RCS messages built from an approved template. A
template is created for a sender, registered with one or more operators,
and sent with rcs_send_campaign and content {"type": "TEMPLATE", ...}.
"""

from typing import Any
from urllib.parse import quote

from fastmcp.exceptions import ToolError

from messaging_mcp import api, rcs_content
from messaging_mcp.app import mcp
from messaging_mcp.confirm import propose_or_apply
from messaging_mcp.tools.rcs_senders import _sender_path


def _template_path(sender: str, name: str) -> str:
    if not name or not name.strip():
        raise ToolError("Template name is required.")
    return f"{_sender_path(sender)}/templates/{quote(name.strip(), safe='')}"


def _list(account: str, sender: str, status: str | None = None) -> list[dict[str, Any]]:
    answer = api.request(
        account,
        "GET",
        f"{_sender_path(sender)}/templates",
        params={"status": status.upper() if status else None},
    )
    if isinstance(answer, dict):
        return answer.get("results") or []
    return answer or []


def _providers(providers: list[str]) -> list[str]:
    cleaned = list(dict.fromkeys(str(p).strip() for p in providers or [] if str(p).strip()))
    if not cleaned:
        raise ToolError(
            "Name at least one operator. rcs_templates_list_operators shows them."
        )
    return cleaned


@mcp.tool
def rcs_templates_list(
    account: str, sender: str, status: str | None = None
) -> dict[str, Any]:
    """Lists a sender's RCS templates and whether operators approved them.

    Args:
        account: Account alias from accounts_list.
        sender: Sender name from rcs_list_senders.
        status: Only templates with this status, e.g. APPROVED or PENDING.
    """
    return {
        "templates": [
            {
                "name": t.get("name"),
                "type": t.get("type"),
                "status": t.get("status"),
                "updated_at": t.get("lastUpdate"),
            }
            for t in _list(account, sender, status)
        ]
    }


@mcp.tool
def rcs_templates_get(account: str, sender: str, name: str) -> dict[str, Any]:
    """Shows one RCS template and its approval per country and operator.

    Args:
        account: Account alias from accounts_list.
        sender: Sender name from rcs_list_senders.
        name: Template name from rcs_templates_list.
    """
    path = _template_path(sender, name)
    template = api.request(account, "GET", path) or {}
    status = api.request(account, "GET", f"{path}/status") or {}
    content = template.get("content") or {}
    return {
        "name": template.get("name") or name,
        "type": template.get("type"),
        "status": status.get("status") or template.get("status"),
        "content": rcs_content.describe(content) if content.get("type") else content,
        "approval": [
            {
                "country": c.get("countryCode"),
                "operators": [
                    {"operator": p.get("providerName"), "status": p.get("status")}
                    for p in c.get("providers") or []
                ],
            }
            for c in status.get("coverage") or []
        ],
    }


@mcp.tool
def rcs_templates_list_operators(
    account: str, sender: str, country_code: str | None = None
) -> dict[str, Any]:
    """Lists the operators a sender can register RCS templates with.

    Args:
        account: Account alias from accounts_list.
        sender: Sender name from rcs_list_senders.
        country_code: Only operators in this country, e.g. SE.
    """
    answer = api.request(
        account,
        "GET",
        "/rcs/1/templates/template-providers",
        params={"sender": sender, "countryCode": country_code},
    )
    return {"operators": (answer or {}).get("providers") or []}


@mcp.tool
def rcs_templates_create(
    account: str,
    sender: str,
    name: str,
    content: dict[str, Any],
    operators: list[str],
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Creates an RCS template and sends it to operators for review. Two steps.

    A template sends nothing by itself. Operators review it before it can be
    used, which can take days.

    Args:
        account: Account alias from accounts_list.
        sender: Sender name from rcs_list_senders.
        name: Template name, at most 20 characters, not already used.
        content: The template. Same format as for rcs_send_test, but the
            type is TEXT, TEXT_WITH_PDF, CARD or CAROUSEL. TEXT_WITH_PDF is
            {"type": "TEXT_WITH_PDF", "text": "...", "pdf_url": "https://...",
            "pdf_first": false}.
        operators: Operators to register with, from rcs_templates_list_operators.
        validate_only: True proposes, False creates.
        checksum: Checksum from the proposal. Required when creating.
    """
    name = str(rcs_content._text(name, "Template name", rcs_content.MAX_TEMPLATE_NAME))
    built = rcs_content.build_template(content)
    providers = _providers(operators)
    existing = [str(t.get("name") or "").lower() for t in _list(account, sender)]
    if name.lower() in existing:
        raise ToolError(
            f"A template named '{name}' already exists. Use rcs_templates_update."
        )

    def apply() -> dict[str, Any]:
        answer = api.request(
            account,
            "POST",
            f"{_sender_path(sender)}/templates",
            json={"templateName": name, "providers": providers, "content": built},
        ) or {}
        return {"name": answer.get("name") or name, "status": answer.get("status")}

    return propose_or_apply(
        account=account,
        action="create_rcs_template",
        request={"sender": sender.strip(), "name": name, "content": built,
                 "operators": providers},
        current=None,
        summary={
            "sender": sender.strip(),
            "creates_template": name,
            "content": rcs_content.describe(built),
            "sent_for_review_to": providers,
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )


@mcp.tool
def rcs_templates_update(
    account: str,
    sender: str,
    name: str,
    content: dict[str, Any],
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Replaces the content of an RCS template. Two steps.

    The proposal shows the current and the new content. A changed template
    may have to be approved by the operators again before it can be sent.

    Args:
        account: Account alias from accounts_list.
        sender: Sender name from rcs_list_senders.
        name: Template name from rcs_templates_list.
        content: The new template, in the same format as for
            rcs_templates_create.
        validate_only: True proposes, False changes.
        checksum: Checksum from the proposal. Required when changing.
    """
    path = _template_path(sender, name)
    built = rcs_content.build_template(content)
    current = api.request(account, "GET", path) or {}
    current_content = current.get("content") or {}
    if current_content == built:
        raise ToolError("Nothing to change: the template already has this content.")

    def apply() -> dict[str, Any]:
        answer = api.request(account, "PUT", path, json=built) or {}
        return {"name": name.strip(), "status": answer.get("status")}

    return propose_or_apply(
        account=account,
        action="update_rcs_template",
        request={"sender": sender.strip(), "name": name.strip(), "content": built},
        current={"content": current_content, "status": current.get("status")},
        summary={
            "template": name.strip(),
            "current_status": current.get("status"),
            "current_content": rcs_content.describe(current_content)
            if current_content.get("type")
            else current_content,
            "new_content": rcs_content.describe(built),
            "note": "The operators may have to approve the template again.",
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )


@mcp.tool
def rcs_templates_register(
    account: str,
    sender: str,
    name: str,
    operators: list[str],
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Sends an existing RCS template for review to more operators. Two steps.

    Args:
        account: Account alias from accounts_list.
        sender: Sender name from rcs_list_senders.
        name: Template name from rcs_templates_list.
        operators: Operators from rcs_templates_list_operators.
        validate_only: True proposes, False registers.
        checksum: Checksum from the proposal. Required when registering.
    """
    path = _template_path(sender, name)
    providers = _providers(operators)
    status = api.request(account, "GET", f"{path}/status") or {}
    already = sorted(
        p.get("providerName")
        for c in status.get("coverage") or []
        for p in c.get("providers") or []
        if p.get("providerName")
    )

    def apply() -> dict[str, Any]:
        answer = api.request(
            account, "PATCH", f"{path}/register", json={"providers": providers}
        ) or {}
        return {"name": name.strip(), "status": answer.get("status")}

    return propose_or_apply(
        account=account,
        action="register_rcs_template",
        request={"sender": sender.strip(), "name": name.strip(), "operators": providers},
        current={"registered_with": already, "status": status.get("status")},
        summary={
            "template": name.strip(),
            "current_status": status.get("status"),
            "already_registered_with": already,
            "sends_for_review_to": providers,
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )
