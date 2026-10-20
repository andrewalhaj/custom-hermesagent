# Web dashboard (`hermes dashboard`) — launch and expose it off-box

Upstream ships a React dashboard in `<install>/web/` (config, keys, sessions). Distinct from any
user-built dashboard in another repo — when the user says "the webui", ask which if ambiguous.

## Commands

```
hermes dashboard                # npm build of web/ (first run, 1–2 min) + serve 127.0.0.1:9119 + open browser
hermes dashboard --no-open      # headless box
hermes dashboard --skip-build   # reuse existing web/dist
hermes dashboard --status | --stop
hermes serve                    # backend only (what the desktop app talks to)
```

Ready marker on stdout: `HERMES_DASHBOARD_READY port=9119`.

## Make it survive the terminal

A foreground `hermes dashboard` dies with the SSH session — the symptom downstream is a reverse
proxy 502 with `dial tcp 127.0.0.1:9119: connection refused` in `journalctl -u tailscaled`.
Launch it as a transient user unit instead:

```
systemd-run --user --unit=hermes-dashboard-$(date +%H%M%S) --collect -p WorkingDirectory=/root \
  hermes dashboard --no-open --skip-build
```

Do not prefix a defensive `systemctl --user stop <unit>` for a unit that may not exist — the
write gate matches the verb and blocks the whole launch line. Stop later with
`systemctl --user stop hermes-dashboard-<stamp>` or `hermes dashboard --stop`.

Health: `ss -tlnp | grep 9119; curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9119/`.

## Reaching it from another machine — pick one

**A. SSH tunnel (zero config, works immediately):**
`ssh -L 9119:127.0.0.1:9119 root@<tailscale-ip>` → `http://localhost:9119`. Host header is
`localhost`, so the rebinding guard (below) passes. Use this for "just let me look at it".

**B. `tailscale serve` (persistent, HTTPS, tailnet-only):**
`tailscale serve --bg 9119` → `https://<node>.<tailnet>.ts.net/`. Undo: `tailscale serve
--https=443 off`. Two things must be true before the page renders, in this order:

1. **Host-header guard.** `hermes_cli/web_server.py` rejects any `Host` that isn't
   loopback or the configured public hostname (DNS-rebinding defense). Symptom: JSON
   `"Invalid Host header. Dashboard requests must use the bound hostname or the configured
   public hostname."` Fix: `hermes config set dashboard.public_url https://<node>.<tailnet>.ts.net`
   (env: `HERMES_DASHBOARD_PUBLIC_URL`).
2. **Auth becomes mandatory.** A non-loopback `public_url` makes `should_require_dashboard_auth`
   true, and the dashboard refuses to start without a provider. Built-in basic-auth plugin
   (`plugins/dashboard_auth/basic`) needs BOTH a username and a scrypt hash — there is no
   default username:
   ```
   cd <install> && venv/bin/python -c "from plugins.dashboard_auth.basic import hash_password; print(hash_password('PW'))"
   hermes config set dashboard.basic_auth.username <name>
   hermes config set dashboard.basic_auth.password_hash '<hash>'
   ```
   (env: `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `_PASSWORD_HASH`). Alternative provider:
   `hermes dashboard register` for Nous Portal OAuth. Then restart the unit.

   The agent must not run the hash step or see the password — hand the user the three commands
   and tell them the username they chose when the login form appears.

`--host 0.0.0.0` is strictly worse than B: same auth requirement, no TLS, no tailnet identity.
`--insecure` is a no-op since the loopback-auth hardening.

## Verification limits

`curl https://<node>.ts.net/` **from the serving box itself** times out — not a failure signal.
Verify from a second tailnet node, or check `ss -tlnp | grep :443` (tailscaled listening) and
the tailscaled journal for the proxied request. First remote hit may stall 10–30 s on Let's
Encrypt issuance; HTTPS certs must be enabled in the tailnet admin console (DNS → HTTPS).