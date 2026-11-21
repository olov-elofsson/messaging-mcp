"""Lists the configured accounts, never their keys."""

from typing import Any

from messaging_mcp import access
from messaging_mcp.accounts import load_accounts
from messaging_mcp.app import mcp


@mcp.tool
def accounts_list() -> dict[str, Any]:
    """Lists the Infobip accounts this server can act in.

    Every other tool takes one of these aliases as its `account` argument.
    Ask the user which account to use when it is not clear from the
    conversation. Never guess.
    """
    access.require_allowed_user()
    accounts = load_accounts()
    return {
        "accounts": [
            {"account": alias, "api_base_url": account.base_url}
            for alias, account in sorted(accounts.items())
        ]
    }
