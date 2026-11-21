"""RCS content, sends, senders, test numbers and operator templates."""

import datetime

from fastmcp.exceptions import ToolError

from messaging_mcp import rcs_content, rcs_delivery
from messaging_mcp.tools import rcs_send, rcs_senders, rcs_templates
from tests.helpers import ACCOUNT, FakeApiTestCase, confirm, response
from tests.tools.sending_test import SendCase, future, person

CARD = {
    "type": "CARD", "title": "Sale", "description": "Ends Friday",
    "image_url": "https://img.example/a.png",
    "buttons": [{"type": "OPEN_URL", "text": "Shop", "url": "https://shop.example"}],
}
PERSONAL = {"type": "TEXT", "text": "Hej {{firstName|kund}}!"}


class TestContent(FakeApiTestCase):

    def test_text_with_every_kind_of_button(self):
        built = rcs_content.build({
            "type": "text", "text": "Hi", "buttons": [
                {"type": "REPLY", "text": "Yes", "postback_data": "YES"},
                {"type": "DIAL_PHONE", "text": "Call", "phone_number": "+46701"},
                {"type": "OPEN_URL", "text": "Web", "url": "https://x.example",
                 "open_in": "webview", "webview_size": "half"},
                {"type": "SHOW_LOCATION", "text": "Map", "latitude": 59.33,
                 "longitude": "18.06", "label": "Store"},
                {"type": "REQUEST_LOCATION", "text": "Where?"},
                {"type": "CREATE_CALENDAR_EVENT", "text": "Save",
                 "start_time": "2026-12-01T18:00:00+01:00",
                 "end_time": "2026-12-01T20:00:00+01:00", "title": "Party"},
            ]})
        buttons = built["suggestions"]
        self.assertEqual(buttons[0], {"type": "REPLY", "text": "Yes", "postbackData": "YES"})
        self.assertEqual(buttons[1]["phoneNumber"], "+46701")
        self.assertEqual(buttons[2]["application"], "WEBVIEW")
        self.assertEqual(buttons[2]["webviewViewMode"], "HALF")
        self.assertEqual((buttons[3]["latitude"], buttons[3]["longitude"]), (59.33, 18.06))
        self.assertEqual(buttons[4]["postbackData"], "Where?")
        self.assertEqual(buttons[5]["startTime"], "2026-12-01T17:00:00Z")
        self.assertEqual(buttons[5]["endTime"], "2026-12-01T19:00:00Z")

    def test_file_with_thumbnail(self):
        built = rcs_content.build({"type": "FILE", "file_url": "https://x.example/v.mp4",
                                   "thumbnail_url": "https://x.example/t.jpg"})
        self.assertEqual(built, {"type": "FILE", "file": {"url": "https://x.example/v.mp4"},
                                 "thumbnail": {"url": "https://x.example/t.jpg"}})

    def test_card_puts_its_buttons_on_the_card(self):
        built = rcs_content.build(dict(CARD, orientation="HORIZONTAL", image_side="right"))
        self.assertEqual((built["orientation"], built["alignment"]), ("HORIZONTAL", "RIGHT"))
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

    def test_operator_template(self):
        built = rcs_content.build({"type": "TEMPLATE", "name": "black_week",
                                   "parameters": {"code": "BW20"}})
        self.assertEqual(built, {"type": "PROVIDER_TEMPLATE", "templateId": "black_week",
                                 "parameters": [{"name": "code", "value": "BW20"}]})

    def test_refuses_what_rcs_would_reject(self):
        long_cards = [CARD] * 11
        cases = [
            ({"type": "TEXT"}, "text is required"),
            ({"type": "TEXT", "text": "x" * 3073}, "at most 3072"),
            ({"type": "VIDEO"}, "TEXT, FILE, CARD, CAROUSEL or TEMPLATE"),
            ({"type": "FILE", "file_url": "http://x/a.pdf"}, "https://"),
            ({"type": "CARD"}, "needs a title"),
            ({"type": "CARD", "title": "t", "image_url": "https://x/a.png",
              "image_height": "HUGE"}, "image_height"),
            ({"type": "CARD", "title": "t", "image_side": "UP"}, "image_side"),
            ({"type": "CAROUSEL", "cards": [CARD]}, "between 2 and 10"),
            ({"type": "CAROUSEL", "cards": long_cards}, "between 2 and 10"),
            ({"type": "TEXT", "text": "x",
              "buttons": [{"type": "REPLY", "text": "y" * 26}]}, "at most 25"),
            ({"type": "TEXT", "text": "x", "buttons": [{"type": "MAP", "text": "y"}]},
             "button type"),
            ({"type": "TEXT", "text": "x",
              "buttons": [{"type": "OPEN_URL", "text": "y"}]}, "url is required"),
            ({"type": "TEXT", "text": "x", "buttons": [
                {"type": "OPEN_URL", "text": "y", "url": "https://a", "open_in": "APP"}]},
             "open_in"),
            ({"type": "TEXT", "text": "x",
              "buttons": [{"type": "DIAL_PHONE", "text": "y", "phone_number": "abc"}]},
             "phone_number"),
            ({"type": "TEXT", "text": "x", "buttons": [
                {"type": "SHOW_LOCATION", "text": "y", "latitude": 91, "longitude": 0}]},
             "latitude"),
            ({"type": "TEXT", "text": "x", "buttons": [
                {"type": "CREATE_CALENDAR_EVENT", "text": "y", "title": "t",
                 "start_time": "2026-12-01T18:00:00",
                 "end_time": "2026-12-01T19:00:00+01:00"}]}, "time zone"),
            ({"type": "TEXT", "text": "x", "buttons": [
                {"type": "CREATE_CALENDAR_EVENT", "text": "y", "title": "t",
                 "start_time": "2026-12-01T18:00:00+01:00",
                 "end_time": "2026-12-01T17:00:00+01:00"}]}, "after start_time"),
            ({"type": "CARD", "title": "t",
              "buttons": [{"type": "REPLY", "text": str(n)} for n in range(5)]},
             "at most 4 buttons"),
            ({"type": "TEXT", "text": "x",
              "buttons": [{"type": "REPLY", "text": str(n)} for n in range(12)]},
             "at most 11 buttons"),
            ({"type": "TEMPLATE", "name": "x" * 21}, "at most 20"),
            ({"type": "TEMPLATE", "name": "t", "parameters": ["a"]}, "name: value"),
            ({"type": "TEMPLATE", "name": "t",
              "buttons": [{"type": "REPLY", "text": "y"}]}, "own buttons"),
        ]
        for content, message in cases:
            with self.assertRaisesRegex(ToolError, message, msg=str(content)):
                rcs_content.build(content)

    def test_template_content_rules(self):
        pdf = rcs_content.build_template({"type": "TEXT_WITH_PDF", "text": "Terms",
                                          "pdf_url": "https://x.example/t.pdf",
                                          "pdf_first": True})
        self.assertEqual(pdf["messageOrder"], "PDF_AT_TOP")
        with self.assertRaisesRegex(ToolError, "at most 2500"):
            rcs_content.build_template({"type": "TEXT", "text": "x" * 2501})
        with self.assertRaisesRegex(ToolError, "TEXT, TEXT_WITH_PDF, CARD or CAROUSEL"):
            rcs_content.build_template({"type": "FILE", "file_url": "https://x"})

    def test_placeholders_are_found_rendered_and_checked(self):
        content = {"type": "TEXT", "text": "Hej {{firstName|kund}} {{ lastName }}"}
        self.assertEqual(rcs_content.placeholders_in(content),
                         {"firstName": "kund", "lastName": None})
        self.assertEqual(
            rcs_content.render(content, {"firstName": "Ada", "lastName": "L"})["text"],
            "Hej Ada L")
        self.assertEqual(rcs_content.render(content, {})["text"], "Hej kund ")
        with self.assertRaisesRegex(ToolError, "Unknown placeholder"):
            rcs_content.placeholders_in({"text": "{{city}}"})

    def test_describe_is_readable(self):
        summary = rcs_content.describe(rcs_content.build(CARD))
        self.assertEqual(summary["card"]["title"], "Sale")
        self.assertEqual(summary["card"]["buttons"], ["Shop (https://shop.example)"])


class TestDelivery(FakeApiTestCase):

    def test_send_window_is_converted_from_swedish_time(self):
        winter = rcs_delivery.send_window(
            {"from": "09:00", "to": "18:30"}, datetime.date(2026, 12, 1))
        self.assertEqual(winter["days"][0], "MONDAY")
        self.assertEqual(len(winter["days"]), 5)
        self.assertEqual(winter["from"], {"hour": 8, "minute": 0})
        self.assertEqual(winter["to"], {"hour": 17, "minute": 30})
        summer = rcs_delivery.send_window(
            {"days": ["saturday"], "from": "09:00", "to": "12:00"},
            datetime.date(2026, 7, 1))
        self.assertEqual((summer["days"], summer["from"]["hour"]), (["SATURDAY"], 7))

    def test_send_window_rules(self):
        day = datetime.date(2026, 12, 1)
        for window, message in [
            ({"from": "9", "to": "18:00"}, "time such as"),
            ({"from": "09:00", "to": "09:30"}, "at least one hour"),
            ({"days": ["MÅNDAG"], "from": "09:00", "to": "18:00"}, "weekday names"),
            ({"from": "00:30", "to": "05:00"}, "crosses midnight"),
        ]:
            with self.assertRaisesRegex(ToolError, message):
                rcs_delivery.send_window(window, day)
        self.assertIsNone(rcs_delivery.send_window(None, day))

    def test_validity_speed_and_tracking(self):
        day = datetime.date(2026, 12, 1)
        self.assertEqual(rcs_delivery.message_options(6, None, day),
                         {"validityPeriod": {"amount": 6, "timeUnit": "HOURS"}})
        with self.assertRaisesRegex(ToolError, "between 1 and 48"):
            rcs_delivery.message_options(49, None, day)
        options = rcs_delivery.request_options(1000, True)
        self.assertEqual(options["speed"],
                         {"sendingSpeedLimit": {"amount": 1000, "timeUnit": "HOUR"}})
        self.assertEqual(options["tracking"], {"shortenUrl": True, "trackClicks": True})
        self.assertEqual(rcs_delivery.request_options(None, False),
                         {"speed": {}, "tracking": None})
        with self.assertRaisesRegex(ToolError, "at least 1"):
            rcs_delivery.request_options(0, False)


class RcsCase(SendCase):

    def setUp(self):
        super().setUp()
        self.sender_status = "LAUNCHED"
        self.routes[("GET", "/rcs/1/senders/Shop")] = lambda c: {
            "senderName": "Shop", "senderStatus": self.sender_status}

    def token(self, content=CARD, fallback="Sale ends Friday"):
        _, applied = confirm(rcs_send.rcs_send_test, account=ACCOUNT, sender="Shop",
                             content=content, to=["46701234567"],
                             sms_fallback_text=fallback)
        return applied["result"]["test_token"]

    def campaign(self, token, **extra):
        kwargs = dict(account=ACCOUNT, sender="Shop", content=CARD,
                      audience_kind="tag", audience_value="vip", test_token=token,
                      sms_fallback_text="Sale ends Friday")
        kwargs.update(extra)
        return kwargs


class TestRcsSend(RcsCase):

    def test_preview_sends_nothing_and_shows_the_sender_status(self):
        result = rcs_send.rcs_preview(ACCOUNT, "Shop", PERSONAL)
        self.assertEqual(result["message"]["text"], "Hej Test!")
        self.assertEqual(result["sender_status"], "LAUNCHED")
        self.assertEqual(result["personal_fields"], {"firstName": "default 'kund'"})
        self.assertIn("get nothing", result["sms_fallback_text"])
        self.assertEqual(self.sent("POST", "/rcs/2/messages"), [])

    def test_test_send_goes_only_to_the_test_number_with_fallback(self):
        self.token()
        (post,) = self.sent("POST", "/rcs/2/messages")
        message = post.json["messages"][0]
        self.assertEqual(message["destinations"], [{"to": "46701234567"}])
        self.assertEqual(message["content"]["type"], "CARD")
        self.assertEqual(message["options"]["smsFailover"], {"text": "Sale ends Friday"})

    def test_campaign_needs_a_test_of_exactly_this_message(self):
        token = self.token()
        for extra in ({"content": dict(CARD, title="Other")},
                      {"sms_fallback_text": "Other"},
                      {"sms_fallback_text": None},
                      {"sender": "Other"}):
            with self.assertRaisesRegex(ToolError, "test send"):
                rcs_send.rcs_send_campaign(**self.campaign(token, **extra))

    def test_campaign_proposes_then_sends_with_delivery_settings(self):
        token = self.token()
        self.calls.clear()
        kwargs = self.campaign(
            token, send_at=future(), validity_hours=6, max_per_hour=500,
            track_clicks=True, send_window={"from": "09:00", "to": "18:00"})
        proposal = rcs_send.rcs_send_campaign(**kwargs)
        summary = proposal["summary"]
        self.assertEqual(summary["recipients"], 3)
        self.assertEqual(summary["message"]["card"]["title"], "Sale")
        self.assertEqual(summary["delivery"]["max_per_hour"], 500)
        self.assertEqual(summary["warnings"], [])
        self.assertEqual(self.sent("POST", "/rcs/2/messages"), [])
        applied = rcs_send.rcs_send_campaign(
            **kwargs, validate_only=False, checksum=proposal["checksum"])
        (post,) = self.sent("POST", "/rcs/2/messages")
        (message,) = post.json["messages"]
        self.assertEqual(len(message["destinations"]), 3)
        self.assertEqual(message["options"]["validityPeriod"],
                         {"amount": 6, "timeUnit": "HOURS"})
        self.assertIn("deliveryTimeWindow", message["options"])
        schedule = post.json["options"]["schedule"]
        self.assertIn("sendAt", schedule)
        self.assertEqual(schedule["sendingSpeedLimit"], {"amount": 500, "timeUnit": "HOUR"})
        self.assertEqual(post.json["options"]["tracking"],
                         {"shortenUrl": True, "trackClicks": True})
        self.assertEqual(applied["result"]["recipients_accepted"], 3)

    def test_changed_delivery_settings_invalidate_the_proposal(self):
        token = self.token()
        proposal = rcs_send.rcs_send_campaign(**self.campaign(token, max_per_hour=100))
        with self.assertRaisesRegex(ToolError, "does not match"):
            rcs_send.rcs_send_campaign(**self.campaign(token, max_per_hour=1000),
                                       validate_only=False,
                                       checksum=proposal["checksum"])
        self.assertEqual(len(self.sent("POST", "/rcs/2/messages")), 1)

    def test_personal_campaign_sends_one_message_per_recipient(self):
        token = self.token(content=PERSONAL, fallback="Hej {{firstName|kund}}")
        self.people = [person(1), person(2)]
        self.people[1]["firstName"] = None
        self.calls.clear()
        kwargs = self.campaign(token, content=PERSONAL,
                               sms_fallback_text="Hej {{firstName|kund}}")
        proposal = rcs_send.rcs_send_campaign(**kwargs)
        self.assertEqual(proposal["summary"]["message"]["text"], "Hej First1!")
        self.assertEqual(proposal["summary"]["shown_as_for"], "46700000001")
        rcs_send.rcs_send_campaign(**kwargs, validate_only=False,
                                   checksum=proposal["checksum"])
        (post,) = self.sent("POST", "/rcs/2/messages")
        texts = [(m["destinations"], m["content"]["text"],
                  m["options"]["smsFailover"]["text"]) for m in post.json["messages"]]
        self.assertEqual(texts, [
            ([{"to": "46700000001"}], "Hej First1!", "Hej First1"),
            ([{"to": "46700000002"}], "Hej kund!", "Hej kund")])

    def test_personal_field_without_default_is_refused_when_someone_lacks_it(self):
        content = {"type": "TEXT", "text": "Hej {{firstName}}"}
        token = self.token(content=content)
        self.people[0]["firstName"] = None
        with self.assertRaisesRegex(ToolError, "1 recipients have no firstName"):
            rcs_send.rcs_send_campaign(**self.campaign(token, content=content))

    def test_too_long_text_for_one_recipient_is_caught_before_sending(self):
        content = {"type": "TEXT", "text": "x" * 3060 + "{{firstName|a}}"}
        token = self.token(content=content)
        self.people[0]["firstName"] = "Bartholomew-Maximilian"
        with self.assertRaisesRegex(ToolError, "at most 3072"):
            rcs_send.rcs_send_campaign(**self.campaign(token, content=content))

    def test_warns_when_the_sender_is_not_launched_or_has_no_fallback(self):
        token = self.token(fallback=None)
        self.sender_status = "READY_FOR_TESTING"
        warnings = rcs_send.rcs_send_campaign(
            **self.campaign(token, sms_fallback_text=None))["summary"]["warnings"]
        self.assertIn("READY_FOR_TESTING", warnings[0])
        self.assertIn("get nothing", warnings[1])

    def test_sender_status_failure_does_not_block(self):
        self.routes[("GET", "/rcs/1/senders/Shop")] = response({"x": 1}, 403)
        result = rcs_send.rcs_preview(ACCOUNT, "Shop", CARD)
        self.assertEqual(result["sender_status"], "unknown")

    def test_audience_change_after_the_proposal_is_refused(self):
        token = self.token()
        proposal = rcs_send.rcs_send_campaign(**self.campaign(token))
        self.people.append(person(9))
        self.calls.clear()
        with self.assertRaisesRegex(ToolError, "does not match"):
            rcs_send.rcs_send_campaign(**self.campaign(token), validate_only=False,
                                       checksum=proposal["checksum"])
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
        self.assertEqual(result["can_receive_rcs"], 310)
        self.assertEqual(result["cannot_receive_rcs"], 310)


class TestSenders(FakeApiTestCase):

    def setUp(self):
        super().setUp()
        self.numbers = [{"testNumber": "46700000001", "status": "APPROVED",
                         "primary": True}]
        self.routes.update({
            ("GET", "/rcs/1/senders"): lambda c: {
                "results": [{"senderName": f"S{c.params['page']}",
                             "senderStatus": "LAUNCHED", "brand": {"brandName": "B"}}],
                "paging": {"totalPages": 2}},
            ("GET", "/rcs/1/senders/Shop"): {"senderName": "Shop",
                                            "senderStatus": "PENDING_LAUNCH"},
            ("GET", "/rcs/1/senders/Shop/launch-status"): {
                "coverage": [{"countryCode": "SE", "providers": [
                    {"name": "Telia", "status": "REJECTED", "rejectionReason": "Logo"}]}]},
            ("GET", "/rcs/1/senders/Shop/test-numbers"): lambda c: {
                "results": self.numbers, "paging": {"totalPages": 1}},
            ("POST", "/rcs/1/senders/Shop/test-numbers"): {
                "testNumber": "46700000002", "status": "PENDING"},
        })

    def test_lists_senders_across_pages(self):
        senders = rcs_senders.rcs_list_senders(ACCOUNT)["senders"]
        self.assertEqual([s["sender"] for s in senders], ["S0", "S1"])
        self.assertEqual(senders[0]["brand"], "B")

    def test_status_shows_each_operator(self):
        status = rcs_senders.rcs_sender_status(ACCOUNT, "Shop")
        self.assertEqual(status["status"], "PENDING_LAUNCH")
        self.assertEqual(status["coverage"][0]["operators"][0],
                         {"operator": "Telia", "status": "REJECTED",
                          "rejection_reason": "Logo"})

    def test_adds_a_test_number_only_after_confirmation(self):
        proposal = rcs_senders.rcs_add_test_number(ACCOUNT, "Shop", "+46 70 000 00 02")
        self.assertEqual(proposal["summary"]["adds_test_number"], "46700000002")
        self.assertEqual(self.sent("POST", "/rcs/1/senders/Shop/test-numbers"), [])
        confirm(rcs_senders.rcs_add_test_number, account=ACCOUNT, sender="Shop",
                phone_number="+46 70 000 00 02")
        (post,) = self.sent("POST", "/rcs/1/senders/Shop/test-numbers")
        self.assertEqual(post.json, {"testNumber": "46700000002", "primary": False})

    def test_refuses_a_number_that_is_already_there(self):
        with self.assertRaisesRegex(ToolError, "already a test number"):
            rcs_senders.rcs_add_test_number(ACCOUNT, "Shop", "46700000001")

    def test_proposal_breaks_when_the_list_changes(self):
        proposal = rcs_senders.rcs_add_test_number(ACCOUNT, "Shop", "46700000002")
        self.numbers = self.numbers + [{"testNumber": "46700000003"}]
        with self.assertRaisesRegex(ToolError, "does not match"):
            rcs_senders.rcs_add_test_number(ACCOUNT, "Shop", "46700000002",
                                            validate_only=False,
                                            checksum=proposal["checksum"])


class TestTemplates(FakeApiTestCase):

    def setUp(self):
        super().setUp()
        self.stored = {"name": "bw", "type": "TEXT", "status": "APPROVED",
                       "content": {"type": "TEXT", "text": "Old"}}
        base = "/rcs/1/senders/Shop/templates"
        self.routes.update({
            ("GET", base): [{"name": "bw", "type": "TEXT", "status": "APPROVED",
                             "lastUpdate": "2026-11-01"}],
            ("POST", base): {"name": "new", "status": "PENDING"},
            ("GET", base + "/bw"): lambda c: self.stored,
            ("GET", base + "/bw/status"): {"status": "APPROVED", "coverage": [
                {"countryCode": "SE", "providers": [
                    {"providerName": "Telia", "status": "APPROVED"}]}]},
            ("PUT", base + "/bw"): {"name": "bw", "status": "PENDING"},
            ("PATCH", base + "/bw/register"): {"name": "bw", "status": "PENDING"},
            ("GET", "/rcs/1/templates/template-providers"): {"providers": ["Telia", "Tele2"]},
        })

    def test_list_get_and_operators(self):
        self.assertEqual(rcs_templates.rcs_templates_list(ACCOUNT, "Shop")["templates"][0]
                         ["status"], "APPROVED")
        got = rcs_templates.rcs_templates_get(ACCOUNT, "Shop", "bw")
        self.assertEqual(got["content"], {"type": "TEXT", "text": "Old"})
        self.assertEqual(got["approval"][0]["operators"][0]["operator"], "Telia")
        self.assertEqual(rcs_templates.rcs_templates_list_operators(
            ACCOUNT, "Shop", "SE")["operators"], ["Telia", "Tele2"])
        self.assertEqual(self.calls[-1].params, {"sender": "Shop", "countryCode": "SE"})

    def test_create_proposes_then_posts(self):
        content = {"type": "TEXT", "text": "Hej",
                   "buttons": [{"type": "REPLY", "text": "Ja"}]}
        proposal = rcs_templates.rcs_templates_create(ACCOUNT, "Shop", "new", content,
                                                      ["Telia"])
        self.assertEqual(proposal["summary"]["sent_for_review_to"], ["Telia"])
        self.assertEqual(self.sent("POST", "/rcs/1/senders/Shop/templates"), [])
        confirm(rcs_templates.rcs_templates_create, account=ACCOUNT, sender="Shop",
                name="new", content=content, operators=["Telia"])
        (post,) = self.sent("POST", "/rcs/1/senders/Shop/templates")
        self.assertEqual(post.json["templateName"], "new")
        self.assertEqual(post.json["providers"], ["Telia"])
        self.assertEqual(post.json["content"]["suggestions"][0]["text"], "Ja")

    def test_create_refusals(self):
        content = {"type": "TEXT", "text": "Hej"}
        with self.assertRaisesRegex(ToolError, "already exists"):
            rcs_templates.rcs_templates_create(ACCOUNT, "Shop", "BW", content, ["Telia"])
        with self.assertRaisesRegex(ToolError, "at least one operator"):
            rcs_templates.rcs_templates_create(ACCOUNT, "Shop", "x", content, [])
        with self.assertRaisesRegex(ToolError, "at most 20"):
            rcs_templates.rcs_templates_create(ACCOUNT, "Shop", "x" * 21, content, ["T"])

    def test_update_shows_old_and_new_and_puts_the_content(self):
        proposal = rcs_templates.rcs_templates_update(
            ACCOUNT, "Shop", "bw", {"type": "TEXT", "text": "New"})
        self.assertEqual(proposal["summary"]["current_content"]["text"], "Old")
        self.assertEqual(proposal["summary"]["new_content"]["text"], "New")
        self.assertEqual(self.sent("PUT", "/rcs/1/senders/Shop/templates/bw"), [])
        confirm(rcs_templates.rcs_templates_update, account=ACCOUNT, sender="Shop",
                name="bw", content={"type": "TEXT", "text": "New"})
        (put,) = self.sent("PUT", "/rcs/1/senders/Shop/templates/bw")
        self.assertEqual(put.json, {"type": "TEXT", "text": "New"})

    def test_update_refuses_no_change_and_a_changed_template(self):
        with self.assertRaisesRegex(ToolError, "Nothing to change"):
            rcs_templates.rcs_templates_update(ACCOUNT, "Shop", "bw",
                                               {"type": "TEXT", "text": "Old"})
        proposal = rcs_templates.rcs_templates_update(
            ACCOUNT, "Shop", "bw", {"type": "TEXT", "text": "New"})
        self.stored = dict(self.stored, content={"type": "TEXT", "text": "Other"})
        with self.assertRaisesRegex(ToolError, "does not match"):
            rcs_templates.rcs_templates_update(
                ACCOUNT, "Shop", "bw", {"type": "TEXT", "text": "New"},
                validate_only=False, checksum=proposal["checksum"])
        self.assertEqual(self.sent("PUT", "/rcs/1/senders/Shop/templates/bw"), [])

    def test_register_shows_where_it_already_is(self):
        proposal = rcs_templates.rcs_templates_register(ACCOUNT, "Shop", "bw", ["Tele2"])
        self.assertEqual(proposal["summary"]["already_registered_with"], ["Telia"])
        confirm(rcs_templates.rcs_templates_register, account=ACCOUNT, sender="Shop",
                name="bw", operators=["Tele2"])
        (patch,) = self.sent("PATCH", "/rcs/1/senders/Shop/templates/bw/register")
        self.assertEqual(patch.json, {"providers": ["Tele2"]})
