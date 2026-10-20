# Detached Update Runner: surviving the gateway restart that kills your session

## When to use

Any `hermes update` driven from INSIDE a gateway session (CLI/Telegram/Discord). The update
restarts the gateway and kills the controlling session mid-command. A detached runner completes
the full sequence and reports out-of-band, so a severed session can't leave you half-updated
with the OAuth bypass down.

## Pre-flight (verify BEFORE building the runner)

```bash
hermes update --check                    # confirm commits pending
which systemd-run                        # detachment mechanism
systemctl --user list-units | grep hermes-gateway   # enumerate gateways to verify after
ls ~/hermes-claude-auth/install.sh   # bypass reinstall source must exist
```
Confirm `systemd-run` actually detaches as expected with a throwaway unit first:
```bash
systemd-run --unit=detach-test --collect /bin/bash -c 'sleep 2; echo ok > /tmp/dt.txt'
```

## Runner sequence (each step reports to Telegram via Bot API)

1. Pre-update full snapshot (`hermes profile create pre-update-YYYY-MM --clone-all`) — rollback point.
2. `hermes update --yes --backup` — **FATAL-abort if it fails**; system stays on old code.
3. `hermes config migrate` (bare, no flags) on default + each satellite (see update-cadence.md).
4. Reinstall OAuth bypass: `cd ~/hermes-claude-auth && HOME=/root ./install.sh`; verify
   sitecustomize present; **re-verify customizations survived** (install.sh ships vanilla).
5. Restart gateways — NOTE: `systemctl --user` fails from a system-level systemd-run unit
   (no user bus). The update already restarts them; this step is redundant/harmless.
6. Live Anthropic health test: `hermes chat -q 'Reply with exactly: AUTH OK' --provider anthropic -m claude-sonnet-4-6 -Q`.
7. Final report.

## Out-of-band Telegram reporting (gateway is DOWN during the run)

In-session delivery won't work — the gateway is restarting. Use the Bot API directly:

```bash
# Read token at RUNTIME from .env — NEVER inline it (credential filter corrupts inline tokens
# in heredocs / python -c / shell vars; writing the curl inside a script file is the reliable
# bypass). chat_id is the user's DM id.
TOKEN="$(grep -E '^TELEGRAM_BOT_TOKEN=' ~/.hermes/.env | head -1 | cut -d= -f2- | tr -d '\r\n')"
curl -s --max-time 20 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  --data-urlencode "chat_id=<telegram-user-id>" \
  --data-urlencode "text=...status..." -d "disable_web_page_preview=true"
```
Pre-flight the path with a real test message + `getMe` before launching the real update.

## Launch detached

```bash
systemd-run --unit=hermes-update-$(date +%s) --collect bash ~/.hermes/scripts/hermes-update-runner.sh
```
Then verify the unit is `active (running)`. The driving session goes dark when the gateway
cycles; progress arrives on Telegram.

## After the runner finishes — VERIFY, don't trust the "done" line

A `no_agent` runner's final "🏁 finished" can print even when mid-steps failed (config migrate
errored on a removed flag, restart failed on the user-bus issue, install.sh hung). Always:
```bash
hermes --version                                          # new version live?
cd /usr/local/lib/hermes-agent && git rev-parse --short HEAD
hermes config check | grep -i version                    # config on new schema?
ps -o pid,lstart,cmd -p $(pgrep -f 'hermes.*gateway run' | tr '\n' ,)  # gateways restarted AFTER update?
hermes chat -q 'Reply with exactly: AUTH OK' --provider anthropic -m claude-sonnet-4-6 -Q
tail -30 ~/.hermes/logs/update-runner-*.log              # read for hidden ❌ steps
```
