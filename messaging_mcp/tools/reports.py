"""Delivery results per send, counted from Infobip's message logs."""

from collections import Counter
from typing import Any

from fastmcp.exceptions import ToolError

from messaging_mcp import api
from messaging_mcp.app import mcp

_LOG_PATHS = {"EMAIL": "/email/4/logs", "SMS": "/sms/3/logs"}
_PAGE_SIZE = 1000
_MAX_PAGES = 50
_NOT_IN_LOGS = (
    "Opens, clicks and unsubscribes are not in the message logs. Infobip only "
    "delivers them through its Metrics API, which answers to a callback "
    "address. That part is not built yet. Until then they are shown in the "
    "Infobip web interface under Analyze."
)


@mcp.tool
def reports_delivery(
    account: str,
    channel: str,
    campaign_reference_id: str | None = None,
    bulk_id: str | None = None,
) -> dict[str, Any]:
    """Counts delivered, pending and failed messages for one send.

    Bounces are the failed emails, listed by reason. Infobip keeps message
    logs for a limited time (about 48 hours), so run this soon after a send.

    Args:
        account: Account alias from accounts_list.
        channel: EMAIL or SMS.
        campaign_reference_id: The reference returned by a send tool. Covers
            every batch of the send.
        bulk_id: One bulk ID, as an alternative to the reference.
    """
    channel = (channel or "").strip().upper()
    if channel not in _LOG_PATHS:
        raise ToolError("Delivery reports are available for EMAIL and SMS.")
    if not (campaign_reference_id or bulk_id):
        raise ToolError("Pass campaign_reference_id or bulk_id.")
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    cost = 0.0
    currency = None
    total = 0
    cursor: str | None = None
    complete = False
    for _ in range(_MAX_PAGES):
        answer = api.request(
            account,
            "GET",
            _LOG_PATHS[channel],
            params={
                "campaignReferenceId": campaign_reference_id,
                "bulkId": bulk_id,
                "limit": _PAGE_SIZE,
                "useCursor": "true",
                "cursor": cursor,
            },
        ) or {}
        for log in answer.get("results") or []:
            total += 1
            status = log.get("status") or {}
            group = status.get("groupName") or "UNKNOWN"
            statuses[group] += 1
            if group not in ("DELIVERED", "PENDING"):
                error = log.get("error") or {}
                reasons[error.get("name") or status.get("name") or "UNKNOWN"] += 1
            price = log.get("price") or {}
            if price.get("pricePerMessage") is not None:
                cost += float(price["pricePerMessage"]) * int(
                    log.get("messageCount") or 1
                )
                currency = price.get("currency") or currency
        cursor = (answer.get("cursor") or {}).get("nextCursor")
        if not cursor:
            complete = True
            break
    delivered = statuses.get("DELIVERED", 0)
    return {
        "channel": channel,
        "messages_in_logs": total,
        "all_pages_read": complete,
        "by_status": dict(statuses),
        "delivered_share": round(delivered / total, 4) if total else None,
        "failed_by_reason": dict(reasons.most_common(15)),
        "cost": {"amount": round(cost, 4), "currency": currency},
        "note": _NOT_IN_LOGS if channel == "EMAIL" else None,
    }
