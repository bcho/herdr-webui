#!/usr/bin/env python3
"""Tiny loopback-only HTTP bridge from a browser dashboard to the local Herdr server.

Stdlib only. Every Herdr call is an argv list (no shell). Mutations require a
per-process CSRF token that is only embedded in the served page.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
HERDR = os.environ.get("HERDR_BIN", "herdr")
TIMEOUT = float(os.environ.get("HERDR_TIMEOUT", "20"))
TOKEN = secrets.token_urlsafe(32)

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.\-]{0,127}$")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+_\-]{0,31}$")
KINDS = {"pi", "claude", "codex", "gemini", "cursor", "opencode", "copilot", "amp", "hermes", "qwen", "kimi", "droid"}
MAX_TEXT = 16_000
MAX_READ = 250_000


class BridgeError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def herdr(args: list[str], *, parse: bool = True):
    try:
        done = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except FileNotFoundError:
        raise BridgeError(f"herdr not found: {HERDR}")
    except subprocess.TimeoutExpired:
        raise BridgeError(f"herdr timed out after {TIMEOUT:g}s", 504)
    if done.returncode != 0:
        lines = (done.stderr or done.stdout).strip().splitlines()
        raise BridgeError((lines[-1] if lines else f"exit {done.returncode}")[:500])
    if not parse:
        return done.stdout
    try:
        payload = json.loads(done.stdout)
    except json.JSONDecodeError:
        raise BridgeError("herdr returned invalid JSON")
    return payload.get("result", payload) if isinstance(payload, dict) else payload


# ---------------------------------------------------------------------- tree
_cache: dict = {"at": 0.0, "tree": None}
_lock = threading.Lock()
PANE_KEYS = ("pane_id", "tab_id", "workspace_id", "label", "cwd", "foreground_cwd", "focused",
             "agent_status", "terminal_title_stripped", "revision")


def snapshot(max_age: float = 1.0) -> dict:
    """Host > Space(workspace) > Tab > Pane, normalized from `herdr api snapshot`."""
    with _lock:
        if _cache["tree"] and time.time() - _cache["at"] < max_age:
            return _cache["tree"]
    started = time.time()
    raw = herdr(["api", "snapshot"])
    snap = raw.get("snapshot", raw) if isinstance(raw, dict) else {}
    agents = {a.get("pane_id"): a for a in snap.get("agents", []) if isinstance(a, dict)}
    panes = []
    for p in snap.get("panes", []):
        a = agents.get(p.get("pane_id"), {})
        panes.append({k: p.get(k) for k in PANE_KEYS} | {
            "agent": p.get("agent") or a.get("agent"),
            "has_session": bool(p.get("agent_session") or a.get("agent_session")),
        })
    tree = {
        "host": socket.gethostname().split(".")[0],
        "version": snap.get("version"),
        "protocol": snap.get("protocol"),
        "focused": {k: snap.get(f"focused_{k}_id") for k in ("workspace", "tab", "pane")},
        "workspaces": snap.get("workspaces", []),
        "tabs": snap.get("tabs", []),
        "panes": panes,
        "layouts": [{"tab_id": l.get("tab_id"), "zoomed": l.get("zoomed"), "area": l.get("area"),
                     "panes": l.get("panes", [])} for l in snap.get("layouts", [])],
        "latency_ms": round((time.time() - started) * 1000),
    }
    with _lock:
        _cache.update(at=time.time(), tree=tree)
    return tree


def invalidate() -> None:
    with _lock:
        _cache["tree"] = None


# ------------------------------------------------------------------ validate
def need_id(body: dict, key: str) -> str:
    value = str(body.get(key) or "")
    if not ID_RE.match(value):
        raise BridgeError(f"invalid {key}", 400)
    return value


def need_text(body: dict, key: str, *, allow_empty: bool = False) -> str:
    value = body.get(key)
    if not isinstance(value, str) or (not value and not allow_empty) or len(value) > MAX_TEXT:
        raise BridgeError(f"invalid {key}", 400)
    return value


def opt_label(body: dict, key: str) -> list[str]:
    value = body.get(key)
    if not value:
        return []
    if not isinstance(value, str) or len(value) > 120 or "\n" in value or value.startswith("-"):
        raise BridgeError(f"invalid {key}", 400)
    return ["--label", value]


# ------------------------------------------------------------------- actions
def act_send(b):
    """Type literal text into the pane, then (by default) press Enter."""
    pane = need_id(b, "pane")
    text = need_text(b, "text", allow_empty=True)
    enter = bool(b.get("enter", True))
    if text and enter:
        herdr(["pane", "run", pane, text], parse=False)  # atomic text + Enter (no race with blocked prompts)
    elif text:
        herdr(["pane", "send-text", pane, text], parse=False)
    elif enter:
        herdr(["pane", "send-keys", pane, "enter"], parse=False)
    return {"ok": True}


def act_keys(b):
    pane = need_id(b, "pane")
    keys = b.get("keys")
    if not isinstance(keys, list) or not 0 < len(keys) <= 16 or not all(isinstance(k, str) and KEY_RE.match(k) for k in keys):
        raise BridgeError("invalid keys", 400)
    herdr(["pane", "send-keys", pane, *keys])
    return {"ok": True}


def act_prompt(b):
    """Agent-aware submit (rejects when the agent is blocked)."""
    return herdr(["agent", "prompt", need_id(b, "pane"), need_text(b, "text")])


def act_focus(b):
    """Focus the pane in the Herdr TUI: agent panes directly, others via their tab."""
    if b.get("agent"):
        return herdr(["agent", "focus", need_id(b, "pane")])
    return herdr(["tab", "focus", need_id(b, "tab")])


def act_split(b):
    direction = b.get("direction", "right")
    if direction not in ("right", "down"):
        raise BridgeError("invalid direction", 400)
    return herdr(["pane", "split", need_id(b, "pane"), "--direction", direction, "--no-focus"])


def act_close(b):
    return herdr(["pane", "close", need_id(b, "pane")])


def act_spawn(b):
    """New space (workspace) or new tab in an existing space; optionally start an agent in its root pane."""
    if b.get("workspace"):
        res = herdr(["tab", "create", "--workspace", need_id(b, "workspace"), *opt_label(b, "tab_label"), "--no-focus"])
    else:
        res = herdr(["workspace", "create", *opt_label(b, "space_label"), "--no-focus"])
    pane = (res.get("root_pane") or {}).get("pane_id")
    if not b.get("workspace") and b.get("tab_label") and res.get("tab", {}).get("tab_id"):
        herdr(["tab", "rename", res["tab"]["tab_id"], *opt_label(b, "tab_label")[1:]])
    kind = b.get("kind")
    if kind:
        if kind not in KINDS or not pane:
            raise BridgeError("invalid agent kind", 400)
        name = str(b.get("name") or kind)
        if not ID_RE.match(name):
            raise BridgeError("invalid agent name", 400)
        herdr(["agent", "start", name, "--kind", kind, "--pane", pane, "--timeout", "30000"])
    return {"pane_id": pane, "tab_id": (res.get("tab") or {}).get("tab_id"),
            "workspace_id": (res.get("root_pane") or {}).get("workspace_id")}


ACTIONS = {"send": act_send, "keys": act_keys, "prompt": act_prompt, "focus": act_focus,
           "split": act_split, "close": act_close, "spawn": act_spawn}


# -------------------------------------------------------------------- server
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
       "img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "herdr-webui/0.1"

    def log_message(self, fmt, *args):
        if self.command != "GET":
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                     ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                     ("X-Frame-Options", "DENY"), ("Content-Security-Policy", CSP)):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data):
        self._send(status, json.dumps(data).encode(), "application/json")

    def _host_ok(self) -> bool:
        # Defeat DNS rebinding: only accept loopback Host headers.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._host_ok():
            return self._json(421, {"error": "bad host"})
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                page = (HERE / "index.html").read_text().replace("__CSRF_TOKEN__", TOKEN)
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if url.path == "/api/tree":
                return self._json(200, snapshot())
            if url.path == "/api/read":
                pane = need_id(q, "pane")
                if not any(p["pane_id"] == pane for p in snapshot(max_age=5)["panes"]):
                    raise BridgeError("pane is not in the current snapshot", 404)
                lines = max(5, min(int(q.get("lines", "120")), 2000))
                fmt = "ansi" if q.get("ansi") == "1" else "text"
                text = herdr(["pane", "read", pane, "--source", "recent-unwrapped", "--lines", str(lines), "--format", fmt], parse=False)
                return self._json(200, {"pane": pane, "text": text[-MAX_READ:]})
            return self._json(404, {"error": "not found"})
        except BridgeError as e:
            return self._json(e.status, {"error": str(e)})
        except ValueError:
            return self._json(400, {"error": "bad request"})

    def do_POST(self):
        if not self._host_ok():
            return self._json(421, {"error": "bad host"})
        if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), TOKEN):
            return self._json(403, {"error": "missing or bad CSRF token"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(415, {"error": "expected application/json"})
        action = ACTIONS.get(urlparse(self.path).path.removeprefix("/api/action/"))
        if not action:
            return self._json(404, {"error": "unknown action"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 64_000:
                raise BridgeError("body too large", 413)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise BridgeError("expected object", 400)
            result = action(body)
            invalidate()
            return self._json(200, {"ok": True, "result": result})
        except BridgeError as e:
            return self._json(e.status, {"error": str(e)})
        except ValueError:
            return self._json(400, {"error": "bad request"})


def make_server(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8780")))
    args = ap.parse_args()
    httpd = make_server(args.port)
    print(f"Herdr web UI: http://127.0.0.1:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
