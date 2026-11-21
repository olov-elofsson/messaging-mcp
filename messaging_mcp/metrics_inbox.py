"""Receives the answers of Infobip's Metrics API.

The Metrics API does not answer a query directly. It posts the result to an
address given in the query. Each query gets its own unguessable address
under /metrics-callback/, and the tool that asked waits here for the answer.
Only aggregated numbers pass through, and nothing is stored.
"""

import asyncio
import json
import secrets
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

CALLBACK_PATH = "/metrics-callback"
_MAX_BODY_BYTES = 2_000_000
_pending: dict[str, "asyncio.Future[Any]"] = {}


def open_slot() -> tuple[str, "asyncio.Future[Any]"]:
    token = secrets.token_urlsafe(32)
    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    _pending[token] = future
    return token, future


def close_slot(token: str) -> None:
    _pending.pop(token, None)


async def receive(request: Request) -> Response:
    """Accepts one answer for a query that is still waiting."""
    future = _pending.get(request.path_params.get("token", ""))
    if future is None or future.done():
        return JSONResponse({"error": "unknown"}, status_code=404)
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        return JSONResponse({"error": "too large"}, status_code=413)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "not json"}, status_code=400)
    future.set_result(payload)
    return JSONResponse({"status": "received"})
