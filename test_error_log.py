"""Error-file coverage using isolated HTTP requests and fictional secret canaries."""
import contextlib
import http.client
import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from error_log import ErrorLog


class ErrorLoggingTest(unittest.TestCase):
    def setUp(self):
        root = Path.home() / "tmp"
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "logs/errors.jsonl"
        self.httpd = server.make_server(0, log_file=self.path)
        self.addCleanup(self.httpd.server_close)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        server.invalidate()

    def request(self, method="GET", path="/api/tree", body=None, token=server.TOKEN, **headers):
        conn = http.client.HTTPConnection(*self.httpd.server_address, timeout=3)
        self.addCleanup(conn.close)
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-CSRF-Token": token})
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        return response.status, data, response.getheader("X-Request-ID")

    def events(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_csrf_invalid_json_and_unknown_routes_have_safe_correlated_logs(self):
        secret = "FICTIONAL-DO-NOT-LOG"
        cases = [
            ("POST", "/api/action/send", b"{}", secret, 403, "csrf_invalid"),
            ("POST", "/api/action/send", b"{", server.TOKEN, 400, "invalid_json"),
            ("GET", f"/private/{secret}?token={secret}", None, server.TOKEN, 404, "not_found"),
        ]
        for method, route, body, token, status, code in cases:
            actual, _, request_id = self.request(method, route, body, token)
            self.assertEqual(actual, status)
            entry = self.events()[-1]
            self.assertEqual(entry["code"], code)
            self.assertEqual(entry["request_id"], request_id)
            self.assertIn("duration_ms", entry)
        self.assertNotIn(secret, self.path.read_text())
        self.assertNotIn(server.TOKEN, self.path.read_text())

    def test_get_errors_and_unexpected_exceptions_are_logged_without_messages(self):
        for error, status, code in (
            (server.BridgeError("herdr returned invalid JSON", code="herdr_invalid_json"), 502, "herdr_invalid_json"),
            (RuntimeError("SECRET-MESSAGE-AND-TERMINAL-OUTPUT"), 500, "internal_error"),
            (ValueError("SECRET-INTERNAL-VALUE"), 500, "internal_error"),
        ):
            with patch.object(server, "snapshot", side_effect=error):
                self.assertEqual(self.request()[0], status)
            entry = self.events()[-1]
            self.assertEqual(entry["code"], code)
            self.assertEqual(entry["route"], "/api/tree")
        content = self.path.read_text()
        self.assertNotIn("SECRET-", content)
        self.assertIn('"exception":"ValueError"', content)
        self.assertEqual(len(self.events()), 3)

    def test_subprocess_failure_classes_do_not_log_output_or_argv(self):
        fixtures = [
            (FileNotFoundError(), "herdr_not_found", 502),
            (subprocess.TimeoutExpired("secret command", 1), "herdr_timeout", 504),
            (subprocess.CompletedProcess([], 3, "SECRET-STDOUT", "SECRET-STDERR"), "herdr_exit", 502),
            (subprocess.CompletedProcess([], 0, "SECRET-NOT-JSON", ""), "herdr_invalid_json", 502),
        ]
        for result, code, status in fixtures:
            kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            server.invalidate()
            with patch.object(server.subprocess, "run", **kwargs):
                self.assertEqual(self.request()[0], status)
            self.assertEqual(self.events()[-1]["code"], code)
        self.assertNotIn("SECRET-", self.path.read_text())
        self.assertNotIn("secret command", self.path.read_text())

    def test_parser_and_unsupported_methods_are_logged(self):
        self.assertEqual(self.request("DELETE", "/")[0], 501)
        with socket.create_connection(self.httpd.server_address, timeout=3) as sock:
            sock.sendall(b"SECRET-METHOD / HTTP/bogus\r\n\r\n")
            response = sock.recv(4096)
            self.assertIn(b"Bad Request", response)
        self.assertEqual([e["status"] for e in self.events()], [501, 400])
        self.assertNotIn("SECRET-METHOD", self.path.read_text())

    def test_slow_headers_log_timeout_without_raw_headers(self):
        self.httpd.request_timeout = 0.05
        with socket.create_connection(self.httpd.server_address, timeout=3) as sock:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nSecret: FICTIONAL")
            self.assertEqual(sock.recv(1024), b"")
        self.assertEqual(self.events()[-1]["code"], "request_timeout")
        self.assertNotIn("FICTIONAL", self.path.read_text())

    def test_browser_reports_are_allowlisted_and_exclude_arbitrary_data(self):
        body = {"kind": "invalid_json", "route": "/api/tree?token=SECRET-QUERY", "status": 502,
                "request_id": "0123456789abcdef", "message": "SECRET-MESSAGE", "stack": "SECRET-STACK"}
        self.assertEqual(self.request("POST", "/api/client-error", json.dumps(body))[0], 200)
        entry = self.events()[-1]
        self.assertEqual(entry["event"], "browser_error")
        self.assertEqual(entry["source_route"], "/api/tree")
        self.assertEqual(entry["related_request_id"], body["request_id"])
        self.assertNotIn("SECRET-", self.path.read_text())
        self.assertEqual(self.request("POST", "/api/client-error", b'{"kind":"raw-secret"}')[0], 400)
        self.assertEqual(self.request("POST", "/api/client-error", b"x" * 2049)[0], 413)
        self.assertEqual(self.request("POST", "/api/client-error", b'{"kind":"network_error"}', token="bad")[0], 403)

    def test_successful_requests_are_quiet_and_mockup_has_no_herdr_dependency(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/errors.js")[0], 200)
        with patch.object(server, "herdr", side_effect=AssertionError("must not call Herdr")):
            for route in ("/mockups/sre-landing", "/mockups/model-switch"):
                status, body, _ = self.request("GET", route)
                self.assertEqual(status, 200)
                self.assertIn(b"mockup", body.lower())
        self.assertEqual(self.events(), [])

    def test_file_is_private_append_only_and_rotates(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        log = ErrorLog(Path(self.tmp.name) / "rotation.jsonl")
        log.handler.maxBytes = 180
        try:
            for i in range(10):
                log.event("fixture_error", sequence=i)
        finally:
            log.close()
        files = list(Path(self.tmp.name).glob("rotation.jsonl*"))
        self.assertLessEqual(len(files), 4)
        self.assertGreater(len(files), 1)
        for path in files:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for line in path.read_text().splitlines():
                json.loads(line)
        log = ErrorLog(Path(self.tmp.name) / "rotation.jsonl")
        try:
            log.event("after_restart")
        finally:
            log.close()
        self.assertIn("after_restart", (Path(self.tmp.name) / "rotation.jsonl").read_text())

    def test_log_open_failure_is_explicit_and_symlinks_are_rejected(self):
        target = Path(self.tmp.name) / "target"
        target.write_text("untouched")
        link = Path(self.tmp.name) / "symlink"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            server.make_server(0, log_file=link)
        self.assertEqual(target.read_text(), "untouched")

    def test_runtime_write_failure_retains_safe_stderr_fallback(self):
        stream = io.StringIO()
        with patch.object(self.httpd.errors.handler, "emit", side_effect=OSError("SECRET-DISK-ERROR")), contextlib.redirect_stderr(stream):
            self.httpd.errors.event("fixture_error", code="safe_code")
        self.assertIn("error log write failed", stream.getvalue())
        self.assertIn("safe_code", stream.getvalue())
        self.assertNotIn("SECRET", stream.getvalue())

    def test_concurrent_events_remain_valid_json(self):
        threads = [threading.Thread(target=lambda: [self.httpd.errors.event("fixture_error") for _ in range(10)]) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        self.assertEqual(len(self.events()), 80)

    def test_response_write_failure_is_logged_without_retry(self):
        handler = object.__new__(server.Handler)
        handler.server, handler.request_id = self.httpd, "0123456789abcdef"
        handler.started, handler.command, handler.path = 0, "POST", "/api/action/send"
        handler.send_response = lambda status: None
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler.wfile = io.BytesIO()
        with patch.object(handler.wfile, "write", side_effect=BrokenPipeError("SECRET-WRITE")):
            handler._send(200, b"{}", "application/json")
        self.assertEqual(self.events()[-1]["code"], "response_write_failed")
        self.assertNotIn("SECRET", self.path.read_text())


if __name__ == "__main__":
    unittest.main()
