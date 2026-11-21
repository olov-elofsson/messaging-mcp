"""Audience loading and the email, SMS and RCS send tools."""

import datetime
import json
import os

from fastmcp.exceptions import ToolError

from messaging_mcp import audience, sending
from messaging_mcp.tools import audience_tools, email_send, text_send
from tests.helpers import ACCOUNT, OTHER, FakeApiTestCase, confirm, response
from tests.tools.templates_test import STORED


def person(n, email=True, phone=True):
    info = {}
    if email:
        info["email"] = [{"address": f"P{n}@Example.com"}]
    if phone:
        info["phone"] = [{"number": f"+4670000{n:04d}"}]
    return {"firstName": f"First{n}", "lastName": None, "contactInformation": info}


def future(days=1):
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
    return when.replace(microsecond=0).isoformat()


class SendCase(FakeApiTestCase):

    def setUp(self):
        super().setUp()
        self.people = [person(1), person(2), person(3, email=False), person(1)]
        self.routes.update({
            ("GET", "/people/2/tags/vip"): {"name": "vip"},
            ("GET", "/people/2/persons"): self._persons,
            ("GET", "/people/3/segments/12"): {"id": 12, "name": "Buyers"},
            ("GET", "/people/3/segments/12/persons"): self._segment,
            ("GET", "/email/1/templates/7"): dict(STORED),
            ("POST", "/email/4/messages"): {"bulkId": "b"},
            ("POST", "/sms/1/preview"): {
                "previews": [{"textPreview": "Hi", "messageCount": 2,
                              "charactersRemaining": 10}]
            },
            ("POST", "/sms/3/messages"): {"bulkId": "b"},
            ("POST", "/messages-api/1/messages"): {"bulkId": "b"},
        })

    def _slice(self, page, size):
        return self.people[page * size:(page + 1) * size]

    def _persons(self, call):
        return {"persons": self._slice(call.params["page"] - 1, call.params["limit"])}

    def _segment(self, call):
        return {"content": self._slice(call.params["page"], call.params["size"])}


class TestAudience(SendCase):

    def test_tag_audience_filters_on_the_tag_and_dedupes(self):
        loaded = audience.load_audience(ACCOUNT, "tag", "vip", "EMAIL")
        self.assertEqual(
            [r.address for r in loaded.recipients],
            ["p1@example.com", "p2@example.com"],
        )
        summary = loaded.summary()
        self.assertEqual(summary["people_in_audience"], 4)
        self.assertEqual(summary["skipped_without_address"], 1)
        self.assertEqual(summary["skipped_duplicates"], 1)
        (call,) = self.sent("GET", "/people/2/persons")
        self.assertEqual(json.loads(call.params["filter"]),
                         {"#contains": {"tags": "vip"}})

    def test_segment_audience_reads_phone_numbers_for_sms(self):
        loaded = audience.load_audience(ACCOUNT, "segment", "12", "SMS")
        self.assertEqual(loaded.label, "Buyers")
        self.assertEqual(
            [r.address for r in loaded.recipients],
            ["46700000001", "46700000002", "46700000003"],
        )

    def test_reads_every_page(self):
        self.people = [person(n) for n in range(2300)]
        loaded = audience.load_audience(ACCOUNT, "tag", "vip", "EMAIL")
        self.assertEqual(len(loaded.recipients), 2300)
        pages = [c.params["page"] for c in self.sent("GET", "/people/2/persons")]
        self.assertEqual(pages, [1, 2, 3])

    def test_refuses_an_audience_over_the_limit(self):
        os.environ[audience.MAX_RECIPIENTS_ENV_VAR] = "1"
        self.addCleanup(os.environ.pop, audience.MAX_RECIPIENTS_ENV_VAR)
        with self.assertRaisesRegex(ToolError, "more than 1 recipients"):
            audience.load_audience(ACCOUNT, "tag", "vip", "EMAIL")

    def test_unknown_tag_fails_before_anyone_is_read(self):
        self.routes[("GET", "/people/2/tags/nope")] = response({"x": 1}, 404)
        with self.assertRaisesRegex(ToolError, "404"):
            audience.load_audience(ACCOUNT, "tag", "nope", "EMAIL")
        self.assertEqual(self.sent("GET", "/people/2/persons"), [])

    def test_refuses_unknown_kind_and_channel(self):
        with self.assertRaisesRegex(ToolError, "audience_kind"):
            audience.load_audience(ACCOUNT, "list", "vip", "EMAIL")
        with self.assertRaisesRegex(ToolError, "channel"):
            audience.load_audience(ACCOUNT, "tag", "vip", "FAX")

    def test_fingerprint_changes_when_someone_joins(self):
        before = audience.load_audience(ACCOUNT, "tag", "vip", "EMAIL").fingerprint()
        self.people.append(person(9))
        after = audience.load_audience(ACCOUNT, "tag", "vip", "EMAIL").fingerprint()
        self.assertNotEqual(before, after)

    def test_tools_list_tags_segments_and_preview(self):
        self.routes[("GET", "/people/2/tags")] = {"tags": [{"name": "vip"}]}
        self.routes[("GET", "/people/3/segments")] = {
            "content": [{"id": 12, "name": "Buyers"}]
        }
        self.assertEqual(audience_tools.audience_list_tags(ACCOUNT)["tags"][0]["name"], "vip")
        self.assertEqual(
            audience_tools.audience_list_segments(ACCOUNT)["segments"],
            [{"segment_id": 12, "name": "Buyers"}],
        )
        preview = audience_tools.audience_preview(ACCOUNT, "tag", "vip", "EMAIL")
        self.assertEqual(preview["recipients"], 2)


class TestSendingRules(SendCase):

    def test_send_at_needs_time_zone_future_and_at_most_30_days(self):
        self.assertIsNone(sending.parse_send_at(None))
        self.assertTrue(sending.parse_send_at(future()).endswith("+0000"))
        with self.assertRaisesRegex(ToolError, "time zone"):
            sending.parse_send_at("2099-01-01T09:00:00")
        with self.assertRaisesRegex(ToolError, "future"):
            sending.parse_send_at("2020-01-01T09:00:00+01:00")
        with self.assertRaisesRegex(ToolError, "at most 30 days"):
            sending.parse_send_at(future(days=40))
        with self.assertRaisesRegex(ToolError, "date and time"):
            sending.parse_send_at("tomorrow")

    def test_send_at_is_converted_to_utc(self):
        when = datetime.datetime.now(datetime.timezone.utc).replace(
            microsecond=0) + datetime.timedelta(days=2)
        local = when.astimezone(datetime.timezone(datetime.timedelta(hours=1)))
        self.assertEqual(
            sending.parse_send_at(local.isoformat()),
            when.strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
        )

    def test_test_recipients_are_validated_and_limited(self):
        self.assertEqual(
            sending.clean_test_recipients(["+46 70-123 45 67"], "SMS"), ["46701234567"]
        )
        with self.assertRaisesRegex(ToolError, "not a valid"):
            sending.clean_test_recipients(["nope"], "EMAIL")
        with self.assertRaisesRegex(ToolError, "between 1 and 5"):
            sending.clean_test_recipients([f"a{n}@b.se" for n in range(6)], "EMAIL")
        with self.assertRaisesRegex(ToolError, "between 1 and 5"):
            sending.clean_test_recipients([], "EMAIL")

    def test_test_token_is_bound_to_account_channel_and_content(self):
        token = sending.test_token(ACCOUNT, "SMS", {"text": "a"})
        sending.require_test_token(ACCOUNT, "SMS", {"text": "a"}, token)
        for args in ((OTHER, "SMS", {"text": "a"}), (ACCOUNT, "RCS", {"text": "a"}),
                     (ACCOUNT, "SMS", {"text": "b"})):
            with self.assertRaisesRegex(ToolError, "test send"):
                sending.require_test_token(*args, token)
        with self.assertRaisesRegex(ToolError, "test send"):
            sending.require_test_token(ACCOUNT, "SMS", {"text": "a"}, None)


class TestEmail(SendCase):

    def _token(self, **kwargs):
        _, applied = confirm(email_send.email_send_test, account=ACCOUNT,
                             template_id=7, to=["me@example.com"], **kwargs)
        return applied["result"]["test_token"]

    def test_test_proposal_sends_nothing(self):
        proposal = email_send.email_send_test(ACCOUNT, 7, ["me@example.com"])
        self.assertEqual(proposal["summary"]["sends_test_to"], ["me@example.com"])
        self.assertEqual(proposal["summary"]["subject"], "Hello")
        self.assertEqual(self.sent("POST", "/email/4/messages"), [])

    def test_test_send_goes_only_to_the_test_address_with_a_prefix(self):
        self._token()
        (post,) = self.sent("POST", "/email/4/messages")
        message = post.json["messages"][0]
        self.assertEqual(message["destinations"],
                         [{"to": [{"destination": "me@example.com"}]}])
        self.assertEqual(message["content"],
                         {"templateId": "7", "subject": "[TEST] Hello"})
        self.assertEqual(message["sender"], "Shop <news@shop.example>")
        self.assertNotIn("sendAt", post.json["options"]["schedule"])

    def test_campaign_without_a_test_is_refused(self):
        with self.assertRaisesRegex(ToolError, "test send"):
            email_send.email_send_campaign(ACCOUNT, 7, "tag", "vip", "wrong")
        self.assertEqual(self.sent("POST", "/email/4/messages"), [])

    def test_campaign_proposal_shows_the_count_and_sends_nothing(self):
        token = self._token()
        self.calls.clear()
        proposal = email_send.email_send_campaign(
            ACCOUNT, 7, "tag", "vip", token, send_at=future())
        summary = proposal["summary"]
        self.assertEqual(summary["recipients"], 2)
        self.assertEqual(summary["template_name"], "Spring")
        self.assertEqual(summary["audience"], "tag: vip")
        self.assertTrue(summary["send_time_utc"].endswith("+0000"))
        self.assertEqual(self.sent("POST", "/email/4/messages"), [])

    def test_confirmed_campaign_sends_one_group_per_recipient_scheduled(self):
        token = self._token()
        self.calls.clear()
        when = future()
        _, applied = confirm(
            email_send.email_send_campaign, account=ACCOUNT, template_id=7,
            audience_kind="tag", audience_value="vip", test_token=token,
            send_at=when)
        (post,) = self.sent("POST", "/email/4/messages")
        message = post.json["messages"][0]
        self.assertEqual(
            message["destinations"],
            [{"to": [{"destination": "p1@example.com",
                      "placeholders": '{"firstName": "First1"}'}]},
             {"to": [{"destination": "p2@example.com",
                      "placeholders": '{"firstName": "First2"}'}]}],
        )
        self.assertEqual(message["content"], {"templateId": "7", "subject": "Hello"})
        reference = applied["result"]["campaign_reference_id"]
        self.assertEqual(message["options"], {"campaignReferenceId": reference})
        self.assertEqual(post.json["options"]["schedule"],
                         {"bulkId": reference + "-1",
                          "sendAt": sending.parse_send_at(when)})
        self.assertEqual(applied["result"]["recipients_accepted"], 2)

    def test_audience_change_after_the_proposal_is_refused(self):
        token = self._token()
        proposal = email_send.email_send_campaign(ACCOUNT, 7, "tag", "vip", token)
        self.people.append(person(9))
        self.calls.clear()
        with self.assertRaisesRegex(ToolError, "does not match"):
            email_send.email_send_campaign(
                ACCOUNT, 7, "tag", "vip", token, validate_only=False,
                checksum=proposal["checksum"])
        self.assertEqual(self.sent("POST", "/email/4/messages"), [])

    def test_template_change_after_the_test_requires_a_new_test(self):
        token = self._token()
        self.routes[("GET", "/email/1/templates/7")] = dict(
            STORED, updatedAt="2026-11-05T10:00:00Z")
        with self.assertRaisesRegex(ToolError, "test send"):
            email_send.email_send_campaign(ACCOUNT, 7, "tag", "vip", token)

    def test_token_from_one_subject_does_not_cover_another(self):
        token = self._token()
        with self.assertRaisesRegex(ToolError, "test send"):
            email_send.email_send_campaign(
                ACCOUNT, 7, "tag", "vip", token, subject="Other")

    def test_large_send_is_batched_and_a_failure_names_accepted_batches(self):
        token = self._token()
        self.people = [person(n) for n in range(1200)]
        posts = []

        def flaky(call):
            posts.append(call)
            if len(posts) == 3:
                return response({"requestError": {"serviceException": {"text": "boom"}}}, 500)
            return {"bulkId": "b"}

        self.routes[("POST", "/email/4/messages")] = flaky
        proposal = email_send.email_send_campaign(ACCOUNT, 7, "tag", "vip", token)
        with self.assertRaises(ToolError) as caught:
            email_send.email_send_campaign(
                ACCOUNT, 7, "tag", "vip", token, validate_only=False,
                checksum=proposal["checksum"])
        text = str(caught.exception)
        self.assertIn("Stopped after 1000 of 1200", text)
        self.assertIn("-1", text)
        self.assertIn("boom", text)
        self.assertEqual([len(p.json["messages"][0]["destinations"]) for p in posts],
                         [500, 500, 200])

    def test_refuses_a_template_without_sender_or_subject(self):
        self.routes[("GET", "/email/1/templates/7")] = dict(STORED, subject=None)
        with self.assertRaisesRegex(ToolError, "no subject"):
            email_send.email_send_test(ACCOUNT, 7, ["me@example.com"])
        self.routes[("GET", "/email/1/templates/7")] = dict(STORED, **{"from": None})
        with self.assertRaisesRegex(ToolError, "no sender"):
            email_send.email_send_test(ACCOUNT, 7, ["me@example.com"])

    def test_empty_audience_is_refused(self):
        token = self._token()
        self.people = [person(1, email=False)]
        with self.assertRaisesRegex(ToolError, "no one with an email"):
            email_send.email_send_campaign(ACCOUNT, 7, "tag", "vip", token)


class TestText(SendCase):

    def _token(self, channel="SMS", text="Hi"):
        _, applied = confirm(text_send.text_send_test, account=ACCOUNT,
                             channel=channel, sender="Shop", text=text,
                             to=["+46701234567"])
        return applied["result"]["test_token"]

    def test_preview_gives_sms_parts_and_sends_nothing(self):
        preview = text_send.text_preview(ACCOUNT, "sms", "Hi")
        self.assertEqual(preview["sms_parts_per_message"], 2)
        self.assertEqual(self.sent("POST", "/sms/3/messages"), [])
        self.assertNotIn("sms_parts_per_message",
                         text_send.text_preview(ACCOUNT, "RCS", "Hi"))
        with self.assertRaisesRegex(ToolError, "SMS or RCS"):
            text_send.text_preview(ACCOUNT, "EMAIL", "Hi")

    def test_sms_test_send_goes_only_to_the_test_number(self):
        self._token()
        (post,) = self.sent("POST", "/sms/3/messages")
        message = post.json["messages"][0]
        self.assertEqual(message["destinations"], [{"to": "46701234567"}])
        self.assertEqual(message["content"], {"text": "Hi"})
        self.assertEqual(message["sender"], "Shop")

    def test_sms_campaign_proposal_shows_recipients_parts_and_no_price(self):
        token = self._token()
        self.calls.clear()
        proposal = text_send.text_send_campaign(
            ACCOUNT, "SMS", "Shop", "Hi", "tag", "vip", token)
        summary = proposal["summary"]
        self.assertEqual(summary["recipients"], 3)
        self.assertEqual(summary["sms_parts_per_message"], 2)
        self.assertEqual(summary["sms_parts_in_total"], 6)
        self.assertIn("no price lookup", summary["cost"])
        self.assertEqual(self.sent("POST", "/sms/3/messages"), [])

    def test_confirmed_sms_campaign_sends_to_the_audience(self):
        token = self._token()
        self.calls.clear()
        _, applied = confirm(
            text_send.text_send_campaign, account=ACCOUNT, channel="SMS",
            sender="Shop", text="Hi", audience_kind="segment",
            audience_value="12", test_token=token)
        (post,) = self.sent("POST", "/sms/3/messages")
        self.assertEqual(
            post.json["messages"][0]["destinations"],
            [{"to": "46700000001"}, {"to": "46700000002"}, {"to": "46700000003"}])
        self.assertEqual(applied["result"]["recipients_accepted"], 3)
        self.assertEqual(self.sent("POST", "/messages-api/1/messages"), [])

    def test_rcs_uses_the_messages_api_with_a_text_body(self):
        token = self._token(channel="RCS")
        confirm(
            text_send.text_send_campaign, account=ACCOUNT, channel="RCS",
            sender="Shop", text="Hi", audience_kind="tag",
            audience_value="vip", test_token=token, send_at=future())
        test_post, post = self.sent("POST", "/messages-api/1/messages")
        message = post.json["messages"][0]
        self.assertEqual(message["channel"], "RCS")
        self.assertEqual(message["content"], {"body": {"type": "TEXT", "text": "Hi"}})
        self.assertIn("sendAt", post.json["options"]["schedule"])
        self.assertEqual(self.sent("POST", "/sms/3/messages"), [])

    def test_changed_text_or_channel_requires_a_new_test(self):
        token = self._token()
        with self.assertRaisesRegex(ToolError, "test send"):
            text_send.text_send_campaign(
                ACCOUNT, "SMS", "Shop", "Hi!", "tag", "vip", token)
        with self.assertRaisesRegex(ToolError, "test send"):
            text_send.text_send_campaign(
                ACCOUNT, "RCS", "Shop", "Hi", "tag", "vip", token)
        with self.assertRaisesRegex(ToolError, "test send"):
            text_send.text_send_campaign(
                OTHER, "SMS", "Shop", "Hi", "tag", "vip", token)
