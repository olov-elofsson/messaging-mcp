"""Propose-then-confirm for everything that changes or sends something.

The first call only reads the current state and describes what would happen.
The second call must carry the checksum from the first one and is refused
when the request or the current state has changed in between.
"""

import hashlib
import json
from typing import Any, Callable

from fastmcp.exceptions import ToolError


def compute_checksum(
    account: str, action: str, request: Any, current: Any
) -> str:
    payload = json.dumps(
        [account, action, request, current],
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def propose_or_apply(
    *,
    account: str,
    action: str,
    request: Any,
    current: Any,
    summary: dict[str, Any],
    validate_only: bool,
    checksum: str | None,
    apply: Callable[[], Any],
) -> dict[str, Any]:
    expected = compute_checksum(account, action, request, current)
    if validate_only:
        return {
            "status": "PROPOSED",
            "nothing_has_been_changed_or_sent": True,
            "account": account,
            "action": action,
            "summary": summary,
            "checksum": expected,
            "next_step": (
                "Show this summary to the user. Only after an explicit yes, "
                "call again with validate_only=False and this checksum."
            ),
        }
    if not checksum:
        raise ToolError(
            "A checksum is required. Call first with validate_only=True."
        )
    if checksum != expected:
        raise ToolError(
            "The checksum does not match. The request or the current state "
            "has changed since the proposal. Call again with "
            "validate_only=True and show the new proposal to the user."
        )
    result = apply()
    applied: dict[str, Any] = {
        "status": "APPLIED",
        "account": account,
        "action": action,
        "summary": summary,
    }
    if result is not None:
        applied["result"] = result
    return applied
