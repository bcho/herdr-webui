"""Browser regressions for the static SRE landing mockup (no network).

Run: python3 -m unittest test_mockup -v
"""
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGE = (Path(__file__).resolve().parent / "docs/mockups/sre-landing.html").as_uri()


class MockupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def open(self, filename="sre-landing.html", **kw):
        page = self.browser.new_page(**kw)
        self.addCleanup(page.close)
        self.requests = []
        page.on("request", lambda r: self.requests.append(r.url))
        page.goto((Path(__file__).resolve().parent / "docs/mockups" / filename).as_uri())
        return page

    def test_healthy_prompts_upgrades_chat_and_no_network(self):
        p = self.open()
        self.assertIn("MOCKUP", p.inner_text(".banner"))
        self.assertEqual(p.locator("#prompts button").all_text_contents(), ["Upgrade Herdr", "Upgrade Web UI", "Show recent errors"])
        self.assertIn("bedrock opus 5.5 out of quota", p.inner_text("#incidents"))
        self.assertTrue(p.is_enabled("#open"))
        self.assertLess(p.locator("#open").bounding_box()["height"], 60)
        p.click("#open")
        self.assertTrue(p.is_visible("#preview"))
        p.fill("#input", "<b>x</b>")
        p.press("#input", "Shift+Enter")
        self.assertEqual(p.locator(".msg.user").count(), 0)
        p.press("#input", "Enter")
        self.assertEqual(p.locator(".msg.user").inner_text().split("\n")[-1], "<b>x</b>")
        self.assertEqual(p.locator(".msg.user b").count(), 0)
        for chip, title in (("Upgrade Herdr", "Upgrade Herdr independently"), ("Upgrade Web UI", "Upgrade Web UI independently")):
            p.get_by_role("button", name=chip, exact=True).click()
            p.press("#input", "Enter")
            self.assertEqual(p.inner_text("#proposal-title"), title)
            self.assertIn("Awaiting human approval", p.inner_text("#repair-status"))
        self.assertEqual([u for u in self.requests if not u.startswith("file:")], [])

    def test_down_scenarios_have_matching_causes_incidents_and_approval(self):
        p = self.open()
        prior_incidents = p.inner_text("#incidents")
        for scenario, cause, proposal in (("herdr", "invalid shell_mode", "Restore validated Herdr config"),
                                          ("webui", "missing errors.js", "Roll back Web UI")):
            p.select_option("#scenario", scenario)
            self.assertTrue(p.is_disabled("#open"))
            self.assertEqual(p.locator(".svc.down").count(), 1)
            self.assertIn(cause, p.inner_text("#log") + p.inner_text("#incidents"))
            self.assertIn(proposal, p.inner_text("#proposal-title"))
            self.assertIn("Awaiting human approval", p.inner_text("#repair-status"))
            self.assertNotEqual(p.inner_text("#incidents"), prior_incidents)
            prior_incidents = p.inner_text("#incidents")
            p.click("#approve")
            self.assertIn("no command was executed", p.inner_text("#repair-status"))
        p.select_option("#scenario", "herdr")
        self.assertIn("Awaiting human approval", p.inner_text("#repair-status"))
        self.assertFalse(p.is_disabled("#approve"))
        self.assertEqual([u for u in self.requests if not u.startswith("file:")], [])

    def test_llm_recovery_is_local_default_or_recent_user_order_not_fixed_priority(self):
        p = self.open()
        p.select_option("#scenario", "llm")
        self.assertIn("Reset to configured default", p.inner_text("#proposal-title"))
        self.assertIn("bedrock opus 5.5 out of quota", p.inner_text("#incidents"))
        self.assertTrue(p.is_disabled("#input"))
        self.assertTrue(p.is_disabled("#send"))
        self.assertIn("cached status", p.inner_text("#chat-state"))
        values = p.locator("#recent-models option").evaluate_all("els => els.filter(e => e.value).map(el => el.value)")
        self.assertEqual(values, ["openai-sub/reasoning", "copilot-proxy/reasoning", "bedrock/opus-5.5", "azure-foundry-sub/reasoning", "github-copilot/fast"])
        p.select_option("#recent-models", "openai-sub/reasoning")
        self.assertIn("openai-sub/reasoning", p.inner_text("#recovery-target"))
        p.click("#approve")
        self.assertIn("Approved", p.inner_text("#repair-status"))
        p.select_option("#recent-models", "azure-foundry-sub/reasoning")
        self.assertIn("Awaiting human approval", p.inner_text("#repair-status"))
        p.click("#reset-default")
        self.assertIn("Reset to configured default", p.inner_text("#proposal-title"))
        self.assertIn("copilot-proxy/reasoning", p.inner_text("#recovery-target"))
        self.assertEqual([u for u in self.requests if not u.startswith("file:")], [])

    def test_model_switch_current_and_all_compatible_agents_are_simulated(self):
        p = self.open("model-switch.html")
        self.assertIn("DESIGN MOCKUP", p.inner_text(".banner"))
        self.assertIn("ui-agent", p.inner_text("#pane-title"))
        self.assertIn("Ready", p.inner_text("#plan"))
        p.select_option("#model", "bedrock/opus-5.5")
        self.assertIn("bedrock/opus-5.5", p.inner_text("#plan"))
        self.assertTrue(p.is_disabled("#apply"))
        p.check("#confirm")
        p.click("#apply")
        self.assertIn("No live model or pane changed", p.inner_text("#result"))
        p.select_option("#scope", "all")
        self.assertEqual(p.locator("#plan tr").count(), 4)
        self.assertIn("Deferred: wait until idle", p.inner_text("#plan"))
        self.assertIn("Skipped: no verified runtime adapter", p.inner_text("#plan"))
        self.assertIn("Skipped: plain shell", p.inner_text("#plan"))
        p.check("#confirm")
        p.select_option("#model", "openai-sub/reasoning")
        self.assertFalse(p.is_checked("#confirm"))
        self.assertTrue(p.is_disabled("#apply"))
        p.click("#default")
        self.assertIn("copilot-proxy/reasoning", p.inner_text("#plan"))
        p.select_option("#scope", "current")
        p.click('[data-pane="p2"]')
        p.check("#confirm")
        self.assertTrue(p.is_disabled("#apply"))
        self.assertEqual([u for u in self.requests if not u.startswith("file:")], [])

    def test_model_recent_choices_follow_usage_not_provider_priority(self):
        p = self.open("model-switch.html")
        values = p.locator("#model option").evaluate_all("els => els.map(el => el.value)")
        self.assertEqual(values[:3], ["openai-sub/reasoning", "copilot-proxy/reasoning", "bedrock/opus-5.5"])
        self.assertEqual(len(values), 5)

    def test_mobile_no_horizontal_overflow(self):
        for name in ("sre-landing.html", "model-switch.html"):
            p = self.open(name, viewport={"width": 360, "height": 740})
            self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth"), 360)
            if name == "sre-landing.html":
                for scenario in ("herdr", "webui", "llm"):
                    p.select_option("#scenario", scenario)
                    self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth"), 360)
            else:
                p.select_option("#scope", "all")
                self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth"), 360)


if __name__ == "__main__":
    unittest.main()
