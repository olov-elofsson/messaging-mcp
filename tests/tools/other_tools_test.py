"""Scheduled sends, delivery reports, flows and the account list."""

from fastmcp.exceptions import ToolError

from messaging_mcp.tools import account_tools, flows, reports, scheduled
from tests.helpers import ACCOUNT, FakeApiTestCase, confirm


class TestAccountsTool(FakeApiTestCase):

    def test_lists_aliases_and_hosts_but_no_keys(self):
        result = account_tools.accounts_list()
        self.assertEqual([a["account"] for a in result["accounts"]],
                         ["first", "second"])
        self.assertNotIn("key-", str(result))


class TestScheduled(FakeApiTestCase):

    def setUp(self):
        super().setUp()
        self.status = "PENDING"
        self.routes[("GET", "/email/1/bulks/status")] = lambda call: {
            "externalBulkId": call.params["bulkId"],
            "bulks": [{"bulkId": "x", "status": self.status}],
        }
        self.routes[("PUT", "/email/1/bulks/status")] = {
            "bulkId": "b-1", "status": "CANCELED"}
        self.routes[("GET", "/sms/1/bulks/status")] = {
            "bulkId": "b-1", "status": "PENDING"}

    def test_status_for_email_and_sms(self):
        self.assertEqual(
            scheduled.scheduled_get_status(ACCOUNT, "email", "b-1"),
            {"bulks": [{"bulk_id": "x", "status": "PENDING"}]})
        self.assertEqual(
            scheduled.scheduled_get_status(ACCOUNT, "SMS", "b-1"),
            {"bulks": [{"bulk_id": "b-1", "status": "PENDING"}]})
        with self.assertRaisesRegex(ToolError, "EMAIL and SMS"):
            scheduled.scheduled_get_status(ACCOUNT, "RCS", "b-1")

    def test_cancel_shows_current_status_and_needs_confirmation(self):
        proposal = scheduled.scheduled_cancel(ACCOUNT, "EMAIL", "b-1")
        self.assertEqual(proposal["summary"]["current"],
                         [{"bulk_id": "x", "status": "PENDING"}])
        self.assertEqual(self.sent("PUT", "/email/1/bulks/status"), [])
        confirm(scheduled.scheduled_cancel, account=ACCOUNT, channel="EMAIL",
                bulk_id="b-1")
        (put,) = self.sent("PUT", "/email/1/bulks/status")
        self.assertEqual(put.json, {"status": "CANCELED"})
        self.assertEqual(put.params, {"bulkId": "b-1"})

    def test_refuses_to_cancel_what_is_already_sent(self):
        self.status = "FINISHED"
        with self.assertRaisesRegex(ToolError, "Nothing to cancel"):
            scheduled.scheduled_cancel(ACCOUNT, "EMAIL", "b-1")

    def test_refuses_when_status_changed_after_the_proposal(self):
        proposal = scheduled.scheduled_cancel(ACCOUNT, "EMAIL", "b-1")
        self.status = "PAUSED"
        with self.assertRaisesRegex(ToolError, "does not match"):
            scheduled.scheduled_cancel(ACCOUNT, "EMAIL", "b-1",
                                       validate_only=False,
                                       checksum=proposal["checksum"])
        self.assertEqual(self.sent("PUT", "/email/1/bulks/status"), [])


def log(group, error=None, price=0.01, count=1):
    return {"status": {"groupName": group, "name": group + "_X"},
            "error": {"name": error} if error else None,
            "price": {"pricePerMessage": price, "currency": "EUR"},
            "messageCount": count}


class TestReports(FakeApiTestCase):

    def test_counts_statuses_reasons_and_cost_across_pages(self):
        pages = {
            None: {"results": [log("DELIVERED"), log("DELIVERED", count=2)],
                   "cursor": {"nextCursor": "c2"}},
            "c2": {"results": [log("UNDELIVERABLE", "EC_INVALID_ADDRESS"),
                               log("PENDING")], "cursor": {}},
        }
        self.routes[("GET", "/sms/3/logs")] = lambda call: pages[
            call.params.get("cursor")]
        result = reports.reports_delivery(ACCOUNT, "sms",
                                          campaign_reference_id="sms-1")
        self.assertEqual(result["messages_in_logs"], 4)
        self.assertEqual(result["by_status"],
                         {"DELIVERED": 2, "UNDELIVERABLE": 1, "PENDING": 1})
        self.assertEqual(result["failed_by_reason"], {"EC_INVALID_ADDRESS": 1})
        self.assertEqual(result["delivered_share"], 0.5)
        self.assertEqual(result["cost"], {"amount": 0.05, "currency": "EUR"})
        self.assertTrue(result["all_pages_read"])
        self.assertIsNone(result["note"])
        first = self.calls[0].params
        self.assertEqual(first["campaignReferenceId"], "sms-1")
        self.assertNotIn("bulkId", first)

    def test_email_report_says_what_the_logs_do_not_contain(self):
        self.routes[("GET", "/email/4/logs")] = {"results": []}
        result = reports.reports_delivery(ACCOUNT, "EMAIL", bulk_id="b-1")
        self.assertEqual(result["messages_in_logs"], 0)
        self.assertIsNone(result["delivered_share"])
        self.assertIn("Opens, clicks and unsubscribes", result["note"])

    def test_needs_a_reference_and_a_supported_channel(self):
        with self.assertRaisesRegex(ToolError, "campaign_reference_id or bulk_id"):
            reports.reports_delivery(ACCOUNT, "SMS")
        with self.assertRaisesRegex(ToolError, "EMAIL and SMS"):
            reports.reports_delivery(ACCOUNT, "RCS", bulk_id="b")


class TestFlows(FakeApiTestCase):

    def setUp(self):
        super().setUp()
        self.routes[("POST", "/moments/1/flows/55/participants")] = {
            "operationId": "op-1"}
        self.routes[("GET", "/moments/1/flows/55/participants/report")] = {
            "operationId": "op-1", "participants": []}

    def test_proposal_adds_nobody(self):
        proposal = flows.flows_add_participants(
            ACCOUNT, 55, ["a@b.se", "a@b.se", " c@d.se "], "email")
        self.assertEqual(proposal["summary"]["adds_people"], 2)
        self.assertEqual(self.sent("POST", "/moments/1/flows/55/participants"), [])

    def test_confirm_posts_the_participants(self):
        _, applied = confirm(flows.flows_add_participants, account=ACCOUNT,
                             flow_id=55, identifiers=["a@b.se"],
                             identifier_type="EMAIL")
        (post,) = self.sent("POST", "/moments/1/flows/55/participants")
        self.assertEqual(post.json, {"participants": [
            {"identifyBy": {"identifier": "a@b.se", "type": "EMAIL"}}]})
        self.assertEqual(applied["result"], {"operation_id": "op-1"})

    def test_limits_and_identifier_type(self):
        with self.assertRaisesRegex(ToolError, "between 1 and 1000"):
            flows.flows_add_participants(
                ACCOUNT, 55, [f"a{n}@b.se" for n in range(1001)], "EMAIL")
        with self.assertRaisesRegex(ToolError, "between 1 and 1000"):
            flows.flows_add_participants(ACCOUNT, 55, [], "EMAIL")
        with self.assertRaisesRegex(ToolError, "identifier_type"):
            flows.flows_add_participants(ACCOUNT, 55, ["x"], "NAME")

    def test_report_passes_the_operation_id(self):
        flows.flows_get_add_report(ACCOUNT, 55, "op-1")
        self.assertEqual(self.calls[0].params, {"operationId": "op-1"})
