"""Browser regressions with fictional sessions; never connects to live Herdr.

Uses an already installed Playwright and Google Chrome (no downloads).
Run: python3 -m unittest test_ui -v
"""
import copy
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
TREE = {
    "host": "demo", "version": "1.0", "protocol": 1, "latency_ms": 4,
    "focused": {"workspace": "w1", "tab": "t1", "pane": "p2"},
    "workspaces": [
        {"workspace_id": "w1", "label": "Website", "number": 1, "active_tab_id": "t1", "pane_count": 3},
        {"workspace_id": "w2", "label": "Docs", "number": 2, "active_tab_id": "t2", "pane_count": 1},
    ],
    "tabs": [
        {"tab_id": "t1", "workspace_id": "w1", "label": "Build", "number": 1, "pane_count": 3},
        {"tab_id": "t2", "workspace_id": "w2", "label": "Writing", "number": 1, "pane_count": 1},
        {"tab_id": "t3", "workspace_id": "w2", "label": "Empty", "number": 2, "pane_count": 0},
    ],
    "panes": [
        {"pane_id": "p1", "tab_id": "t1", "workspace_id": "w1", "label": "ui-agent", "agent": "pi", "agent_status": "working", "cwd": "src"},
        {"pane_id": "p2", "tab_id": "t1", "workspace_id": "w1", "label": "api-agent", "agent": "codex", "agent_status": "working", "cwd": "src"},
        {"pane_id": "p3", "tab_id": "t1", "workspace_id": "w1", "label": "shell", "agent": None, "agent_status": "idle", "cwd": "src"},
        {"pane_id": "p4", "tab_id": "t2", "workspace_id": "w2", "label": "review-agent", "agent": "claude", "agent_status": "blocked", "cwd": "docs"},
    ],
    "layouts": [{"tab_id": "t1", "area": {"x": 0, "y": 0, "width": 120, "height": 40}, "panes": [
        {"pane_id": "p1", "rect": {"x": 0, "y": 0, "width": 60, "height": 20}},
        {"pane_id": "p2", "rect": {"x": 60, "y": 0, "width": 60, "height": 20}},
        {"pane_id": "p3", "rect": {"x": 0, "y": 20, "width": 120, "height": 20}},
    ]}],
}


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, data, status=200, html=False):
        body = data.encode() if html else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            return self.reply((HERE / "index.html").read_text().replace("__CSRF_TOKEN__", "fictional-test-token"), html=True)
        if url.path == "/api/tree":
            if self.server.fail_tree:
                return self.reply({"error": "Fixture offline"}, 502)
            return self.reply(self.server.tree)
        if url.path == "/api/read":
            pane = parse_qs(url.query)["pane"][0]
            self.server.reads.append(pane)
            if pane == self.server.delay_pane:
                self.server.read_started.set()
                self.server.read_release.wait(5)
            return self.reply({"text": f"Terminal output for {pane}\n\x1b[32mReady\x1b[0m\n"})
        self.reply({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.server.delay_action:
            self.server.action_started.set()
            self.server.action_release.wait(5)
        self.server.actions.append((self.path, body, self.headers.get("X-CSRF-Token")))
        if self.server.fail_action:
            return self.reply({"error": "Fixture action failed"}, 502)
        if self.path == "/api/action/close":
            self.server.tree["panes"] = [p for p in self.server.tree["panes"] if p["pane_id"] != body["pane"]]
        self.reply({"result": {"ok": True}})


class PhoneUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.httpd.tree = copy.deepcopy(TREE)
        self.httpd.fail_tree = self.httpd.fail_action = self.httpd.delay_action = False
        self.httpd.action_started, self.httpd.action_release = threading.Event(), threading.Event()
        self.httpd.actions, self.httpd.reads = [], []
        self.httpd.delay_pane = None
        self.httpd.read_started, self.httpd.read_release = threading.Event(), threading.Event()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.httpd.read_release.set()
        self.httpd.action_release.set()
        self.context.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.assertEqual(self.errors, [])

    def open(self, suffix=""):
        self.page.goto(self.base + "/" + suffix)
        self.page.wait_for_function("S.tree !== null")

    def choose(self, pane):
        self.page.get_by_role("button", name="Show all agents").click()
        self.page.locator(f'[data-agent-pane="{pane}"]').click()

    def test_phone_starts_with_focused_pane_without_keyboard_or_mutation(self):
        self.open()
        self.page.locator("#dock-term").get_by_text("Terminal output for p2", exact=False).wait_for()
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "api-agent")
        self.assertFalse(self.page.locator("aside").is_visible())
        self.assertFalse(self.page.locator("#tabbar").is_visible())
        self.assertFalse(self.page.locator(".canvas-wrap").is_visible())
        self.assertFalse(self.page.locator("#dock-input").evaluate("el => el === document.activeElement"))
        self.assertEqual(set(self.httpd.reads), {"p2"})
        self.assertEqual(self.httpd.actions, [])

    def test_deep_link_takes_priority(self):
        self.open("#pane=p4")
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "review-agent")
        self.assertEqual(self.page.locator("#mobile-path").inner_text(), "Docs / Writing")
        self.assertEqual(self.page.locator("#dock-mode").input_value(), "send")

    def test_navigation_selection_does_not_change_tui_focus(self):
        self.open()
        self.page.get_by_role("button", name="Open spaces, tabs and panes").click()
        self.page.get_by_role("textbox", name="Find a space, tab or pane").fill("shell")
        self.page.locator('[data-pane="p3"]').click()
        self.assertFalse(self.page.locator("aside").is_visible())
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "shell")
        self.assertEqual(self.page.locator("#dock-mode").input_value(), "send")
        self.assertEqual(self.httpd.actions, [])

    def test_agents_show_working_before_blocked_and_search(self):
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        self.assertEqual(self.page.locator(".agent-card").count(), 3)
        self.assertEqual(self.page.locator(".agent-card").evaluate_all("els => els.map(el => el.dataset.agentPane)"), ["p2", "p1", "p4"])
        self.assertTrue(self.page.locator("#agents-attention").is_visible())
        self.page.get_by_role("textbox", name="Find an agent").fill("review")
        self.assertEqual(self.page.locator(".agent-card").count(), 1)
        self.page.locator(".agent-card").click()
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "review-agent")
        self.assertFalse(self.page.locator("#agents-view").is_visible())
        self.assertFalse(self.page.locator("#dock-input").evaluate("el => el === document.activeElement"))

    def add_agent_states(self):
        for pane_id, name, status in (("p5", "z-done", "done"), ("p6", "a-idle", "idle"), ("p7", "a-unknown", "unexpected")):
            self.httpd.tree["panes"].insert(0, {"pane_id": pane_id, "tab_id": "t2", "workspace_id": "w2", "label": name, "agent": "pi", "agent_status": status})

    def agent_ids(self):
        return self.page.locator(".agent-card").evaluate_all("els => els.map(el => el.dataset.agentPane)")

    def test_agents_sort_all_states_and_unknown_last(self):
        self.add_agent_states()
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        self.assertEqual(self.agent_ids(), ["p2", "p1", "p4", "p5", "p6", "p7"])
        self.assertEqual(self.page.locator(".agents-section").all_text_contents(), ["WORKING", "BLOCKED", "DONE", "IDLE", "UNKNOWN"])

    def test_state_count_filters_and_same_count_restores_sorted_list(self):
        self.add_agent_states()
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        for state, ids in (("working", ["p2", "p1"]), ("blocked", ["p4"]), ("done", ["p5"]), ("idle", ["p6"]), ("unknown", ["p7"])):
            button = self.page.get_by_role("button", name=f"Filter {state} agents")
            button.locator("strong").click()
            self.assertEqual(self.agent_ids(), ids)
            self.assertEqual(button.get_attribute("aria-pressed"), "true")
            button.locator("strong").click()
            self.assertEqual(self.agent_ids(), ["p2", "p1", "p4", "p5", "p6", "p7"])
            self.assertEqual(button.get_attribute("aria-pressed"), "false")
        self.assertEqual(self.httpd.actions, [])

    def test_state_filter_switches_combines_with_search_and_keeps_totals(self):
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        self.page.get_by_role("button", name="Filter working agents").click()
        self.page.get_by_role("textbox", name="Find an agent").fill("ui-agent")
        self.assertEqual(self.agent_ids(), ["p1"])
        self.assertEqual(self.page.locator('[data-agent-state="working"] strong').inner_text(), "2")
        self.page.get_by_role("button", name="Filter blocked agents").click()
        self.assertEqual(self.agent_ids(), [])
        self.page.get_by_role("textbox", name="Find an agent").fill("")
        self.assertEqual(self.agent_ids(), ["p4"])
        self.assertEqual(self.page.locator('[data-agent-state="working"]').get_attribute("aria-pressed"), "false")
        self.page.get_by_role("button", name="Filter done agents").click()
        self.assertEqual(self.agent_ids(), [])
        self.assertIn("No matching agents", self.page.locator("#agents-list").inner_text())
        self.page.get_by_role("button", name="Filter done agents").click()
        self.assertEqual(self.agent_ids(), ["p2", "p1", "p4"])

    def test_state_filter_and_focused_count_survive_refresh_and_zero_count(self):
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        self.page.get_by_role("button", name="Filter blocked agents").click()
        self.httpd.tree["panes"][3]["agent_status"] = "done"
        self.page.evaluate("refreshTree()")
        self.assertEqual(self.agent_ids(), [])
        button = self.page.get_by_role("button", name="Filter blocked agents")
        self.assertEqual(button.locator("strong").inner_text(), "0")
        self.assertEqual(button.get_attribute("aria-pressed"), "true")
        self.assertTrue(button.evaluate("el => el === document.activeElement"))
        self.page.keyboard.press("Enter")
        self.assertEqual(self.agent_ids(), ["p2", "p1", "p4"])

    def test_unknown_filter_remains_clearable_when_last_unknown_changes_state(self):
        self.add_agent_states()
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        self.page.get_by_role("button", name="Filter unknown agents").click()
        next(p for p in self.httpd.tree["panes"] if p["pane_id"] == "p7")["agent_status"] = "idle"
        self.page.evaluate("refreshTree()")
        button = self.page.get_by_role("button", name="Filter unknown agents")
        self.assertEqual(button.locator("strong").inner_text(), "0")
        button.click()
        self.assertEqual(len(self.agent_ids()), 6)

    def test_state_filters_remain_available_in_short_keyboard_viewports(self):
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        for height in (280, 220):
            self.page.evaluate("""height => {
              Object.defineProperty(window.visualViewport, 'height', {configurable:true, value:height});
              window.visualViewport.dispatchEvent(new Event('resize'));
            }""", height)
            button = self.page.get_by_role("button", name="Filter working agents")
            button.scroll_into_view_if_needed()
            button.click()
            self.assertEqual(self.agent_ids(), ["p2", "p1"])
            button.click()
            self.assertEqual(self.agent_ids(), ["p2", "p1", "p4"])
            self.page.locator('[data-agent-pane="p4"]').scroll_into_view_if_needed()
            box = self.page.locator('[data-agent-pane="p4"]').bounding_box()
            self.assertLessEqual(box["y"] + box["height"], height)

    def test_dismissal_preserves_pane_and_escape_never_closes_terminal(self):
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        self.page.get_by_role("button", name="Return to focused pane").click()
        self.page.get_by_role("button", name="Open spaces, tabs and panes").click()
        self.page.keyboard.press("Escape")
        self.page.keyboard.press("Escape")
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "api-agent")
        self.assertTrue(self.page.locator("#dock").is_visible())

    def test_drafts_do_not_cross_panes_and_survive_return(self):
        self.open()
        self.page.locator("#dock-input").fill("draft for api")
        self.choose("p1")
        self.assertEqual(self.page.locator("#dock-input").input_value(), "")
        self.page.locator("#dock-input").fill("draft for ui")
        self.choose("p2")
        self.assertEqual(self.page.locator("#dock-input").input_value(), "draft for api")

    def test_prompt_send_type_and_keys_use_selected_pane(self):
        self.open()
        for mode, action, extra in (("prompt", "prompt", {}), ("send", "send", {"enter": True}), ("type", "send", {"enter": False})):
            self.page.locator("#dock-mode").select_option(mode)
            self.page.locator("#dock-input").fill("fixture only")
            with self.page.expect_response("**/api/action/" + action):
                self.page.locator('#dock-form button[type="submit"]').click()
            self.assertEqual(self.httpd.actions[-1], ("/api/action/" + action, {"pane": "p2", "text": "fixture only", **extra}, "fictional-test-token"))
            self.assertEqual(self.page.locator("#dock-input").input_value(), "")
        with self.page.expect_response("**/api/action/keys"):
            self.page.locator('[data-key="ctrl+c"]').click()
        self.assertEqual(self.httpd.actions[-1][1], {"pane": "p2", "keys": ["ctrl+c"]})

    def test_phone_close_requires_confirmation_and_targets_focused_pane(self):
        self.open()
        self.page.locator("#mobile-more").click()
        messages = []
        def cancel(dialog):
            messages.append(dialog.message)
            dialog.dismiss()
        self.page.once("dialog", cancel)
        self.page.get_by_role("button", name="Close pane", exact=True).click()
        self.assertIn('api-agent', messages[0])
        self.assertIn('Ctrl-C ×2', messages[0])
        self.assertEqual(self.httpd.actions, [])
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "api-agent")
        self.page.once("dialog", lambda dialog: dialog.accept())
        with self.page.expect_response("**/api/action/close"):
            self.page.get_by_role("button", name="Close pane", exact=True).click()
        self.page.wait_for_function("document.querySelector('#mobile-title').textContent === 'ui-agent'")
        self.assertEqual(self.httpd.actions[0], ("/api/action/close", {"pane": "p2"}, "fictional-test-token"))
        self.assertFalse(any(action[0] == "/api/action/keys" for action in self.httpd.actions))

    def test_close_failure_preserves_focused_pane_and_draft(self):
        self.open()
        self.httpd.fail_action = True
        self.page.locator("#dock-input").fill("keep this draft")
        self.page.locator("#mobile-more").click()
        self.page.once("dialog", lambda dialog: dialog.accept())
        with self.page.expect_response("**/api/action/close"):
            self.page.locator("#mobile-close").click()
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "api-agent")
        self.assertEqual(self.page.locator("#dock-input").input_value(), "keep this draft")
        self.assertIn("Fixture action failed", self.page.locator("#toasts").inner_text())
        self.assertTrue(self.page.locator("#mobile-close").is_enabled())

    def test_closing_last_pane_leaves_empty_state(self):
        self.httpd.tree["panes"] = [p for p in self.httpd.tree["panes"] if p["pane_id"] == "p2"]
        self.open()
        self.page.locator("#mobile-more").click()
        self.page.once("dialog", lambda dialog: dialog.accept())
        with self.page.expect_response("**/api/action/close"):
            self.page.locator("#mobile-close").click()
        self.page.locator("#mobile-empty").wait_for()
        self.assertFalse(self.page.locator("#dock").is_visible())

    def test_delayed_close_does_not_close_new_selection(self):
        self.open()
        self.httpd.delay_action = True
        self.page.locator("#mobile-more").click()
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#mobile-close").click()
        self.assertTrue(self.httpd.action_started.wait(2))
        self.page.evaluate("refreshTree()")
        self.assertFalse(self.page.locator("#mobile-close").is_enabled())
        self.choose("p1")
        with self.page.expect_response("**/api/action/close"):
            self.httpd.action_release.set()
        self.page.wait_for_function("!S.tree.panes.some(p => p.pane_id === 'p2')")
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "ui-agent")
        self.assertTrue(self.page.locator("#dock").is_visible())
        self.assertEqual(len(self.httpd.actions), 1)

    def test_double_interrupt_sends_two_keys_without_closing_pane(self):
        self.open()
        self.page.locator("#dock-input").fill("unsent draft")
        with self.page.expect_response("**/api/action/keys"):
            self.page.locator("[data-interrupt-twice]").click()
        self.assertEqual(self.httpd.actions[-1], ("/api/action/keys", {"pane": "p2", "keys": ["ctrl+c", "ctrl+c"]}, "fictional-test-token"))
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "api-agent")
        self.assertEqual(self.page.locator("#dock-input").input_value(), "unsent draft")
        self.assertTrue(self.page.locator("#dock").is_visible())
        self.assertFalse(any(action[0] == "/api/action/close" for action in self.httpd.actions))

    def test_landscape_pane_actions_are_reachable(self):
        self.page.set_viewport_size({"width": 844, "height": 390})
        self.open()
        self.page.locator("#mobile-header-more").click()
        self.assertTrue(self.page.locator("#mobile-close").is_visible())
        with self.page.expect_response("**/api/action/keys"):
            self.page.locator("#mobile-interrupt").click()
        self.assertEqual(self.httpd.actions[-1][1], {"pane": "p2", "keys": ["ctrl+c", "ctrl+c"]})
        self.assertTrue(self.page.locator("#dock").is_visible())

    def test_double_interrupt_guard_survives_refresh_and_selection_changes(self):
        self.open()
        self.httpd.delay_action = True
        self.page.locator("[data-interrupt-twice]").click()
        self.assertTrue(self.httpd.action_started.wait(2))
        self.page.evaluate("refreshTree()")
        self.page.locator("#mobile-more").click()
        self.assertFalse(self.page.locator("#mobile-interrupt").is_enabled())
        self.choose("p1")
        self.assertFalse(self.page.locator("[data-interrupt-twice]").is_enabled())
        with self.page.expect_response("**/api/action/keys"):
            self.httpd.action_release.set()
        self.assertEqual(self.httpd.actions[0][1]["pane"], "p2")
        self.assertEqual(len(self.httpd.actions), 1)
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "ui-agent")
        self.assertTrue(self.page.locator("[data-interrupt-twice]").is_enabled())

    def test_double_interrupt_error_is_reported_and_control_reenabled(self):
        self.open()
        self.httpd.fail_action = True
        with self.page.expect_response("**/api/action/keys"):
            self.page.locator("[data-interrupt-twice]").click()
        self.assertIn("Fixture action failed", self.page.locator("#toasts").inner_text())
        self.assertTrue(self.page.locator("[data-interrupt-twice]").is_enabled())
        self.assertTrue(self.page.locator("#dock").is_visible())

    def test_inflight_send_does_not_clear_new_pane_draft(self):
        self.open()
        self.httpd.delay_action = True
        self.page.locator("#dock-input").fill("sent to api")
        self.page.locator('#dock-form button[type="submit"]').click()
        self.assertTrue(self.httpd.action_started.wait(2))
        self.choose("p1")
        self.page.locator("#dock-input").fill("unsent ui draft")
        with self.page.expect_response("**/api/action/prompt"):
            self.httpd.action_release.set()
        self.assertEqual(self.page.locator("#dock-input").input_value(), "unsent ui draft")
        self.assertEqual(self.httpd.actions[0][1]["pane"], "p2")
        self.choose("p2")
        self.assertEqual(self.page.locator("#dock-input").input_value(), "")

    def test_failed_send_keeps_draft(self):
        self.open()
        self.httpd.fail_action = True
        self.page.locator("#dock-input").fill("keep this")
        with self.page.expect_response("**/api/action/prompt"):
            self.page.locator('#dock-form button[type="submit"]').click()
        self.assertEqual(self.page.locator("#dock-input").input_value(), "keep this")
        self.assertIn("Fixture action failed", self.page.locator("#toasts").inner_text())

    def test_empty_tab_stays_empty_instead_of_showing_another_tab(self):
        self.open()
        self.page.get_by_role("button", name="Open spaces, tabs and panes").click()
        self.page.get_by_role("textbox", name="Find a space, tab or pane").fill("Empty")
        self.page.locator('[data-tab="t3"]').first.click()
        self.page.evaluate("refreshTree()")
        self.assertTrue(self.page.locator("#mobile-empty").is_visible())
        self.assertFalse(self.page.locator("#dock").is_visible())
        self.assertEqual(self.page.locator("#mobile-path").inner_text(), "Docs / Empty")

    def test_deep_link_is_consumed_before_empty_tab_selection(self):
        self.open("#pane=p4")
        self.page.get_by_role("button", name="Open spaces, tabs and panes").click()
        self.page.get_by_role("textbox", name="Find a space, tab or pane").fill("Empty")
        self.page.locator('[data-tab="t3"]').first.click()
        self.page.evaluate("loop()")
        self.assertTrue(self.page.locator("#mobile-empty").is_visible())
        self.assertFalse(self.page.locator("#dock").is_visible())
        self.assertEqual(self.page.locator("#mobile-path").inner_text(), "Docs / Empty")

    def test_closed_pane_falls_back_in_selected_tab(self):
        self.open()
        self.httpd.tree["panes"] = [p for p in self.httpd.tree["panes"] if p["pane_id"] != "p2"]
        self.page.evaluate("refreshTree()")
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "ui-agent")

    def test_external_tui_focus_does_not_steal_selected_phone_pane(self):
        self.open()
        self.choose("p1")
        self.httpd.tree["focused"] = {"workspace": "w2", "tab": "t2", "pane": "p4"}
        self.page.evaluate("refreshTree()")
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "ui-agent")

    def test_stale_read_cannot_replace_new_pane_output(self):
        self.httpd.delay_pane = "p2"
        self.open()
        self.assertTrue(self.httpd.read_started.wait(2))
        self.choose("p1")
        self.page.locator("#dock-term").get_by_text("Terminal output for p1", exact=False).wait_for()
        with self.page.expect_response(lambda r: "/api/read?pane=p2" in r.url):
            self.httpd.read_release.set()
        self.assertNotIn("output for p2", self.page.locator("#dock-term").inner_text())

    def test_empty_offline_and_unknown_states_are_safe(self):
        self.httpd.tree["workspaces"], self.httpd.tree["tabs"], self.httpd.tree["panes"] = [], [], []
        self.open()
        self.assertTrue(self.page.locator("#mobile-empty").is_visible())
        self.page.get_by_role("button", name="Show all agents").click()
        self.assertIn("No agents yet", self.page.locator("#agents-list").inner_text())
        self.page.get_by_role("button", name="Return to focused pane").click()
        self.httpd.fail_tree = True
        self.page.evaluate("refreshTree()")
        self.assertIn("Fixture offline", self.page.locator("#mobile-empty-text").inner_text())
        self.assertIn("STALE", self.page.locator("#mobile-live").inner_text())

    def test_navigation_focus_is_contained(self):
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        self.page.locator(".agent-card").last.focus()
        self.page.keyboard.press("Tab")
        self.assertTrue(self.page.locator("#agents-close").evaluate("el => el === document.activeElement"))
        self.assertTrue(self.page.locator("#main-content").evaluate("el => el.inert"))

    def test_resizing_preserves_desktop_grid_and_mobile_selection(self):
        self.page.set_viewport_size({"width": 1440, "height": 1000})
        self.open()
        self.assertTrue(self.page.locator("aside").is_visible())
        self.assertEqual(self.page.locator(".pane").count(), 3)
        self.assertFalse(self.page.locator("#dock").is_visible())
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("document.querySelector('#mobile-title').textContent === 'api-agent'")
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "api-agent")
        self.page.set_viewport_size({"width": 1440, "height": 1000})
        self.page.wait_for_function("!document.querySelector('#dock').classList.contains('open')")
        self.assertFalse(self.page.locator("#dock").is_visible())
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("document.querySelector('#dock').classList.contains('open')")
        self.choose("p1")
        self.page.set_viewport_size({"width": 1440, "height": 1000})
        self.assertTrue(self.page.locator("#dock").is_visible())
        self.assertEqual(self.page.locator("#dock-title").inner_text(), "ui-agent")

    def test_status_refresh_never_changes_existing_prompt_delivery(self):
        self.open()
        self.page.locator("#dock-input").fill("task draft, not an approval reply")
        self.httpd.tree["panes"][1]["agent_status"] = "blocked"
        self.page.evaluate("refreshTree()")
        self.assertEqual(self.page.locator("#dock-mode").input_value(), "prompt")
        with self.page.expect_response("**/api/action/prompt"):
            self.page.locator('#dock-form button[type="submit"]').click()
        self.assertEqual(self.httpd.actions[-1][0], "/api/action/prompt")

    def test_existing_desktop_inspector_normalizes_mobile_location(self):
        self.page.set_viewport_size({"width": 1440, "height": 1000})
        self.open()
        self.page.locator('[data-pane="p1"]').click()
        self.page.locator('[data-ws="w2"]').click()
        self.assertEqual(self.page.locator("#dock-title").inner_text(), "ui-agent")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("document.querySelector('#mobile-path').textContent === 'Website / Build'")
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "ui-agent")
        self.assertEqual(self.page.locator("#mobile-path").inner_text(), "Website / Build")

    def test_initial_offline_state_recovers(self):
        self.httpd.fail_tree = True
        self.page.goto(self.base)
        self.page.wait_for_function("S.err !== null")
        self.assertIn("Fixture offline", self.page.locator("#mobile-empty-text").inner_text())
        self.httpd.fail_tree = False
        self.page.evaluate("refreshTree()")
        self.assertEqual(self.page.locator("#mobile-title").inner_text(), "api-agent")

    def test_keyboard_visual_viewport_keeps_input_visible(self):
        self.open()
        self.page.locator("#dock-input").focus()
        self.page.evaluate("""() => {
          Object.defineProperty(window.visualViewport, 'height', {configurable:true, value:430});
          Object.defineProperty(window.visualViewport, 'offsetTop', {configurable:true, value:40});
          window.visualViewport.dispatchEvent(new Event('resize'));
        }""")
        box = self.page.locator("#dock-form").bounding_box()
        self.assertGreaterEqual(box["y"], 40)
        self.assertLessEqual(box["y"] + box["height"], 470)
        self.assertTrue(self.page.locator("#dock-input").evaluate("el => el === document.activeElement"))

    def test_short_keyboard_viewports_keep_expanded_composer_visible(self):
        self.open()
        self.page.locator("#dock-input").fill("one\\ntwo\\nthree\\nfour\\nfive\\nsix")
        for height in (280, 220, 180):
            self.page.evaluate("""height => {
              Object.defineProperty(window.visualViewport, 'height', {configurable:true, value:height});
              window.visualViewport.dispatchEvent(new Event('resize'));
            }""", height)
            form = self.page.locator("#dock-form").bounding_box()
            self.assertGreaterEqual(form["y"], 44)
            self.assertLessEqual(form["y"] + form["height"], height)
            self.assertTrue(self.page.locator("#dock-keys").is_visible())
            self.page.locator('[data-key="ctrl+l"]').scroll_into_view_if_needed()
            with self.page.expect_response("**/api/action/keys"):
                self.page.locator('[data-key="ctrl+l"]').click()
            self.assertEqual(self.httpd.actions[-1][1], {"pane": "p2", "keys": ["ctrl+l"]})
            self.page.locator("#mobile-header-more").click()
            self.assertTrue(self.page.locator("#mobile-refresh").is_visible())
            self.page.locator("#mobile-header-more").click()

    def test_collapsed_current_workspace_stays_collapsed_after_poll(self):
        self.open()
        self.page.get_by_role("button", name="Open spaces, tabs and panes").click()
        self.page.locator('[data-toggle="w1"]').click()
        self.assertEqual(self.page.locator('[data-pane="p1"]').count(), 0)
        self.page.evaluate("refreshTree()")
        self.assertEqual(self.page.locator('[data-pane="p1"]').count(), 0)

    def test_agents_search_remains_usable_in_short_keyboard_viewports(self):
        self.open()
        for height in (280, 220):
            self.page.get_by_role("button", name="Show all agents").click()
            self.page.evaluate("""height => {
              Object.defineProperty(window.visualViewport, 'height', {configurable:true, value:height});
              window.visualViewport.dispatchEvent(new Event('resize'));
            }""", height)
            self.page.get_by_role("textbox", name="Find an agent").fill("review")
            self.page.locator('[data-agent-pane="p4"]').scroll_into_view_if_needed()
            box = self.page.locator('[data-agent-pane="p4"]').bounding_box()
            self.assertLessEqual(box["y"] + box["height"], height)
            self.page.locator('[data-agent-pane="p4"]').click()
            self.assertEqual(self.page.locator("#mobile-title").inner_text(), "review-agent")

    def test_navigation_search_remains_usable_in_short_keyboard_viewports(self):
        self.open()
        for height in (280, 220):
            self.page.get_by_role("button", name="Open spaces, tabs and panes").click()
            self.page.evaluate("""height => {
              Object.defineProperty(window.visualViewport, 'height', {configurable:true, value:height});
              window.visualViewport.dispatchEvent(new Event('resize'));
            }""", height)
            self.page.get_by_role("textbox", name="Find a space, tab or pane").fill("review")
            self.page.locator('[data-pane="p4"]').scroll_into_view_if_needed()
            box = self.page.locator('[data-pane="p4"]').bounding_box()
            self.assertLessEqual(box["y"] + box["height"], height)
            self.page.locator('[data-pane="p4"]').click()
            self.assertEqual(self.page.locator("#mobile-title").inner_text(), "review-agent")

    def test_empty_landscape_can_launch_first_session(self):
        self.httpd.tree["workspaces"], self.httpd.tree["tabs"], self.httpd.tree["panes"] = [], [], []
        self.page.set_viewport_size({"width": 844, "height": 390})
        self.open()
        self.page.get_by_role("button", name="Open spaces, tabs and panes").click()
        self.page.get_by_role("button", name="New session", exact=True).click()
        self.assertTrue(self.page.locator("#spawn.open").is_visible())
        self.assertTrue(self.page.locator("#spawn-go").is_visible())

    def test_launch_dialog_follows_short_keyboard_viewport(self):
        self.open()
        self.page.get_by_role("button", name="Open spaces, tabs and panes").click()
        self.page.locator("#mobile-spawn").click()
        self.page.evaluate("""() => {
          Object.defineProperty(window.visualViewport, 'height', {configurable:true, value:220});
          Object.defineProperty(window.visualViewport, 'offsetTop', {configurable:true, value:30});
          window.visualViewport.dispatchEvent(new Event('resize'));
        }""")
        self.page.locator("#spawn-go").scroll_into_view_if_needed()
        box = self.page.locator("#spawn-go").bounding_box()
        self.assertGreaterEqual(box["y"], 30)
        self.assertLessEqual(box["y"] + box["height"], 250)
        self.assertEqual(self.httpd.actions, [])

    def test_unknown_status_and_untrusted_labels_are_plain_text(self):
        self.httpd.tree["panes"][0].update(label='<img src=x onerror="window.fixtureInjected=true">', agent_status="unexpected")
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        self.assertIn("unknown", self.page.locator("#agents-list").inner_text())
        self.assertEqual(self.page.locator("#agents-list img").count(), 0)
        self.assertFalse(self.page.evaluate("Boolean(window.fixtureInjected)"))

    def test_refresh_preserves_focused_agent_and_tree_controls(self):
        self.open()
        self.page.get_by_role("button", name="Show all agents").click()
        self.page.locator('[data-agent-pane="p1"]').focus()
        self.page.evaluate("refreshTree()")
        self.assertTrue(self.page.locator('[data-agent-pane="p1"]').evaluate("el => el === document.activeElement"))
        self.page.get_by_role("button", name="Return to focused pane").click()
        self.page.get_by_role("button", name="Open spaces, tabs and panes").click()
        self.page.locator('[data-pane="p1"]').focus()
        self.page.evaluate("refreshTree()")
        self.assertTrue(self.page.locator('[data-pane="p1"]').evaluate("el => el === document.activeElement"))

    def test_narrow_and_landscape_views_keep_composer_inside_viewport(self):
        self.open()
        for width, height in ((320, 700), (375, 812), (430, 932), (844, 390), (390, 430)):
            self.page.set_viewport_size({"width": width, "height": height})
            self.page.wait_for_function("document.querySelector('.app').clientHeight === window.visualViewport.height")
            self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
            box = self.page.locator("#dock-form").bounding_box()
            self.assertLessEqual(box["y"] + box["height"], height)
            self.page.get_by_role("button", name="Show all agents").click()
            self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
            self.page.get_by_role("button", name="Return to focused pane").click()


if __name__ == "__main__":
    unittest.main()
