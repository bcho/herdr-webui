# herdr-webui

A tiny, dependency-free web dashboard for a local [Herdr](https://herdr.dev) server.
It mirrors Herdr's TUI hierarchy — **Host › Space › Tab › Pane** — with live terminal
output and direct agent interaction from the browser.

*Unofficial community project; not affiliated with the Herdr authors.*

- One Python file (`server.py`, stdlib only) + one HTML file (`index.html`, no CDN, no build).
- Talks to Herdr only through its supported CLI (`herdr api snapshot`, `pane read`, …).
- Single host (the local Herdr server). Multi-host is planned.

## Features

- Sidebar tree of spaces, tabs, and panes with live agent status (working / blocked / done / idle).
- Tab bar and pane canvas laid out from Herdr's real split geometry; each pane streams recent output.
- Inspector dock: full-color (ANSI) terminal view, then
  - **Prompt agent** (`herdr agent prompt`),
  - **Type + Enter** (`herdr pane run`, atomic — safe for blocked approval prompts),
  - **Type only**, and one-click keys (Enter, Esc, Ctrl-C, arrows, y/n, 1/2/3…).
- Launch a new space or tab, optionally starting an agent (`pi`, `claude`, `codex`, …) — without stealing TUI focus.
- Split / close panes, focus a pane in the Herdr TUI, filter (⌘K), `#pane=<id>` deep links.

## Run

Requires Python 3.9+ and `herdr` on `PATH` with a running server.

```bash
python3 server.py            # http://127.0.0.1:8780
python3 server.py --port 9000
HERDR_BIN=/path/to/herdr HERDR_TIMEOUT=20 python3 server.py
```

## Private remote access (Tailscale)

With Tailscale already installed and connected, use Serve for tailnet-only HTTPS:

```bash
HOST=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
python3 server.py --allow-host "$HOST"
# In another terminal:
tailscale serve --bg --https=443 http://127.0.0.1:8780
```

On an iPhone connected to the same tailnet, open the HTTPS URL printed by Serve
in Safari. Only devices allowed by your tailnet policy can connect; anyone with
access to this endpoint can read and control your local terminals. Do not enable
Funnel or forward a public port. The backend still listens only on loopback.

### macOS service

From this repository, create a per-user LaunchAgent using existing Python,
Herdr, and Tailscale installations (no packages are installed):

```bash
python3 - <<'PY'
import json, os, plistlib, shutil, subprocess, sys
from pathlib import Path
home, repo = Path.home(), Path.cwd()
host = json.loads(subprocess.check_output(["tailscale", "status", "--json"]))["Self"]["DNSName"].rstrip(".")
herdr = shutil.which("herdr")
assert herdr and (repo / "server.py").is_file(), "Run from this repository with herdr on PATH"
logs = home / "Library/Logs/herdr-webui"
logs.mkdir(parents=True, exist_ok=True)
agent = home / "Library/LaunchAgents/dev.herdr.webui.plist"
agent.parent.mkdir(parents=True, exist_ok=True)
config = {
    "Label": "dev.herdr.webui",
    "ProgramArguments": [sys.executable, "-u", str(repo / "server.py"), "--allow-host", host],
    "WorkingDirectory": str(repo),
    "EnvironmentVariables": {"HOME": str(home), "PATH": os.environ["PATH"], "HERDR_BIN": herdr},
    "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 5,
    "StandardOutPath": str(logs / "stdout.log"),
    "StandardErrorPath": str(logs / "stderr.log"),
}
with agent.open("wb") as f:
    plistlib.dump(config, f)
agent.chmod(0o600)
PY
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/dev.herdr.webui.plist"
tailscale serve --bg --https=443 http://127.0.0.1:8780
```

Stop any manually started instance first so port 8780 is free. The service starts
at login and restarts if the bridge exits. Herdr itself must already be running;
the Mac must remain awake. Serve's `--bg` configuration persists across Tailscale
restarts. After changing code, restart with
`launchctl kickstart -k "gui/$(id -u)/dev.herdr.webui"`.

See [SAMPLE_COMMANDS.md](SAMPLE_COMMANDS.md) for checks and troubleshooting.

## Security model

This UI can type into your terminals, so it is deliberately loopback-bound:

- Binds `127.0.0.1` only; rejects non-loopback `Host` headers unless an exact hostname is opted in with `--allow-host` (DNS-rebinding defense).
- Every mutation needs a per-process CSRF token embedded in the served page, plus `Content-Type: application/json`.
- Herdr is invoked with argv lists (never a shell); IDs, key names, labels, and agent kinds are allow-listed.
- Strict CSP (no external origins), `X-Frame-Options: DENY`, `no-store`.
- Agent session file paths from the snapshot are never sent to the browser.

Do not bind it to a public or LAN interface. Use the private proxy above or an
SSH tunnel: `ssh -L 8780:127.0.0.1:8780 host`. `--allow-host` does not add
application authentication; use it only behind a trusted private proxy.

## API

| Method | Path | Herdr call |
| --- | --- | --- |
| GET | `/api/tree` | `api snapshot` (normalized, cached 1s) |
| GET | `/api/read?pane=ID&lines=N&ansi=1` | `pane read --source recent-unwrapped` |
| POST | `/api/action/send` `{pane,text,enter}` | `pane run` / `pane send-text` |
| POST | `/api/action/keys` `{pane,keys[]}` | `pane send-keys` |
| POST | `/api/action/prompt` `{pane,text}` | `agent prompt` |
| POST | `/api/action/focus` `{pane,tab,agent}` | `agent focus` / `tab focus` |
| POST | `/api/action/split` `{pane,direction}` | `pane split --no-focus` |
| POST | `/api/action/close` `{pane}` | `pane close` |
| POST | `/api/action/spawn` `{workspace?,space_label?,tab_label?,kind?,name?}` | `workspace create` / `tab create` [+ `agent start`] |

## Test

```bash
python3 -m unittest test_server -v   # uses a fake herdr binary; no live server needed
```

## Roadmap

- Multiple hosts via `herdr --machine` (saved SSH machines).
- Event streaming instead of polling.
- Inline typing directly inside pane cards.

## License

MIT
