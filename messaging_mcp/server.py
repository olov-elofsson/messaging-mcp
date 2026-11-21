"""Entry point for the messaging MCP server."""

import os

from fastmcp import FastMCP

from messaging_mcp import access
from messaging_mcp.app import mcp

# Importing a tool module registers its tools.
from messaging_mcp.tools import (  # noqa: F401
    account_tools,
    audience_tools,
    email_send,
    flows,
    reports,
    scheduled,
    templates,
    text_send,
)


def _ensure_subscriptions_listen(server: FastMCP) -> None:
    """Registers the subscription stream that some MCP clients open."""
    from mcp import types as mcp_types
    from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler

    low_level_server = server._mcp_server
    if "subscriptions/listen" in low_level_server._request_handlers:
        return
    low_level_server.add_request_handler(
        "subscriptions/listen",
        mcp_types.SubscriptionsListenRequestParams,
        ListenHandler(InMemorySubscriptionBus()),
    )


def build_server() -> FastMCP:
    auth = access.build_auth()
    server = (
        FastMCP("Messaging Server", auth=auth)
        if auth is not None
        else FastMCP("Messaging Server")
    )
    _ensure_subscriptions_listen(server)
    server.mount(mcp)
    return server


def run_server() -> None:
    server = build_server()
    if server.auth is not None:
        server.run(
            transport="streamable-http",
            port=int(os.environ.get("PORT", "8080")),
            host="0.0.0.0",
            uvicorn_config={"access_log": False},
        )
    else:
        server.run()


if __name__ == "__main__":
    run_server()
