# Shell & Health-Check Recipes (verified)

Reusable, battle-tested patterns for this infra. Captured because the quoting
problem below cost 4+ failed attempts in a single session.

## PITFALL: inline curl with auth header / JSON keeps breaking

Symptom: commands containing `-H 'Authorization: Bearer ***...'` or inline
`-d '{"json":...}'` repeatedly fail with:
- `unexpected EOF while looking for matching '"'`
- `eval: line N: unexpected EOF`
- the API key extraction collapsing to empty (401 "keys start with mnfst_")

Root cause: nested single/double quotes + the bearer token + JSON braces get
mangled when the command is built as one inline string through the shell layer.

### THE FIX (do this every time, don't fight inline quoting)
1. Write the JSON payload to a file with `write_file` (not echo/heredoc).
2. Write the whole curl invocation into a `.sh` script with `write_file`.
3. Run `bash /path/to/script.sh`.

This sidesteps the shell-escaping layer entirely and is reliable. The same rule
applies to building these strings inside Python: build the command in parts and
concatenate (`auth = "Authorization: Bearer *** + key`) rather than one big
f-string — a multi-line f-string with the token inline silently truncates.

## Known-good: routing health check

`~/route_test.sh` already exists and is the canonical check. It reads the
`mnfst_` key inline, posts a tiny completion through the LB, and prints:
```
health primary :2099 -> 200
health backup  :8080 -> 200
routing POST -> HTTP 200
```
Payload file: `~/route_payload.json`. If recreating after loss, the key is
inline in `~/.hermes/config.yaml` under `providers.manifest-vision.api_key`.

## Manifest API key extraction (the reliable way)
Avoid fragile grep/cut pipelines (they collapsed to empty mid-session). Either:
- read it directly from config.yaml with a regex: `mnfst_[A-Za-z0-9_\-]+`, or
- `docker inspect mnfst-manifest-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^DATABASE_URL='` for the DB URL (no shell needed in the distroless container).

## Railway DB dump (PG version must match)
- Railway runs PG **18.x**; alpine `postgres:16` pg_dump fails with
  `server version mismatch`. Use `docker run --rm --network host postgres:18-alpine pg_dump "$DBURL"`.
- DNS for `acela.proxy.rlwy.net` resolves from the HOST but NOT from inside the
  default-network containers — use `--network host`.

## Watchdog
`~/.hermes/scripts/infra_watchdog.py` (cron `6537cacf1cd6`, every 15 min,
no_agent, silent unless P0/P1). Superset of the retired `heartbeat.py`
(cron-health + Honcho + Manifest) plus live routing POST, both-host Manifest,
gateway, backup nginx, disk. Anti-spam: 60-min cooldown via
`/tmp/infra_watchdog_state.json`, cleared on recovery. Detection+alert ONLY —
never remediates.

## Detached session-surviving work (gateway restart / reboot)
To run steps that kill the live session (gateway restart, host reboot), launch a
detached unit so it survives:
`XDG_RUNTIME_DIR=/run/user/0 systemd-run --user --unit=<name> /bin/bash ~/<script>.sh`
Report progress out-of-band with `~/tg-report.sh "<msg>"` (reads
TELEGRAM_BOT_TOKEN from .env, posts to chat <telegram-user-id>). Arm a `@reboot`
crontab hook for a "back online" confirmation — but verify health INSIDE the
hook before declaring success (a premature "back online" fired before containers
were actually up this session).
