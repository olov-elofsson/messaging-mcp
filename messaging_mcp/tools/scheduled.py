"""Looks at, moves and stops scheduled sends."""

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


_BULK_PATHS = {"EMAIL": "/email/1/bulks", "SMS": "/sms/1/bulks"}


@mcp.tool
def scheduled_reschedule(
    account: str,
    channel: str,
    bulk_id: str,
    send_at: str,
    validate_only: bool = True,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Moves a scheduled send to another time. Two steps: propose, confirm.

    Only a send that has not gone out yet can be moved. A send in several
    batches has several bulk IDs, and each one is moved separately.

    Args:
        account: Account alias from accounts_list.
        channel: EMAIL or SMS.
        bulk_id: A bulk ID returned by a send tool.
        send_at: The new time, with time zone, e.g. 2026-12-01T09:00:00+01:00.
        validate_only: True proposes, False moves the send.
        checksum: Checksum from the proposal. Required when moving.
    """
    channel = (channel or "").strip().upper()
    if channel not in _BULK_PATHS:
        raise ToolError("Scheduled sends can be moved for EMAIL and SMS.")
    new_time = sending.parse_send_at(send_at)
    if new_time is None:
        raise ToolError("send_at is required.")
    status = _statuses(sending.bulk_status(account, channel, bulk_id))
    if not any(s.get("status") in ("PENDING", "PAUSED") for s in status):
        raise ToolError(f"Nothing to move. Current status: {status or 'unknown'}.")
    planned = api.request(
        account, "GET", _BULK_PATHS[channel], params={"bulkId": bulk_id}
    )
    current_time = (planned or {}).get("sendAt")
    if current_time is None and (planned or {}).get("bulks"):
        current_time = [b.get("sendAt") for b in planned["bulks"]]

    def apply() -> Any:
        api.request(
            account,
            "PUT",
            _BULK_PATHS[channel],
            params={"bulkId": bulk_id},
            json={"sendAt": new_time},
        )
        return {"bulk_id": bulk_id, "send_time_utc": new_time}

    return propose_or_apply(
        account=account,
        action=f"reschedule_{channel.lower()}",
        request={"bulk_id": bulk_id, "send_at": new_time},
        current={"status": status, "send_at": current_time},
        summary={
            "bulk_id": bulk_id,
            "current_send_time": current_time,
            "new_send_time_utc": new_time,
            "current_status": status,
        },
        validate_only=validate_only,
        checksum=checksum,
        apply=apply,
    )
