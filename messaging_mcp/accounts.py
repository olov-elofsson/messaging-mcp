"""The Infobip accounts the server can act in.

Every account has an alias, its own API base URL and its own API key. They
come from one JSON secret, so no account name or key is in the code:

    {"alias": {"base_url": "xxxxx.api.infobip.com", "api_key": "..."}}
"""

import json
import os
import re
from dataclasses import dataclass

from fastmcp.exceptions import ToolError

ACCOUNTS_ENV_VAR = "MESSAGING_MCP_ACCOUNTS"
_ALIAS_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
# Keys are only ever sent to Infobip's own API hosts.
_HOST_PATTERN = re.compile(r"[a-z0-9-]+\.api\.infobip\.com|api\.infobip\.com")


@dataclass(frozen=True)
class Account:
    alias: str
    base_url: str
    api_key: str


def normalise_host(value: str) -> str:
    host = value.strip().lower()
    host = re.sub(r"^https?://", "", host).rstrip("/")
    if not _HOST_PATTERN.fullmatch(host):
        raise ToolError(
            f"'{value}' is not an Infobip API base URL. It looks like "
            "xxxxx.api.infobip.com."
        )
    return host


def load_accounts() -> dict[str, Account]:
    raw = os.environ.get(ACCOUNTS_ENV_VAR, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ToolError(f"{ACCOUNTS_ENV_VAR} is not valid JSON.") from exc
    accounts: dict[str, Account] = {}
    for alias, entry in data.items():
        if not _ALIAS_PATTERN.fullmatch(alias):
            raise ToolError(f"Account alias '{alias}' is not valid.")
        api_key = str(entry.get("api_key") or "").strip()
        if not api_key:
            raise ToolError(f"Account '{alias}' has no API key.")
        accounts[alias] = Account(
            alias=alias,
            base_url="https://" + normalise_host(str(entry.get("base_url"))),
            api_key=api_key,
        )
    return accounts


def get_account(alias: str) -> Account:
    accounts = load_accounts()
    account = accounts.get((alias or "").strip().lower())
    if account is None:
        known = ", ".join(sorted(accounts)) or "none configured"
        raise ToolError(
            f"Unknown account '{alias}'. Available accounts: {known}."
        )
    return account
