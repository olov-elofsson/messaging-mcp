"""A fake Infobip API for the tests. No test touches the network."""

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from messaging_mcp import access, accounts, api

ACCOUNT = "first"
OTHER = "second"
ACCOUNTS = {
    ACCOUNT: {"base_url": "aaaaa.api.infobip.com", "api_key": "key-1"},
    OTHER: {"base_url": "https://bbbbb.api.infobip.com/", "api_key": "key-2"},
}


def response(payload=None, status=200):
    fake = MagicMock()
    fake.status_code = status
    fake.content = b"" if payload is None else json.dumps(payload).encode()
    fake.text = "" if payload is None else json.dumps(payload)
    if payload is None:
        fake.json.side_effect = ValueError("no body")
    else:
        fake.json.return_value = payload
    return fake


class Call:
    def __init__(self, method, url, kwargs):
        self.method = method
        self.url = url
        self.path = "/" + url.split("/", 3)[3]
        self.host = url.split("/")[2]
        self.params = kwargs.get("params") or {}
        self.json = kwargs.get("json")
        self.form = {
            key: value[1] for key, value in (kwargs.get("files") or {}).items()
        }
        self.headers = kwargs.get("headers") or {}


class FakeApiTestCase(unittest.TestCase):
    """Routes every HTTP call to self.routes[(method, path)].

    A route is a payload, or a function taking the Call and returning a
    payload or a ready response. Unknown routes fail the test.
    """

    def setUp(self):
        self.routes = {}
        self.calls = []
        env = {
            accounts.ACCOUNTS_ENV_VAR: json.dumps(ACCOUNTS),
            access.SIGNING_KEY_ENV_VAR: "test-signing-key",
        }
        for name in (
            access.CLIENT_ID_ENV_VAR,
            access.CLIENT_SECRET_ENV_VAR,
            access.ALLOWED_EMAILS_ENV_VAR,
        ):
            env[name] = ""
        for patcher in (
            patch.dict(os.environ, env),
            patch.object(api.requests, "request", side_effect=self._http),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _http(self, method, url, **kwargs):
        call = Call(method, url, kwargs)
        self.calls.append(call)
        key = (method, call.path)
        if key not in self.routes:
            raise AssertionError(f"Unexpected call: {method} {call.path}")
        route = self.routes[key]
        if isinstance(route, MagicMock):
            return route
        result = route(call) if callable(route) else route
        return result if isinstance(result, MagicMock) else response(result)

    def sent(self, method, path):
        return [c for c in self.calls if (c.method, c.path) == (method, path)]


def confirm(tool, **kwargs):
    """Runs both steps of a two-step tool and returns (proposal, applied)."""
    proposal = tool(**kwargs)
    assert proposal["status"] == "PROPOSED", proposal
    applied = tool(**kwargs, validate_only=False, checksum=proposal["checksum"])
    return proposal, applied
