# Service checks

First run: connect Tailscale to your tailnet, start Herdr, then follow the macOS
service setup in [README.md](README.md). Run repository commands from its root.

1. Run regression tests without touching the live Herdr server.

```bash
python3 -m unittest test_server -v
# Expect: OK; tests use a fake Herdr executable.
```

2. Check the installed per-user service.

```bash
launchctl print "gui/$(id -u)/dev.herdr.webui"
# Expect: state = running and a pid.
```

3. Verify that the local API can reach Herdr (prints counts, not terminal output).

```bash
curl --fail --silent --show-error --max-time 25 http://127.0.0.1:8780/api/tree | python3 -c 'import json,sys; p=json.load(sys.stdin); print("spaces:", len(p["workspaces"]), "panes:", len(p["panes"]))'
# Expect: space and pane counts matching the local Herdr server.
```

4. Check private HTTPS routing.

```bash
tailscale serve status
# Expect: an HTTPS URL, tailnet-only access, proxying http://127.0.0.1:8780.
```

5. Restart after updating the bridge.

```bash
launchctl kickstart -k "gui/$(id -u)/dev.herdr.webui"
# Expect: command succeeds; the service returns to running with a new pid.
```

6. On an iPhone connected to the same tailnet, open the URL from step 4 in Safari.

```text
https://<machine>.<tailnet>.ts.net/
```

Expect the Host > Space > Tab > Pane tree and live pane output. Safari's Share >
Add to Home Screen makes a convenient shortcut.

| Failure | Fix |
| --- | --- |
| Service exits: address already in use | Stop the old manually started bridge on port 8780; launchd retries. |
| HTTP 421 / bad host | Ensure the LaunchAgent's `--allow-host` exactly matches Serve's hostname; restart. |
| API returns Herdr unavailable | Start Herdr under the same macOS user; verify `HERDR_BIN` in the LaunchAgent. |
| iPhone cannot connect | Connect both devices to the same tailnet, check tailnet access rules, and keep the Mac awake. |
| HTTP 403 after a restart | Reload the page to obtain the new per-process CSRF token. |
