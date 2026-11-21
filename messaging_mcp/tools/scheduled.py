"""Looks at and stops scheduled sends."""

from typing import Any

from fastmcp.exceptions import ToolError

from messaging_mcp import api, sending
from messaging_mcp.app import mcp
from messaging_mcp.confirm import propose_or_apply


def _statuses(answer: Any) -> list[dict[str, Any]]:
    if isinstance(answer, dict) and isinstance(answer.get("bulks"), list):
        return [
            {"bulk_id": b.get("bulkId"), "status": b.get("status")}
            for b in answer["bulks"]
        ]
    if isinstance(answer, dict):
        return [{"bulk_id": answer.get("bulkId"), "status": answer.get("status")}]
    return []


@mcp.tool
def scheduled_get_status(
    account: str, channel: str, bulk_id: str
) -> dict[str, Any]:
    """Shows whether a scheduled send is pending, sent or cancelled.

    Args:
        account: Account alias from accounts_list.
        channel: EMAIL or SMS.
        bulk_id: A bulk ID returned by a send tool.
    """
    return {"bulks": _statuses(sending.bulk_status(account, channel, bulk_id))}


@mcp.tool
def scheduled_cancel(
    account: str,
    channel: str,
    bulk_id: str,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Cancels a scheduled send that has not gone out yet. Two steps.

    A cancelled send cannot be started again. A send in several batches has
    several bulk IDs, and each one is cancelled separately.

    Args:
        account: Account alias from accounts_list.
        channel: EMAIL or SMS.
        bulk_id: A bulk ID returned by a send tool.
        validate_only: True proposes, False cancels.
        checksum: Checksum from the proposal. Required when cancelling.
    """
    channel = (channel or "").strip().upper()
    current = _statuses(sending.bulk_status(account, channel, bulk_id))
    if not any(s.get("status") in ("PENDING", "PAUSED") for s in current):
        raise ToolError(
            f"Nothing to cancel. Current status: {current or 'unknown'}."
        )

    def apply() -> Any:
        answer = api.request(
            account,
            "PUT",
            sending.BULK_STATUS_PATHS[channel],
            params={"bulkId": bulk_id},
            json={"status": "CANCELED"},
        )
        return {"bulks": _statuses(answer)}

    return propose_or_apply(
        account=account,
        action=f"cancel_scheduled_{channel.lower()}",
        request={"bulk_id": bulk_id},
        current=current,
        summary={"bulk_id": bulk_id, "current": current, "new_status": "CANCELED"},
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )
