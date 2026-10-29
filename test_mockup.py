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

    def open(self, **kw):
        page = self.browser.new_page(**kw)
        self.requests = []
        page.on("request", lambda r: self.requests.append(r.url))
        page.goto(PAGE)
        return page

    def test_scenarios_chat_and_no_network(self):
        p = self.open()
        self.assertIn("MOCKUP", p.inner_text(".banner"))
        self.assertTrue(p.is_enabled("#open"))
        for sc in ("herdr", "webui"):
            p.select_option("#scenario", sc)
            self.assertTrue(p.is_disabled("#open"))
            self.assertEqual(p.locator(".svc.down").count(), 1)
        p.select_option("#scenario", "llm")
        self.assertTrue(p.is_enabled("#open"))
        p.fill("#input", "<b>x</b>")
        p.press("#input", "Shift+Enter")
        self.assertEqual(p.locator(".msg.user").count(), 0)
        p.press("#input", "Enter")
        self.assertEqual(p.locator(".msg.user").inner_text().split("\n")[-1], "<b>x</b>")
        self.assertEqual(p.locator(".msg.user b").count(), 0)
        self.assertIn("LLM unavailable", p.locator(".msg.bot").last.inner_text())
        p.click("#approve")
        self.assertIn("no command was executed", p.inner_text("#repair-status"))
        self.assertEqual([u for u in self.requests if not u.startswith("file:")], [])

    def test_mobile_no_horizontal_overflow(self):
        p = self.open(viewport={"width": 360, "height": 740})
        self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth"), 360)


if __name__ == "__main__":
    unittest.main()
