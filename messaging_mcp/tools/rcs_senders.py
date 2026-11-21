"""RCS senders: which exist, whether they are launched, and their test numbers."""

from typing import Any
from urllib.parse import quote

from fastmcp.exceptions import ToolError

from messaging_mcp import api, sending
from messaging_mcp.app import mcp
from messaging_mcp.confirm import propose_or_apply

_PAGE_SIZE = 20
_MAX_PAGES = 25


def _pages(account: str, path: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        answer = api.request(
            account, "GET", path, params={"page": page, "size": _PAGE_SIZE}
        ) or {}
        results.extend(answer.get("results") or [])
        total_pages = (answer.get("paging") or {}).get("totalPages", 1)
        if page + 1 >= total_pages:
            break
    return results


def _sender_path(sender: str) -> str:
    if not sender or not sender.strip():
        raise ToolError("sender is required.")
    return f"/rcs/1/senders/{quote(sender.strip(), safe='')}"


@mcp.tool
def rcs_list_senders(account: str) -> dict[str, Any]:
    """Lists the account's RCS senders with their status.

    Only a sender with status LAUNCHED reaches everyone. Before that it only
    reaches its test numbers.

    Args:
        account: Account alias from accounts_list.
    """
    return {
        "senders": [
            {
                "sender": s.get("senderName"),
                "display_name": s.get("displayName"),
                "status": s.get("senderStatus"),
                "use_case": s.get("useCase"),
                "billing": s.get("billingCategory"),
                "brand": (s.get("brand") or {}).get("brandName"),
                "updated_at": s.get("updatedAt"),
            }
            for s in _pages(account, "/rcs/1/senders")
        ]
    }


@mcp.tool
def rcs_sender_status(account: str, sender: str) -> dict[str, Any]:
    """Shows where an RCS sender is launched, per country and operator.

    Args:
        account: Account alias from accounts_list.
        sender: Sender name from rcs_list_senders.
    """
    path = _sender_path(sender)
    details = api.request(account, "GET", path) or {}
    launch = api.request(account, "GET", f"{path}/launch-status") or {}
    return {
        "sender": details.get("senderName") or sender,
        "display_name": details.get("displayName"),
        "status": details.get("senderStatus") or launch.get("senderStatus"),
        "use_case": details.get("useCase"),
        "coverage": [
            {
                "country": c.get("countryCode"),
                "operators": [
                    {
                        "operator": p.get("name"),
                        "status": p.get("status"),
                        "rejection_reason": p.get("rejectionReason"),
                    }
                    for p in c.get("providers") or []
                ],
            }
            for c in launch.get("coverage") or []
        ],
    }


@mcp.tool
def rcs_list_test_numbers(account: str, sender: str) -> dict[str, Any]:
    """Lists the phones that may receive messages from a sender before launch.

    Args:
        account: Account alias from accounts_list.
        sender: Sender name from rcs_list_senders.
    """
    return {
        "test_numbers": [
            {
                "number": t.get("testNumber"),
                "status": t.get("status"),
                "primary": t.get("primary"),
                "updated_at": t.get("updatedAt"),
            }
            for t in _pages(account, f"{_sender_path(sender)}/test-numbers")
        ]
    }


@mcp.tool
def rcs_add_test_number(
    account: str,
    sender: str,
    phone_number: str,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Adds a phone as a test number for a sender. Two steps.

    The phone's owner gets an invitation from Google and must accept it
    before the phone receives anything. Test numbers cannot be removed
    through the API.

    Args:
        account: Account alias from accounts_list.
        sender: Sender name from rcs_list_senders.
        phone_number: Phone number in international format, e.g. 46701234567.
        validate_only: True proposes, False adds the number.
        checksum: Checksum from the proposal. Required when adding.
    """
    (number,) = sending.clean_test_recipients([phone_number], "RCS")
    path = f"{_sender_path(sender)}/test-numbers"
    current = [t.get("testNumber") for t in _pages(account, path)]
    if number in [str(n or "").lstrip("+") for n in current]:
        raise ToolError(f"{number} is already a test number for this sender.")

    def apply() -> dict[str, Any]:
        answer = api.request(
            account, "POST", path, json={"testNumber": number, "primary": False}
        ) or {}
        return {"number": answer.get("testNumber"), "status": answer.get("status")}

    return propose_or_apply(
        account=account,
        action="add_rcs_test_number",
        request={"sender": sender.strip(), "number": number},
        current={"test_numbers": sorted(str(n) for n in current)},
        summary={
            "sender": sender.strip(),
            "adds_test_number": number,
            "current_test_numbers": current,
            "note": "The phone's owner must accept an invitation before it works.",
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )
