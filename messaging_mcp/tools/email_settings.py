"""Read-only facts about email in an account: sender domains and blocked addresses."""

from typing import Any

from fastmcp.exceptions import ToolError

from messaging_mcp import api
from messaging_mcp.app import mcp

_SUPPRESSION_TYPES = ("BOUNCE", "COMPLAINT", "OVER_QUOTA")


@mcp.tool
def email_list_sender_domains(account: str) -> dict[str, Any]:
    """Lists the domains the account can send email from.

    A sender address must use one of the active domains. Use this before a
    send to suggest a valid sender instead of guessing.

    Args:
        account: Account alias from accounts_list.
    """
    answer = api.request(
        account, "GET", "/email/1/domains", params={"size": 20, "page": 0}
    )
    return {
        "domains": [
            {
                "domain": d.get("domainName"),
                "active": d.get("active"),
                "blocked": d.get("blocked"),
                "dns_verified": all(
                    r.get("verified") for r in d.get("dnsRecords") or []
                ),
                "tracks": d.get("tracking"),
            }
            for d in (answer or {}).get("results") or []
        ]
    }


@mcp.tool
def email_list_suppressions(
    account: str,
    domain: str,
    reason_type: str = "BOUNCE",
    email_address: str | None = None,
    page: int = 0,
) -> dict[str, Any]:
    """Lists addresses Infobip will not send to from a domain, and why.

    Args:
        account: Account alias from accounts_list.
        domain: A sender domain from email_list_sender_domains.
        reason_type: BOUNCE, COMPLAINT or OVER_QUOTA.
        email_address: Only this address.
        page: Zero-based page number, 100 addresses per page.
    """
    kind = (reason_type or "").strip().upper()
    if kind not in _SUPPRESSION_TYPES:
        raise ToolError("reason_type must be BOUNCE, COMPLAINT or OVER_QUOTA.")
    answer = api.request(
        account,
        "GET",
        "/email/1/suppressions",
        params={
            "domainName": domain,
            "type": kind,
            "emailAddress": email_address,
            "page": max(page, 0),
            "size": 100,
        },
    )
    results = (answer or {}).get("results") or []
    return {
        "domain": domain,
        "reason_type": kind,
        "addresses": [
            {
                "email": r.get("emailAddress"),
                "since": r.get("createdDate"),
                "reason": r.get("reason"),
            }
            for r in results
        ],
        "count_on_page": len(results),
        "page": max(page, 0),
    }
