"""Rich RCS content and the RCS send tools."""

from fastmcp.exceptions import ToolError

from messaging_mcp import rcs_content
from messaging_mcp.tools import rcs_send
from tests.helpers import ACCOUNT, FakeApiTestCase, confirm
from tests.tools.sending_test import SendCase, future, person

CARD = {
    "type": "CARD", "title": "Sale", "description": "Ends Friday",
    "image_url": "https://img.example/a.png",
    "buttons": [{"type": "OPEN_URL", "text": "Shop", "url": "https://shop.example"}],
}


class TestContent(FakeApiTestCase):

    def test_text_with_buttons(self):
        built = rcs_content.build({
            "type": "text", "text": "Hi",
            "buttons": [{"type": "REPLY", "text": "Yes"},
                        {"type": "DIAL_PHONE", "text": "Call", "phone_number": "+46701"}]})
        self.assertEqual(built, {
            "type": "TEXT", "text": "Hi",
            "suggestions": [
                {"type": "REPLY", "text": "Yes", "postbackData": "Yes"},
                {"type": "DIAL_PHONE", "text": "Call", "postbackData": "Call",
                 "phoneNumber": "+46701"}]})

    def test_card_puts_its_buttons_on_the_card(self):
        built = rcs_content.build(CARD)
        self.assertEqual(built["type"], "CARD")
        self.assertEqual(built["orientation"], "VERTICAL")
        self.assertEqual(built["content"]["media"],
                         {"file": {"url": "https://img.example/a.png"}, "height": "MEDIUM"})
        self.assertEqual(built["content"]["suggestions"][0]["url"], "https://shop.example")
        self.assertNotIn("suggestions", built)

    def test_carousel_of_cards(self):
        built = rcs_content.build({"type": "CAROUSEL", "cards": [CARD, CARD],
                                   "buttons": [{"type": "REPLY", "text": "Stop"}]})
        self.assertEqual(built["cardWidth"], "MEDIUM")
        self.assertEqual(len(built["contents"]), 2)
        self.assertEqual(built["suggestions"][0]["text"], "Stop")

    def test_refuses_what_rcs_would_reject(self):
        cases = [
            ({"type": "TEXT"}, "text is required"),
            ({"type": "TEXT", "text": "x" * 3073}, "at most 3072"),
            ({"type": "VIDEO"}, "TEXT, CARD or CAROUSEL"),
            ({"type": "CARD"}, "needs a title"),
            ({"type": "CARD", "title": "t", "image_url": "http://x/a.png"}, "https://"),
            ({"type": "CARD", "title": "t", "image_url": "https://x/a.png",
              "image_height": "HUGE"}, "image_height"),
            ({"type": "CAROUSEL", "cards": [CARD]}, "between 2 and 10"),
            ({"type": "CAROUSEL", "cards": [CARD] * 11}, "between 2 and 10"),
            ({"type": "TEXT", "text": "x",
              "buttons": [{"type": "REPLY", "text": "y" * 26}]}, "at most 25"),
            ({"type": "TEXT", "text": "x", "buttons": [{"type": "MAP", "text": "y"}]},
             "REPLY, OPEN_URL or DIAL_PHONE"),
            ({"type": "TEXT", "text": "x",
              "buttons": [{"type": "OPEN_URL", "text": "y"}]}, "url is required"),
            ({"type": "TEXT", "text": "x",
              "buttons": [{"type": "DIAL_PHONE", "text": "y", "phone_number": "abc"}]},
             "phone_number"),
            ({"type": "CARD", "title": "t",
              "buttons": [{"type": "REPLY", "text": str(n)} for n in range(5)]},
             "at most 4 buttons"),
            ({"type": "TEXT", "text": "x",
              "buttons": [{"type": "REPLY", "text": str(n)} for n in range(12)]},
             "at most 11 buttons"),
        ]
        for content, message in cases:
            with self.assertRaisesRegex(ToolError, message):
                rcs_content.build(content)

    def test_describe_is_readable(self):
        summary = rcs_content.describe(rcs_content.build(CARD))
        self.assertEqual(summary["card"]["title"], "Sale")
        self.assertEqual(summary["card"]["buttons"], ["Shop"])


class TestRcsSend(SendCase):

    def _token(self, content=CARD, fallback="Sale ends Friday"):
        _, applied = confirm(rcs_send.rcs_send_test, account=ACCOUNT, sender="Shop",
                             content=content, to=["46701234567"],
                             sms_fallback_text=fallback)
        return applied["result"]["test_token"]

    def test_preview_sends_nothing(self):
        result = rcs_send.rcs_preview(ACCOUNT, "Shop", CARD)
        self.assertEqual(result["message"]["card"]["title"], "Sale")
        self.assertIn("get nothing", result["sms_fallback_text"])
        self.assertEqual(self.calls, [])

    def test_test_send_goes_only_to_the_test_number_with_fallback(self):
        self._token()
        (post,) = self.sent("POST", "/rcs/2/messages")
        message = post.json["messages"][0]
        self.assertEqual(message["destinations"], [{"to": "46701234567"}])
        self.assertEqual(message["content"]["type"], "CARD")
        self.assertEqual(message["options"]["smsFailover"], {"text": "Sale ends Friday"})

    def test_campaign_needs_a_test_of_exactly_this_message(self):
        token = self._token()
        changed = dict(CARD, title="Other")
        for kwargs in ({"content": changed, "sms_fallback_text": "Sale ends Friday"},
                       {"content": CARD, "sms_fallback_text": "Other"},
                       {"content": CARD}):
            with self.assertRaisesRegex(ToolError, "test send"):
                rcs_send.rcs_send_campaign(
                    account=ACCOUNT, sender="Shop", audience_kind="tag",
                    audience_value="vip", test_token=token, **kwargs)

    def test_campaign_proposes_then_sends_to_the_audience(self):
        token = self._token()
        self.calls.clear()
        kwargs = dict(account=ACCOUNT, sender="Shop", content=CARD,
                      audience_kind="tag", audience_value="vip", test_token=token,
                      sms_fallback_text="Sale ends Friday", send_at=future())
        proposal = rcs_send.rcs_send_campaign(**kwargs)
        self.assertEqual(proposal["summary"]["recipients"], 3)
        self.assertEqual(proposal["summary"]["message"]["card"]["title"], "Sale")
        self.assertEqual(self.sent("POST", "/rcs/2/messages"), [])
        applied = rcs_send.rcs_send_campaign(
            **kwargs, validate_only=False, checksum=proposal["checksum"])
        (post,) = self.sent("POST", "/rcs/2/messages")
        self.assertEqual(len(post.json["messages"][0]["destinations"]), 3)
        self.assertIn("sendAt", post.json["options"]["schedule"])
        self.assertEqual(applied["result"]["recipients_accepted"], 3)

    def test_audience_change_after_the_proposal_is_refused(self):
        token = self._token()
        kwargs = dict(account=ACCOUNT, sender="Shop", content=CARD,
                      audience_kind="tag", audience_value="vip", test_token=token,
                      sms_fallback_text="Sale ends Friday")
        proposal = rcs_send.rcs_send_campaign(**kwargs)
        self.people.append(person(9))
        self.calls.clear()
        with self.assertRaisesRegex(ToolError, "does not match"):
            rcs_send.rcs_send_campaign(
                **kwargs, validate_only=False, checksum=proposal["checksum"])
        self.assertEqual(self.sent("POST", "/rcs/2/messages"), [])

    def test_capability_check_counts_and_batches(self):
        self.people = [person(n) for n in range(620)]
        batches = []

        def check(call):
            numbers = call.json["phoneNumbers"]
            batches.append(len(numbers))
            return {"capabilityCheckResults": [
                {"phoneNumber": n, "code": "ENABLED" if i % 2 == 0 else "UNREACHABLE"}
                for i, n in enumerate(numbers)]}

        self.routes[("POST", "/rcs/2/capability-check/query")] = check
        result = rcs_send.rcs_check_capability(ACCOUNT, "Shop", "tag", "vip")
        self.assertEqual(batches, [500, 120])
        self.assertEqual(result["numbers_checked"], 620)
        self.assertEqual(result["can_receive_rcs"], 310)
        self.assertEqual(result["cannot_receive_rcs"], 310)
