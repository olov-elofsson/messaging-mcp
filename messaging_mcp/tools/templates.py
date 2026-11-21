"""Email templates (Infobip Templates API, Broadcast templates only)."""

from typing import Any

from fastmcp.exceptions import ToolError

from messaging_mcp import api
from messaging_mcp.app import mcp
from messaging_mcp.confirm import propose_or_apply

_PATH = "/email/1/templates"
# API field name for each tool argument that a template stores.
_FIELDS = {
    "name": "name",
    "html": "html",
    "subject": "subject",
    "preheader": "preheader",
    "sender": "from",
    "reply_to": "replyTo",
}
_MAX_SUBJECT = 200
_MAX_PREHEADER = 75


def _check_lengths(subject: str | None, preheader: str | None) -> None:
    if subject is not None and len(subject) > _MAX_SUBJECT:
        raise ToolError(f"The subject can be at most {_MAX_SUBJECT} characters.")
    if preheader is not None and len(preheader) > _MAX_PREHEADER:
        raise ToolError(
            f"The preheader can be at most {_MAX_PREHEADER} characters."
        )


def _html_summary(html: str) -> dict[str, Any]:
    lowered = html.lower()
    return {
        "characters": len(html),
        "has_unsubscribe_link": "unsubscribe" in lowered,
        "starts_with": html.strip()[:120],
    }


def fetch_template(account: str, template_id: int) -> dict[str, Any]:
    return api.request(account, "GET", f"{_PATH}/{int(template_id)}")


def _stored_values(template: dict[str, Any]) -> dict[str, Any]:
    return {arg: template.get(field) for arg, field in _FIELDS.items()}


@mcp.tool
def templates_list(
    account: str, page: int = 0, page_size: int = 50
) -> dict[str, Any]:
    """Lists the email templates in an account, newest information included.

    Args:
        account: Account alias from accounts_list.
        page: Zero-based page number.
        page_size: Templates per page, 1 to 100.
    """
    if not 1 <= page_size <= 100:
        raise ToolError("page_size must be between 1 and 100.")
    answer = api.request(
        account, "GET", _PATH, params={"page": max(page, 0), "size": page_size}
    )
    return {
        "templates": [
            {
                "template_id": item.get("id"),
                "name": item.get("name"),
                "created_at": item.get("createdAt"),
                "updated_at": item.get("updatedAt"),
                "preview_image_url": item.get("imagePreviewUrl"),
            }
            for item in (answer or {}).get("results", [])
        ],
        "paging": (answer or {}).get("paging"),
    }


@mcp.tool
def templates_get(
    account: str, template_id: int, include_html: bool = True
) -> dict[str, Any]:
    """Fetches one email template with sender, subject and HTML.

    Args:
        account: Account alias from accounts_list.
        template_id: Template ID from templates_list.
        include_html: Set False to leave the HTML out of the answer.
    """
    template = fetch_template(account, template_id)
    html = template.get("html") or ""
    result = {
        "template_id": template.get("id"),
        **{k: v for k, v in _stored_values(template).items() if k != "html"},
        "html_summary": _html_summary(html),
        "attachments": template.get("attachments") or [],
        "landing_page_id": template.get("landingPageId"),
        "created_at": template.get("createdAt"),
        "updated_at": template.get("updatedAt"),
        "preview_image_url": template.get("imagePreviewUrl"),
    }
    if include_html:
        result["html"] = html
    return result


@mcp.tool
def templates_create(
    account: str,
    name: str,
    html: str,
    subject: str | None = None,
    preheader: str | None = None,
    sender: str | None = None,
    reply_to: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Creates a new email template from HTML. Two steps: propose, then confirm.

    A template sends nothing by itself. Use {{placeholder}} style variables
    only if the user asked for personalisation, and include an unsubscribe
    link in marketing emails.

    Args:
        account: Account alias from accounts_list.
        name: Template name. Must not already be used in the account.
        html: Complete HTML of the email.
        subject: Default subject line, at most 200 characters.
        preheader: Preview text shown after the subject, at most 75 characters.
        sender: Default sender, e.g. 'Name <address@domain>'. The domain must
            be verified in the account.
        reply_to: Default reply-to address.
        validate_only: True proposes, False creates.
        checksum: Checksum from the proposal. Required when creating.
    """
    if not name.strip() or not html.strip():
        raise ToolError("Both name and html are required.")
    _check_lengths(subject, preheader)
    existing = _names_in_use(account)
    if name.strip().lower() in existing:
        raise ToolError(
            f"A template named '{name}' already exists in '{account}'. "
            "Choose another name, or use templates_update to change it."
        )
    values = {
        "name": name.strip(),
        "html": html,
        "subject": subject,
        "preheader": preheader,
        "sender": sender,
        "reply_to": reply_to,
    }

    def apply() -> dict[str, Any]:
        created = api.request(account, "POST", _PATH, form=_form(values))
        return {"template_id": (created or {}).get("id")}

    return propose_or_apply(
        account=account,
        action="create_email_template",
        request=values,
        current=None,
        summary={
            "creates": {k: v for k, v in values.items() if k != "html"},
            "html_summary": _html_summary(html),
            "warnings": _warnings(html),
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )


@mcp.tool
def templates_update(
    account: str,
    template_id: int,
    name: str | None = None,
    html: str | None = None,
    subject: str | None = None,
    preheader: str | None = None,
    sender: str | None = None,
    reply_to: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Changes an existing email template. Two steps: propose, then confirm.

    Only the arguments that are given are changed. The proposal shows the
    current and the new value of each. Scheduled sends that use the template
    will go out with the changed version.

    Args:
        account: Account alias from accounts_list.
        template_id: Template ID from templates_list.
        name: New name.
        html: New complete HTML. Replaces all of the old HTML.
        subject: New default subject line.
        preheader: New preview text.
        sender: New default sender.
        reply_to: New default reply-to address.
        validate_only: True proposes, False changes.
        checksum: Checksum from the proposal. Required when changing.
    """
    _check_lengths(subject, preheader)
    requested = {
        "name": name,
        "html": html,
        "subject": subject,
        "preheader": preheader,
        "sender": sender,
        "reply_to": reply_to,
    }
    template = fetch_template(account, template_id)
    stored = _stored_values(template)
    changes = {
        key: value
        for key, value in requested.items()
        if value is not None and value != stored.get(key)
    }
    if not changes:
        raise ToolError("Nothing to change: no new value differs from the template.")
    change_items = []
    for key, value in changes.items():
        if key == "html":
            change_items.append(
                {
                    "field": "html",
                    "current": _html_summary(stored.get("html") or ""),
                    "new": _html_summary(value),
                }
            )
        else:
            change_items.append(
                {"field": key, "current": stored.get(key), "new": value}
            )

    def apply() -> dict[str, Any]:
        api.request(
            account,
            "PATCH",
            f"{_PATH}/{int(template_id)}",
            form=_form(changes),
        )
        return {"template_id": int(template_id)}

    return propose_or_apply(
        account=account,
        action="update_email_template",
        request={"template_id": int(template_id), "changes": changes},
        current={"updated_at": template.get("updatedAt"), "stored": stored},
        summary={
            "template_id": int(template_id),
            "template_name": stored.get("name"),
            "changes": change_items,
            "warnings": _warnings(changes["html"]) if "html" in changes else [],
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )


@mcp.tool
def templates_copy(
    account: str,
    template_id: int,
    new_name: str,
    to_account: str | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Copies an email template under a new name. Two steps: propose, confirm.

    The original is left untouched. Attachments are not copied.

    Args:
        account: Account alias the template is read from.
        template_id: Template ID to copy.
        new_name: Name of the copy.
        to_account: Account alias to create the copy in. Defaults to the
            same account. The sender address of the original is only kept
            when the copy stays in the same account.
        validate_only: True proposes, False copies.
        checksum: Checksum from the proposal. Required when copying.
    """
    target = to_account or account
    if not new_name.strip():
        raise ToolError("new_name is required.")
    template = fetch_template(account, template_id)
    if new_name.strip().lower() in _names_in_use(target):
        raise ToolError(
            f"A template named '{new_name}' already exists in '{target}'."
        )
    values = _stored_values(template)
    values["name"] = new_name.strip()
    dropped = []
    if target != account:
        # A sender is tied to a domain verified in the source account.
        for key in ("sender", "reply_to"):
            if values.get(key):
                dropped.append(key)
            values[key] = None
    if template.get("attachments"):
        dropped.append("attachments")

    def apply() -> dict[str, Any]:
        created = api.request(target, "POST", _PATH, form=_form(values))
        return {"template_id": (created or {}).get("id"), "account": target}

    return propose_or_apply(
        account=account,
        action="copy_email_template",
        request={
            "template_id": int(template_id),
            "to_account": target,
            "new_name": values["name"],
        },
        current={"updated_at": template.get("updatedAt")},
        summary={
            "copies_from": {
                "account": account,
                "template_id": int(template_id),
                "name": template.get("name"),
            },
            "creates": {"account": target, "name": values["name"]},
            "not_copied": dropped,
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )


def _form(values: dict[str, Any]) -> dict[str, str]:
    return {
        _FIELDS[key]: str(value)
        for key, value in values.items()
        if value is not None
    }


def _warnings(html: str) -> list[str]:
    warnings = []
    if "unsubscribe" not in html.lower():
        warnings.append(
            "The HTML has no unsubscribe link. Marketing emails need one."
        )
    return warnings


def _names_in_use(account: str) -> set[str]:
    names: set[str] = set()
    page = 0
    while True:
        answer = api.request(
            account, "GET", _PATH, params={"page": page, "size": 100}
        ) or {}
        for item in answer.get("results", []):
            if item.get("name"):
                names.add(str(item["name"]).strip().lower())
        total_pages = (answer.get("paging") or {}).get("totalPages", 1)
        page += 1
        if page >= total_pages or page >= 50:
            return names
