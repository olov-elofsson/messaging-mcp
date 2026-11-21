"""Accounts, the allow list, the API client and propose-then-confirm."""

import asyncio
import os
from unittest.mock import patch

import requests
from fastmcp.exceptions import ToolError

from messaging_mcp import access, accounts, api, confirm
from messaging_mcp.server import build_server
from tests.helpers import ACCOUNT, OTHER, FakeApiTestCase, response


class TestAccounts(FakeApiTestCase):

    def test_loads_every_account_with_a_normalised_base_url(self):
        loaded = accounts.load_accounts()
        self.assertEqual(sorted(loaded), [ACCOUNT, OTHER])
        self.assertEqual(loaded[OTHER].base_url, "https://bbbbb.api.infobip.com")

    def test_unknown_account_lists_the_known_ones_but_no_key(self):
        with self.assertRaises(ToolError) as caught:
            accounts.get_account("nope")
        self.assertIn("first, second", str(caught.exception))
        self.assertNotIn("key-1", str(caught.exception))

    def test_refuses_a_base_url_outside_infobip(self):
        for bad in ("evil.example.com", "api.infobip.com.evil.io", "a.b/x"):
            with self.assertRaisesRegex(ToolError, "not an Infobip API base URL"):
                accounts.normalise_host(bad)

    def test_refuses_an_account_without_key_and_invalid_json(self):
        os.environ[accounts.ACCOUNTS_ENV_VAR] = (
            '{"x": {"base_url": "a.api.infobip.com", "api_key": ""}}'
        )
        with self.assertRaisesRegex(ToolError, "no API key"):
            accounts.load_accounts()
        os.environ[accounts.ACCOUNTS_ENV_VAR] = "{not json"
        with self.assertRaisesRegex(ToolError, "not valid JSON"):
            accounts.load_accounts()

    def test_no_accounts_configured_is_an_empty_list(self):
        os.environ[accounts.ACCOUNTS_ENV_VAR] = ""
        self.assertEqual(accounts.load_accounts(), {})


class TestAccess(FakeApiTestCase):

    def _sign_in(self, claims, allowed="me@example.com"):
        os.environ[access.CLIENT_ID_ENV_VAR] = "id"
        os.environ[access.CLIENT_SECRET_ENV_VAR] = "secret"
        os.environ[access.ALLOWED_EMAILS_ENV_VAR] = allowed
        patcher = patch.object(access, "_signed_in_claims", return_value=claims)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_without_sign_in_configured_there_is_nobody_to_check(self):
        self.assertIsNone(access.require_allowed_user())

    def test_allows_a_listed_address_regardless_of_case(self):
        self._sign_in(
            {"email": "Me@Example.com", "email_verified": True},
            allowed="other@example.com, ME@example.com",
        )
        self.assertEqual(access.require_allowed_user(), "me@example.com")

    def test_refuses_everyone_else(self):
        self._sign_in({"email": "you@example.com", "email_verified": True})
        with self.assertRaisesRegex(ToolError, "not allowed"):
            access.require_allowed_user()

    def test_an_empty_allow_list_lets_nobody_in(self):
        self._sign_in({"email": "me@example.com", "email_verified": True}, "")
        with self.assertRaisesRegex(ToolError, "not allowed"):
            access.require_allowed_user()

    def test_refuses_unverified_address_and_missing_sign_in(self):
        self._sign_in({"email": "me@example.com", "email_verified": False})
        with self.assertRaisesRegex(ToolError, "verified"):
            access.require_allowed_user()
        with patch.object(access, "_signed_in_claims", return_value=None):
            with self.assertRaisesRegex(ToolError, "Not signed in"):
                access.require_allowed_user()

    def test_a_refused_user_makes_no_api_call(self):
        self._sign_in({"email": "you@example.com", "email_verified": True})
        with self.assertRaises(ToolError):
            api.request(ACCOUNT, "GET", "/email/1/templates")
        self.assertEqual(self.calls, [])


class TestApi(FakeApiTestCase):

    def test_uses_the_key_and_host_of_the_chosen_account(self):
        self.routes[("GET", "/x")] = {"ok": True}
        api.request(ACCOUNT, "GET", "/x")
        api.request(OTHER, "GET", "/x")
        first, second = self.calls
        self.assertEqual(first.host, "aaaaa.api.infobip.com")
        self.assertEqual(first.headers["Authorization"], "App key-1")
        self.assertEqual(second.host, "bbbbb.api.infobip.com")
        self.assertEqual(second.headers["Authorization"], "App key-2")

    def test_drops_empty_params_and_sends_forms_as_multipart(self):
        self.routes[("POST", "/x")] = {}
        api.request(ACCOUNT, "POST", "/x", params={"a": 1, "b": None},
                    form={"name": "n", "skip": None})
        self.assertEqual(self.calls[0].params, {"a": 1})
        self.assertEqual(self.calls[0].form, {"name": "n"})

    def test_error_carries_infobips_text_but_never_the_key(self):
        self.routes[("GET", "/x")] = response(
            {"requestError": {"serviceException": {"text": "Bad sender"}}}, 400
        )
        with self.assertRaises(ToolError) as caught:
            api.request(ACCOUNT, "GET", "/x")
        self.assertIn("400", str(caught.exception))
        self.assertIn("Bad sender", str(caught.exception))
        self.assertNotIn("key-1", str(caught.exception))

    def test_network_failure_becomes_a_tool_error_without_the_key(self):
        with patch.object(
            api.requests, "request",
            side_effect=requests.ConnectionError("App key-1 leaked"),
        ):
            with self.assertRaises(ToolError) as caught:
                api.request(ACCOUNT, "GET", "/x")
        self.assertNotIn("key-1", str(caught.exception))

    def test_empty_answer_is_none(self):
        self.routes[("PUT", "/x")] = response(None, 200)
        self.assertIsNone(api.request(ACCOUNT, "PUT", "/x"))


class TestConfirm(FakeApiTestCase):

    def _run(self, applied, **overrides):
        kwargs = dict(
            account=ACCOUNT, action="a", request={"x": 1}, current={"y": 2},
            summary={"s": 1}, validate_only=True, checksum=None,
            apply=lambda: applied.append(1) or {"done": True},
        )
        kwargs.update(overrides)
        return confirm.propose_or_apply(**kwargs)

    def test_proposal_applies_nothing(self):
        applied = []
        proposal = self._run(applied)
        self.assertEqual(proposal["status"], "PROPOSED")
        self.assertTrue(proposal["nothing_has_been_changed_or_sent"])
        self.assertEqual(applied, [])

    def test_applies_with_the_matching_checksum(self):
        applied = []
        checksum = self._run(applied)["checksum"]
        result = self._run(applied, validate_only=False, checksum=checksum)
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(result["result"], {"done": True})
        self.assertEqual(applied, [1])

    def test_refuses_without_or_with_a_wrong_checksum(self):
        applied = []
        with self.assertRaisesRegex(ToolError, "checksum is required"):
            self._run(applied, validate_only=False)
        with self.assertRaisesRegex(ToolError, "does not match"):
            self._run(applied, validate_only=False, checksum="0" * 64)
        self.assertEqual(applied, [])

    def test_checksum_is_bound_to_account_request_and_current_state(self):
        applied = []
        checksum = self._run(applied)["checksum"]
        for change in (
            {"account": OTHER}, {"request": {"x": 2}}, {"current": {"y": 3}},
            {"action": "b"},
        ):
            with self.assertRaisesRegex(ToolError, "does not match"):
                self._run(applied, validate_only=False, checksum=checksum, **change)
        self.assertEqual(applied, [])


class TestServer(FakeApiTestCase):

    def test_exposes_the_expected_tools_and_none_that_deletes(self):
        tools = asyncio.run(build_server().list_tools())
        names = sorted(tool.name for tool in tools)
        self.assertEqual(
            names,
            [
                "accounts_list", "audience_list_segments", "audience_list_tags",
                "audience_preview", "email_list_sender_domains",
                "email_list_suppressions", "email_send_campaign",
                "email_send_test", "flows_add_participants",
                "flows_get_add_report", "rcs_check_capability", "rcs_preview",
                "rcs_send_campaign", "rcs_send_test", "reports_delivery",
                "reports_statistics", "scheduled_cancel", "scheduled_get_status",
                "scheduled_reschedule", "templates_copy", "templates_create",
                "templates_get", "templates_list", "templates_update",
                "text_preview", "text_send_campaign", "text_send_test",
            ],
        )
        self.assertFalse([n for n in names if "delete" in n or "remove" in n])

    def test_every_changing_tool_proposes_by_default(self):
        tools = asyncio.run(build_server().list_tools())
        changing = {
            "templates_create", "templates_update", "templates_copy",
            "email_send_test", "email_send_campaign", "text_send_test",
            "text_send_campaign", "scheduled_cancel", "flows_add_participants",
            "rcs_send_test", "rcs_send_campaign", "scheduled_reschedule",
        }
        for tool in tools:
            properties = tool.parameters["properties"]
            if tool.name in changing:
                self.assertIs(properties["validate_only"]["default"], True, tool.name)
                self.assertIn("checksum", properties, tool.name)
            else:
                self.assertNotIn("validate_only", properties, tool.name)
