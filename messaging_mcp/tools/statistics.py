"""Statistics from Infobip's Metrics API: delivery, opens, clicks, bounces."""

import asyncio
import datetime
import os
from typing import Any

from fastmcp.exceptions import ToolError

from messaging_mcp import access, api, metrics_inbox
from messaging_mcp.app import mcp

WAIT_SECONDS_ENV_VAR = "MESSAGING_MCP_METRICS_WAIT_SECONDS"
_DEFAULT_WAIT_SECONDS = 45
_MAX_DAYS = 93
_PATH = "/metrics/1/query-aggregate-data"

METRICS = {
    "EMAIL": [
        "TOTAL_SENT_TRAFFIC_COUNT",
        "DELIVERED_TRAFFIC_COUNT",
        "TRAFFIC_DELIVERY_RATE",
        "SEEN_OPENED_TRAFFIC_COUNT",
        "SEEN_OPENED_TRAFFIC_RATE",
        "UNIQUE_CLICKS_COUNT",
        "UNIQUE_CLICK_THROUGH_RATE",
        "CLICK_TO_OPEN_RATE",
        "TOTAL_BOUNCE_COUNT",
        "HARD_BOUNCE_COUNT",
        "SOFT_BOUNCE_COUNT",
        "UNSUBSCRIBED_USER_COUNT",
        "EMAIL_COMPLAINTS_COUNT",
    ],
    "SMS": [
        "TOTAL_SENT_TRAFFIC_COUNT",
        "TOTAL_SMS_MESSAGES_COUNT",
        "DELIVERED_TRAFFIC_COUNT",
        "FAILED_TRAFFIC_COUNT",
        "TRAFFIC_DELIVERY_RATE",
        "UNIQUE_CLICKS_COUNT",
        "UNIQUE_CLICK_THROUGH_RATE",
    ],
    "RCS": [
        "TOTAL_SENT_TRAFFIC_COUNT",
        "DELIVERED_TRAFFIC_COUNT",
        "FAILED_TRAFFIC_COUNT",
        "TRAFFIC_DELIVERY_RATE",
        "SEEN_OPENED_TRAFFIC_COUNT",
        "SEEN_OPENED_TRAFFIC_RATE",
        "UNIQUE_CLICKS_COUNT",
        "UNIQUE_CLICK_THROUGH_RATE",
    ],
}
# What the user says, and the dimension Infobip groups by.
GROUPINGS = {
    "CAMPAIGN": "CAMPAIGN_REFERENCE",
    "DAY": "DAY",
    "MONTH": "MONTH",
    "SENDER": "SENDER",
    "STATUS": "STATUS_GROUP",
    "ERROR": "ERROR_CODE",
    "FLOW": "COMMUNICATION",
    "URL": "URL",
}


def _wait_seconds() -> float:
    return float(os.environ.get(WAIT_SECONDS_ENV_VAR, _DEFAULT_WAIT_SECONDS))


def _moment(value: str | None, what: str, end_of_day: bool) -> datetime.datetime:
    text = str(value or "").strip()
    try:
        if len(text) == 10:
            day = datetime.date.fromisoformat(text)
            clock = datetime.time(23, 59, 59) if end_of_day else datetime.time(0, 0)
            return datetime.datetime.combine(day, clock, datetime.timezone.utc)
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(
            f"{what} must be a date such as 2026-11-01, or a date and time "
            "with a time zone."
        ) from exc
    if parsed.tzinfo is None:
        raise ToolError(f"{what} needs a time zone when it has a time.")
    return parsed.astimezone(datetime.timezone.utc)


def _period(since: str, until: str | None) -> tuple[str, str]:
    start = _moment(since, "since", end_of_day=False)
    now = datetime.datetime.now(datetime.timezone.utc)
    end = _moment(until, "until", end_of_day=True) if until else now
    end = min(end, now)
    if end <= start:
        raise ToolError("until must be later than since, and since in the past.")
    if end - start > datetime.timedelta(days=_MAX_DAYS):
        raise ToolError(f"The period can be at most {_MAX_DAYS} days.")
    stamp = "%Y-%m-%dT%H:%M:%S.000+0000"
    return start.strftime(stamp), end.strftime(stamp)


def _callback_url(token: str) -> str:
    base = os.environ.get(access.BASE_URL_ENV_VAR, "").rstrip("/")
    if not base.startswith("https://"):
        raise ToolError(
            "Statistics need the deployed server: Infobip posts the result "
            f"to a public https address ({access.BASE_URL_ENV_VAR})."
        )
    return f"{base}{metrics_inbox.CALLBACK_PATH}/{token}"


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("response") or {}
    names = [c.get("columnName") for c in response.get("columns") or []]
    return [dict(zip(names, row)) for row in response.get("rows") or []]


@mcp.tool
async def reports_statistics(
    account: str,
    channel: str,
    since: str,
    until: str | None = None,
    group_by: str = "CAMPAIGN",
    campaign_reference_id: str | None = None,
    flow_id: int | None = None,
) -> dict[str, Any]:
    """Delivery, opens, clicks, bounces and unsubscribes for a period.

    Reads Infobip's aggregated statistics and changes nothing. Everything
    sent in the account is included, also sends made in the Infobip web
    interface and by flows. Infobip's numbers can lag some minutes behind.

    Args:
        account: Account alias from accounts_list.
        channel: EMAIL, SMS or RCS.
        since: First day, e.g. 2026-11-01. At most 93 days before `until`.
        until: Last day. Defaults to now.
        group_by: One row per CAMPAIGN (the reference returned by the send
            tools), DAY, MONTH, SENDER, STATUS, ERROR, FLOW or URL. URL shows
            clicks per link and nothing else.
        campaign_reference_id: Only this send.
        flow_id: Only this Moments flow.
    """
    access.require_allowed_user()
    channel = (channel or "").strip().upper()
    if channel not in METRICS:
        raise ToolError("channel must be EMAIL, SMS or RCS.")
    grouping = GROUPINGS.get((group_by or "").strip().upper())
    if grouping is None:
        raise ToolError("group_by must be one of: " + ", ".join(GROUPINGS) + ".")
    sent_since, sent_until = _period(since, until)
    filter_by: dict[str, Any] = {
        "sentSince": sent_since,
        "sentUntil": sent_until,
        "channelCodes": [channel],
        "directions": ["OUTBOUND"],
    }
    if campaign_reference_id:
        filter_by["campaignReferenceIds"] = [campaign_reference_id]
    if flow_id is not None:
        filter_by["communicationIds"] = [int(flow_id)]
    metrics = ["UNIQUE_URL_CLICK_COUNT"] if grouping == "URL" else METRICS[channel]

    token, future = metrics_inbox.open_slot()
    try:
        started = await asyncio.to_thread(
            api.request,
            account,
            "POST",
            _PATH,
            json={
                "callbackUrl": _callback_url(token),
                "request": {
                    "filterBy": filter_by,
                    "aggregateBy": [grouping],
                    "metrics": metrics,
                },
            },
        )
        request_id = (started or {}).get("requestId")
        try:
            payload = await asyncio.wait_for(future, timeout=_wait_seconds())
        except asyncio.TimeoutError:
            status = await asyncio.to_thread(
                api.request, account, "GET", f"/metrics/1/requests/{request_id}"
            )
            raise ToolError(
                "Infobip did not answer in time. Its status for the query is "
                f"{(status or {}).get('status')} "
                f"{(status or {}).get('failureMessage') or ''}. Try again, or "
                "with a shorter period."
            )
    finally:
        metrics_inbox.close_slot(token)
    if payload.get("status") != "SUCCESS":
        raise ToolError(
            f"Infobip could not compute the statistics: "
            f"{payload.get('failureMessage') or payload.get('status')}"
        )
    rows = _rows(payload)
    return {
        "account": account,
        "channel": channel,
        "period_utc": {"since": sent_since, "until": sent_until},
        "grouped_by": group_by.strip().upper(),
        "rows": rows,
        "row_count": len(rows),
        "note": (
            None
            if rows
            else "No traffic matched. Statistics can lag some minutes after a send."
        ),
    }
