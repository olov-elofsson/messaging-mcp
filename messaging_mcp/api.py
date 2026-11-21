"""Thin client for the Infobip HTTP API."""

from typing import Any

import requests
from fastmcp.exceptions import ToolError

from messaging_mcp import access
from messaging_mcp.accounts import Account, get_account

_TIMEOUT_SECONDS = 60


def _error_text(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "")[:500]
    if isinstance(payload, dict):
        exception = (payload.get("requestError") or {}).get(
            "serviceException"
        ) or {}
        text = exception.get("text") or payload.get("description")
        if text:
            details = exception.get("validationErrors") or payload.get(
                "violations"
            )
            return f"{text} {details}" if details else str(text)
    return str(payload)[:500]


def request(
    account: str | Account,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: Any = None,
    form: dict[str, str] | None = None,
) -> Any:
    """Calls the API in one account and returns the parsed JSON answer.

    ``form`` sends multipart/form-data, which the template endpoints need.
    Every call checks first that the signed-in user is allowed.
    """
    access.require_allowed_user()
    if not isinstance(account, Account):
        account = get_account(account)
    kwargs: dict[str, Any] = {
        "headers": {
            "Authorization": f"App {account.api_key}",
            "Accept": "application/json",
        },
        "timeout": _TIMEOUT_SECONDS,
    }
    if params:
        kwargs["params"] = {k: v for k, v in params.items() if v is not None}
    if json is not None:
        kwargs["json"] = json
    if form is not None:
        kwargs["files"] = {
            key: (None, value) for key, value in form.items() if value is not None
        }
    try:
        response = requests.request(method, account.base_url + path, **kwargs)
    except requests.RequestException as exc:
        raise ToolError(
            f"Could not reach Infobip for account '{account.alias}': "
            f"{type(exc).__name__}."
        ) from exc
    if response.status_code >= 400:
        raise ToolError(
            f"Infobip answered {response.status_code} for {method} {path} "
            f"in account '{account.alias}': {_error_text(response)}"
        )
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text
