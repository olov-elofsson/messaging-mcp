"""How an RCS campaign is delivered: validity, a send window, speed, tracking.

Infobip reads the send window in UTC. The tools take it in Swedish time
(Europe/Stockholm) and convert it for the day the campaign goes out.
"""

import datetime
import re
from typing import Any
from zoneinfo import ZoneInfo

from fastmcp.exceptions import ToolError

LOCAL_ZONE = ZoneInfo("Europe/Stockholm")
MAX_VALIDITY_HOURS = 48
_DAYS = (
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
)
_CLOCK = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")


def _clock(value: Any, what: str) -> tuple[int, int]:
    match = _CLOCK.fullmatch(str(value or "").strip())
    if not match:
        raise ToolError(f"{what} must be a time such as 09:00.")
    return int(match.group(1)), int(match.group(2))


def _to_utc(hour: int, minute: int, day: datetime.date) -> tuple[int, int, int]:
    """Returns (hour, minute, day shift) in UTC for a Swedish clock time."""
    local = datetime.datetime.combine(day, datetime.time(hour, minute), LOCAL_ZONE)
    utc = local.astimezone(datetime.timezone.utc)
    return utc.hour, utc.minute, (utc.date() - day).days


def send_window(window: dict[str, Any] | None, send_day: datetime.date) -> dict[str, Any] | None:
    """Builds Infobip's deliveryTimeWindow from Swedish times, or None."""
    if not window:
        return None
    days = [str(d).strip().upper() for d in window.get("days") or _DAYS[:5]]
    unknown = [d for d in days if d not in _DAYS]
    if unknown or not days:
        raise ToolError("days must be weekday names such as MONDAY, in English.")
    start = _clock(window.get("from"), "from")
    end = _clock(window.get("to"), "to")
    if (end[0] * 60 + end[1]) - (start[0] * 60 + start[1]) < 60:
        raise ToolError("The send window must be at least one hour long.")
    from_utc = _to_utc(*start, send_day)
    to_utc = _to_utc(*end, send_day)
    if from_utc[2] or to_utc[2]:
        raise ToolError(
            "The send window crosses midnight in UTC, which Infobip cannot handle. "
            "Start it at 02:00 or later and end it before 23:00."
        )
    return {
        "days": days,
        "from": {"hour": from_utc[0], "minute": from_utc[1]},
        "to": {"hour": to_utc[0], "minute": to_utc[1]},
    }


def message_options(
    validity_hours: int | None, window: dict[str, Any] | None, send_day: datetime.date
) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if validity_hours is not None:
        if not 1 <= int(validity_hours) <= MAX_VALIDITY_HOURS:
            raise ToolError(
                f"validity_hours must be between 1 and {MAX_VALIDITY_HOURS}."
            )
        options["validityPeriod"] = {"amount": int(validity_hours), "timeUnit": "HOURS"}
    built_window = send_window(window, send_day)
    if built_window:
        options["deliveryTimeWindow"] = built_window
    return options


def request_options(max_per_hour: int | None, track_clicks: bool) -> dict[str, Any]:
    """Options that go next to the schedule in the request."""
    options: dict[str, Any] = {}
    if max_per_hour is not None:
        if int(max_per_hour) < 1:
            raise ToolError("max_per_hour must be at least 1.")
        options["sendingSpeedLimit"] = {"amount": int(max_per_hour), "timeUnit": "HOUR"}
    tracking = (
        {"shortenUrl": True, "trackClicks": True} if track_clicks else None
    )
    return {"speed": options, "tracking": tracking}


def describe(
    validity_hours: int | None,
    window: dict[str, Any] | None,
    max_per_hour: int | None,
    track_clicks: bool,
) -> dict[str, Any]:
    return {
        "valid_for": f"{validity_hours} hours" if validity_hours else "Infobip default",
        "send_window_swedish_time": (
            f"{', '.join(window.get('days') or _DAYS[:5])} {window.get('from')}–{window.get('to')}"
            if window
            else "any time"
        ),
        "max_per_hour": max_per_hour or "no limit",
        "click_tracking": "links shortened and clicks counted" if track_clicks else "off",
    }
