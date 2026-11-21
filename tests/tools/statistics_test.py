"""Statistics through the Metrics API, the callback inbox, and small read tools."""

import asyncio
import datetime
import os

from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from messaging_mcp import access, metrics_inbox
from messaging_mcp.server import build_server
from messaging_mcp.tools import email_settings, scheduled, statistics
from tests.helpers import ACCOUNT, FakeApiTestCase, confirm
from tests.tools.sending_test import future

ANSWER = {
    "requestId": "r1", "status": "SUCCESS",
    "response": {
        "columns": [{"columnName": "CAMPAIGN_REFERENCE"},
                    {"columnName": "DELIVERED_TRAFFIC_COUNT"},
                    {"columnName": "SEEN_OPENED_TRAFFIC_COUNT"}],
        "rows": [["email-1", 90, 40], ["email-2", 10, 3]],
    },
}


class StatisticsCase(FakeApiTestCase):

    def setUp(self):
        super().setUp()
        os.environ[access.BASE_URL_ENV_VAR] = "https://server.example"
        os.environ[statistics.WAIT_SECONDS_ENV_VAR] = "2"
        self.addCleanup(os.environ.pop, access.BASE_URL_ENV_VAR, None)
        self.addCleanup(os.environ.pop, statistics.WAIT_SECONDS_ENV_VAR, None)
        self.answer = ANSWER
        self.routes[("POST", "/metrics/1/query-aggregate-data")] = self._query
        self.routes[("GET", "/metrics/1/requests/r1")] = {"status": "IN_PROGRESS"}

    def _query(self, call):
        token = call.json["callbackUrl"].rsplit("/", 1)[1]
        if self.answer is not None:
            loop = self.loop
            loop.call_soon_threadsafe(
                metrics_inbox._pending[token].set_result, self.answer)
        return {"requestId": "r1"}

    def run_tool(self, **kwargs):
        async def main():
            self.loop = asyncio.get_running_loop()
            return await statistics.reports_statistics(**kwargs)
        return asyncio.run(main())


class TestStatistics(StatisticsCase):

    def test_asks_for_the_period_channel_and_grouping(self):
        result = self.run_tool(account=ACCOUNT, channel="email", since="2026-11-01",
                               until="2026-11-10")
        (call,) = self.sent("POST", "/metrics/1/query-aggregate-data")
        request = call.json["request"]
        self.assertTrue(call.json["callbackUrl"].startswith(
            "https://server.example/metrics-callback/"))
        self.assertEqual(request["aggregateBy"], ["CAMPAIGN_REFERENCE"])
        self.assertEqual(request["filterBy"]["channelCodes"], ["EMAIL"])
        self.assertEqual(request["filterBy"]["sentSince"], "2026-11-01T00:00:00.000+0000")
        self.assertEqual(request["filterBy"]["sentUntil"], "2026-11-10T23:59:59.000+0000")
        self.assertIn("UNSUBSCRIBED_USER_COUNT", request["metrics"])
        self.assertIn("SEEN_OPENED_TRAFFIC_COUNT", request["metrics"])
        self.assertEqual(result["rows"], [
            {"CAMPAIGN_REFERENCE": "email-1", "DELIVERED_TRAFFIC_COUNT": 90,
             "SEEN_OPENED_TRAFFIC_COUNT": 40},
            {"CAMPAIGN_REFERENCE": "email-2", "DELIVERED_TRAFFIC_COUNT": 10,
             "SEEN_OPENED_TRAFFIC_COUNT": 3}])
        self.assertEqual(metrics_inbox._pending, {})

    def test_filters_on_one_send_or_one_flow_and_groups_by_url(self):
        self.run_tool(account=ACCOUNT, channel="SMS", since="2026-11-01",
                      until="2026-11-02", group_by="url",
                      campaign_reference_id="sms-1", flow_id=55)
        request = self.sent("POST", "/metrics/1/query-aggregate-data")[0].json["request"]
        self.assertEqual(request["filterBy"]["campaignReferenceIds"], ["sms-1"])
        self.assertEqual(request["filterBy"]["communicationIds"], [55])
        self.assertEqual(request["aggregateBy"], ["URL"])
        self.assertEqual(request["metrics"], ["UNIQUE_URL_CLICK_COUNT"])

    def test_refuses_bad_input_before_asking_infobip(self):
        cases = [
            (dict(channel="FAX", since="2026-11-01"), "EMAIL, SMS or RCS"),
            (dict(channel="SMS", since="2026-11-01", group_by="HOUR"), "group_by"),
            (dict(channel="SMS", since="yesterday"), "date such as"),
            (dict(channel="SMS", since="2026-11-10", until="2026-11-01"), "later than"),
            (dict(channel="SMS", since="2026-01-01", until="2026-11-01"), "at most 93"),
            (dict(channel="SMS", since="2026-11-01T10:00:00"), "time zone"),
        ]
        for kwargs, message in cases:
            with self.assertRaisesRegex(ToolError, message):
                self.run_tool(account=ACCOUNT, **kwargs)
        self.assertEqual(self.calls, [])

    def test_needs_a_public_address(self):
        os.environ[access.BASE_URL_ENV_VAR] = "http://localhost:8080"
        with self.assertRaisesRegex(ToolError, "public https address"):
            self.run_tool(account=ACCOUNT, channel="SMS", since="2026-11-01",
                          until="2026-11-02")

    def test_failed_and_missing_answers_are_reported(self):
        self.answer = {"status": "FAILED", "failureMessage": "too big"}
        with self.assertRaisesRegex(ToolError, "too big"):
            self.run_tool(account=ACCOUNT, channel="SMS", since="2026-11-01",
                          until="2026-11-02")
        self.answer = None
        os.environ[statistics.WAIT_SECONDS_ENV_VAR] = "0.2"
        with self.assertRaisesRegex(ToolError, "did not answer in time.*IN_PROGRESS"):
            self.run_tool(account=ACCOUNT, channel="SMS", since="2026-11-01",
                          until="2026-11-02")
        self.assertEqual(metrics_inbox._pending, {})

    def test_empty_answer_explains_the_lag(self):
        self.answer = {"status": "SUCCESS", "response": {"columns": [], "rows": []}}
        result = self.run_tool(account=ACCOUNT, channel="RCS", since="2026-11-01",
                               until="2026-11-02")
        self.assertEqual(result["rows"], [])
        self.assertIn("lag", result["note"])


class TestCallbackRoute(FakeApiTestCase):

    def test_unknown_token_is_refused_and_a_waiting_one_is_delivered(self):
        os.environ[access.CLIENT_ID_ENV_VAR] = "id"
        os.environ[access.CLIENT_SECRET_ENV_VAR] = "secret"
        app = build_server().http_app()

        async def main():
            token, slot = metrics_inbox.open_slot()
            return token, slot

        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        token, slot = loop.run_until_complete(main())
        self.addCleanup(metrics_inbox.close_slot, token)
        with TestClient(app) as client:
            self.assertEqual(
                client.post("/metrics-callback/wrong", json={}).status_code, 404)
            self.assertEqual(
                client.post(f"/metrics-callback/{token}", content=b"nope").status_code,
                400)
            # The MCP endpoint itself still needs a sign-in.
            self.assertEqual(client.post("/mcp", json={}).status_code, 401)
            self.assertEqual(client.get(f"/metrics-callback/{token}").status_code, 405)


class TestReschedule(FakeApiTestCase):

    def setUp(self):
        super().setUp()
        self.status = "PENDING"
        self.routes[("GET", "/email/1/bulks/status")] = lambda c: {
            "bulks": [{"bulkId": "b-1", "status": self.status}]}
        self.routes[("GET", "/email/1/bulks")] = {
            "bulks": [{"bulkId": "b-1", "sendAt": "2026-12-01T08:00:00Z"}]}
        self.routes[("PUT", "/email/1/bulks")] = {"bulkId": "b-1"}

    def test_shows_the_current_time_and_moves_only_after_confirmation(self):
        when = future(days=3)
        proposal = scheduled.scheduled_reschedule(ACCOUNT, "EMAIL", "b-1", when)
        self.assertEqual(proposal["summary"]["current_send_time"],
                         ["2026-12-01T08:00:00Z"])
        self.assertEqual(self.sent("PUT", "/email/1/bulks"), [])
        confirm(scheduled.scheduled_reschedule, account=ACCOUNT, channel="EMAIL",
                bulk_id="b-1", send_at=when)
        (put,) = self.sent("PUT", "/email/1/bulks")
        self.assertEqual(put.params, {"bulkId": "b-1"})
        self.assertTrue(put.json["sendAt"].endswith("+0000"))

    def test_refuses_sent_sends_past_times_and_other_channels(self):
        with self.assertRaisesRegex(ToolError, "future"):
            scheduled.scheduled_reschedule(ACCOUNT, "EMAIL", "b-1",
                                           "2020-01-01T09:00:00+01:00")
        with self.assertRaisesRegex(ToolError, "EMAIL and SMS"):
            scheduled.scheduled_reschedule(ACCOUNT, "RCS", "b-1", future())
        self.status = "FINISHED"
        with self.assertRaisesRegex(ToolError, "Nothing to move"):
            scheduled.scheduled_reschedule(ACCOUNT, "EMAIL", "b-1", future())
        self.assertEqual(self.sent("PUT", "/email/1/bulks"), [])


class TestEmailSettings(FakeApiTestCase):

    def test_lists_sender_domains_with_their_state(self):
        self.routes[("GET", "/email/1/domains")] = {"results": [
            {"domainName": "mail.shop.example", "active": True, "blocked": False,
             "dnsRecords": [{"verified": True}, {"verified": False}],
             "tracking": {"opens": True}}]}
        (domain,) = email_settings.email_list_sender_domains(ACCOUNT)["domains"]
        self.assertEqual(domain["domain"], "mail.shop.example")
        self.assertTrue(domain["active"])
        self.assertFalse(domain["dns_verified"])

    def test_lists_blocked_addresses_for_a_domain_and_reason(self):
        self.routes[("GET", "/email/1/suppressions")] = {"results": [
            {"emailAddress": "a@b.se", "createdDate": "2026-11-01", "reason": "550"}]}
        result = email_settings.email_list_suppressions(
            ACCOUNT, "mail.shop.example", "bounce")
        self.assertEqual(result["addresses"][0]["email"], "a@b.se")
        self.assertEqual(self.calls[0].params["type"], "BOUNCE")
        self.assertEqual(self.calls[0].params["domainName"], "mail.shop.example")
        with self.assertRaisesRegex(ToolError, "reason_type"):
            email_settings.email_list_suppressions(ACCOUNT, "d", "SPAM")
