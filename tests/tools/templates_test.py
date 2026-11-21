"""Email template tools."""

from fastmcp.exceptions import ToolError

from messaging_mcp.tools import templates as tt
from tests.helpers import ACCOUNT, OTHER, FakeApiTestCase, confirm

HTML = "<html><body>Hi {{firstName}} <a href='{{unsubscribe}}'>Unsubscribe</a></body></html>"
STORED = {
    "id": 7, "name": "Spring", "from": "Shop <news@shop.example>",
    "replyTo": "reply@shop.example", "subject": "Hello", "preheader": "Pre",
    "html": HTML, "attachments": [], "updatedAt": "2026-11-01T10:00:00Z",
}


class TemplateCase(FakeApiTestCase):

    def setUp(self):
        super().setUp()
        self.routes[("GET", "/email/1/templates")] = {
            "results": [{"id": 7, "name": "Spring", "updatedAt": "u"}],
            "paging": {"page": 0, "size": 100, "totalPages": 1, "totalResults": 1},
        }
        self.routes[("GET", "/email/1/templates/7")] = dict(STORED)
        self.routes[("POST", "/email/1/templates")] = {"id": 99}
        self.routes[("PATCH", "/email/1/templates/7")] = {"id": 7}


class TestRead(TemplateCase):

    def test_list_returns_ids_names_and_paging(self):
        result = tt.templates_list(ACCOUNT, page=0, page_size=10)
        self.assertEqual(result["templates"][0]["template_id"], 7)
        self.assertEqual(result["templates"][0]["name"], "Spring")
        self.assertEqual(self.calls[0].params, {"page": 0, "size": 10})
        with self.assertRaisesRegex(ToolError, "page_size"):
            tt.templates_list(ACCOUNT, page_size=500)

    def test_get_returns_sender_subject_and_html(self):
        result = tt.templates_get(ACCOUNT, 7)
        self.assertEqual(result["sender"], "Shop <news@shop.example>")
        self.assertEqual(result["subject"], "Hello")
        self.assertEqual(result["html"], HTML)
        self.assertTrue(result["html_summary"]["has_unsubscribe_link"])
        self.assertNotIn("html", tt.templates_get(ACCOUNT, 7, include_html=False))


class TestCreate(TemplateCase):

    def test_proposal_creates_nothing(self):
        proposal = tt.templates_create(ACCOUNT, "New", HTML, subject="S")
        self.assertEqual(proposal["status"], "PROPOSED")
        self.assertEqual(self.sent("POST", "/email/1/templates"), [])
        self.assertEqual(proposal["summary"]["creates"]["name"], "New")
        self.assertEqual(proposal["summary"]["warnings"], [])

    def test_confirm_posts_the_form_with_api_field_names(self):
        _, applied = confirm(
            tt.templates_create, account=ACCOUNT, name="New", html=HTML,
            subject="S", sender="A <a@shop.example>", reply_to="r@shop.example",
        )
        self.assertEqual(applied["result"], {"template_id": 99})
        (post,) = self.sent("POST", "/email/1/templates")
        self.assertEqual(
            post.form,
            {"name": "New", "html": HTML, "subject": "S",
             "from": "A <a@shop.example>", "replyTo": "r@shop.example"},
        )
        self.assertIsNone(post.json)

    def test_refuses_a_name_already_in_use_and_overlong_texts(self):
        with self.assertRaisesRegex(ToolError, "already exists"):
            tt.templates_create(ACCOUNT, "spring", HTML)
        with self.assertRaisesRegex(ToolError, "subject"):
            tt.templates_create(ACCOUNT, "New", HTML, subject="x" * 201)
        with self.assertRaisesRegex(ToolError, "preheader"):
            tt.templates_create(ACCOUNT, "New", HTML, preheader="x" * 76)
        with self.assertRaisesRegex(ToolError, "required"):
            tt.templates_create(ACCOUNT, "New", "  ")

    def test_warns_when_the_html_has_no_unsubscribe_link(self):
        proposal = tt.templates_create(ACCOUNT, "New", "<p>Hi</p>")
        self.assertIn("unsubscribe", proposal["summary"]["warnings"][0])

    def test_changed_html_invalidates_the_checksum(self):
        proposal = tt.templates_create(ACCOUNT, "New", HTML)
        with self.assertRaisesRegex(ToolError, "does not match"):
            tt.templates_create(
                ACCOUNT, "New", HTML + " ", validate_only=False,
                checksum=proposal["checksum"],
            )
        self.assertEqual(self.sent("POST", "/email/1/templates"), [])


class TestUpdate(TemplateCase):

    def test_proposal_shows_current_and_new_value_and_changes_nothing(self):
        proposal = tt.templates_update(ACCOUNT, 7, subject="New subject")
        self.assertEqual(
            proposal["summary"]["changes"],
            [{"field": "subject", "current": "Hello", "new": "New subject"}],
        )
        self.assertEqual(proposal["summary"]["template_name"], "Spring")
        self.assertEqual(self.sent("PATCH", "/email/1/templates/7"), [])

    def test_confirm_patches_only_what_changed(self):
        confirm(tt.templates_update, account=ACCOUNT, template_id=7,
                subject="New subject", name="Spring")
        (patch,) = self.sent("PATCH", "/email/1/templates/7")
        self.assertEqual(patch.form, {"subject": "New subject"})

    def test_html_change_is_summarised_not_dumped(self):
        proposal = tt.templates_update(ACCOUNT, 7, html="<p>Short</p>")
        change = proposal["summary"]["changes"][0]
        self.assertEqual(change["current"]["characters"], len(HTML))
        self.assertEqual(change["new"]["characters"], len("<p>Short</p>"))
        self.assertTrue(proposal["summary"]["warnings"])

    def test_refuses_when_nothing_differs(self):
        with self.assertRaisesRegex(ToolError, "Nothing to change"):
            tt.templates_update(ACCOUNT, 7, subject="Hello")

    def test_refuses_when_the_template_changed_after_the_proposal(self):
        proposal = tt.templates_update(ACCOUNT, 7, subject="New subject")
        changed = dict(STORED, updatedAt="2026-11-02T10:00:00Z", preheader="X")
        self.routes[("GET", "/email/1/templates/7")] = changed
        with self.assertRaisesRegex(ToolError, "does not match"):
            tt.templates_update(
                ACCOUNT, 7, subject="New subject", validate_only=False,
                checksum=proposal["checksum"],
            )
        self.assertEqual(self.sent("PATCH", "/email/1/templates/7"), [])


class TestCopy(TemplateCase):

    def test_copy_in_the_same_account_keeps_everything_but_the_name(self):
        _, applied = confirm(tt.templates_copy, account=ACCOUNT, template_id=7,
                             new_name="Spring copy")
        (post,) = self.sent("POST", "/email/1/templates")
        self.assertEqual(post.host, "aaaaa.api.infobip.com")
        self.assertEqual(post.form["name"], "Spring copy")
        self.assertEqual(post.form["html"], HTML)
        self.assertEqual(post.form["from"], "Shop <news@shop.example>")
        self.assertEqual(applied["result"], {"template_id": 99, "account": ACCOUNT})
        self.assertEqual(self.sent("PATCH", "/email/1/templates/7"), [])

    def test_copy_to_another_account_drops_the_sender_and_says_so(self):
        proposal, _ = confirm(tt.templates_copy, account=ACCOUNT, template_id=7,
                              new_name="Moved", to_account=OTHER)
        (post,) = self.sent("POST", "/email/1/templates")
        self.assertEqual(post.host, "bbbbb.api.infobip.com")
        self.assertEqual(post.headers["Authorization"], "App key-2")
        self.assertNotIn("from", post.form)
        self.assertNotIn("replyTo", post.form)
        self.assertEqual(proposal["summary"]["not_copied"], ["sender", "reply_to"])

    def test_says_when_attachments_are_not_copied(self):
        self.routes[("GET", "/email/1/templates/7")] = dict(
            STORED, attachments=[{"id": 1}]
        )
        proposal = tt.templates_copy(ACCOUNT, 7, "Copy")
        self.assertEqual(proposal["summary"]["not_copied"], ["attachments"])

    def test_refuses_a_name_already_in_use_in_the_target(self):
        with self.assertRaisesRegex(ToolError, "already exists"):
            tt.templates_copy(ACCOUNT, 7, "Spring")
