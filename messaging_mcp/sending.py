"""Shared rules for every send: test first, schedule, batch, confirm."""

import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
from typing import Any, Callable

from fastmcp.exceptions import ToolError

from messaging_mcp import access, api

BATCH_SIZE = 500
MAX_TEST_RECIPIENTS = 5
MAX_SCHEDULE_DAYS = 30
_FALLBACK_KEY = secrets.token_bytes(32)
_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE_PATTERN = re.compile(r"\d{7,15}")
BULK_STATUS_PATHS = {
    "EMAIL": "/email/1/bulks/status",
    "SMS": "/sms/1/bulks/status",
}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _key() -> bytes:
    configured = os.environ.get(access.SIGNING_KEY_ENV_VAR)
    return configured.encode("utf-8") if configured else _FALLBACK_KEY


def test_token(account: str, channel: str, content: Any) -> str:
    """Proves that exactly this content was test-sent in this account."""
    message = json.dumps([account, channel, content], sort_keys=True, default=str)
    return hmac.new(_key(), message.encode("utf-8"), hashlib.sha256).hexdigest()[
        :32
    ]


def require_test_token(
    account: str, channel: str, content: Any, token: str | None
) -> None:
    expected = test_token(account, channel, content)
    if not token or not hmac.compare_digest(str(token), expected):
        raise ToolError(
            "A test send of exactly this content is required first. Run the "
            "test tool, let the user check the result, and pass its "
            "test_token. A token stops working when the content, sender or "
            "template changes."
        )


def new_reference(prefix: str) -> str:
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(3)}"


def parse_send_at(send_at: str | None) -> str | None:
    """Validates a scheduled time and returns it in UTC, or None for now."""
    if send_at is None or not str(send_at).strip():
        return None
    try:
        parsed = datetime.datetime.fromisoformat(
            str(send_at).strip().replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ToolError(
            "send_at must be a date and time such as 2026-12-01T09:00:00+01:00."
        ) from exc
    if parsed.tzinfo is None:
        raise ToolError(
            "send_at needs a time zone offset, e.g. 2026-12-01T09:00:00+01:00."
        )
    now = _now()
    if parsed <= now + datetime.timedelta(minutes=2):
        raise ToolError("send_at must be at least a few minutes in the future.")
    if parsed > now + datetime.timedelta(days=MAX_SCHEDULE_DAYS):
        raise ToolError(
            f"send_at can be at most {MAX_SCHEDULE_DAYS} days in the future."
        )
    utc = parsed.astimezone(datetime.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def clean_test_recipients(values: list[str], channel: str) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if channel == "EMAIL":
            text = text.lower()
            valid = bool(_EMAIL_PATTERN.fullmatch(text))
        else:
            text = re.sub(r"[\s\-()]", "", text).lstrip("+")
            valid = bool(_PHONE_PATTERN.fullmatch(text))
        if not valid:
            raise ToolError(f"'{value}' is not a valid test recipient.")
        if text not in cleaned:
            cleaned.append(text)
    if not 1 <= len(cleaned) <= MAX_TEST_RECIPIENTS:
        raise ToolError(
            f"A test goes to between 1 and {MAX_TEST_RECIPIENTS} recipients."
        )
    return cleaned


def send_in_batches(
    recipients: list[Any],
    reference: str,
    send_batch: Callable[[list[Any], str], Any],
) -> dict[str, Any]:
    """Sends batch by batch and stops at the first failure.

    When a later batch fails, the error names the batches Infobip already
    accepted, so they can be checked or cancelled.
    """
    bulk_ids: list[str] = []
    accepted = 0
    for start in range(0, len(recipients), BATCH_SIZE):
        batch = recipients[start : start + BATCH_SIZE]
        bulk_id = f"{reference}-{start // BATCH_SIZE + 1}"
        try:
            send_batch(batch, bulk_id)
        except ToolError as exc:
            raise ToolError(
                f"Stopped after {accepted} of {len(recipients)} recipients. "
                f"Accepted bulk IDs: {bulk_ids or 'none'}. Reference: "
                f"{reference}. Error: {exc}"
            ) from exc
        bulk_ids.append(bulk_id)
        accepted += len(batch)
    return {
        "campaign_reference_id": reference,
        "bulk_ids": bulk_ids,
        "recipients_accepted": accepted,
    }


def schedule_options(bulk_id: str, send_at: str | None) -> dict[str, Any]:
    schedule: dict[str, Any] = {"bulkId": bulk_id}
    if send_at:
        schedule["sendAt"] = send_at
    return {"schedule": schedule}


def bulk_status(account: str, channel: str, bulk_id: str) -> Any:
    channel = (channel or "").strip().upper()
    if channel not in BULK_STATUS_PATHS:
        raise ToolError("Bulk status is available for EMAIL and SMS.")
    return api.request(
        account, "GET", BULK_STATUS_PATHS[channel], params={"bulkId": bulk_id}
    )
