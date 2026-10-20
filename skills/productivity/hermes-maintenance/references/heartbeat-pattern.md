# Heartbeat Pattern: Silent Infrastructure Monitoring

Daily zero-token watchdog that checks all critical infrastructure and stays silent when healthy. Reports only failures to the user's DM.

## What it checks

1. **Cron job health** — reads `~/.hermes/cron/jobs.json`:
   - Skips disabled jobs
   - Flags `last_status=error` (exception during run)
   - Flags `last_status=timeout` (job exceeded limit)
   - Flags "scheduled but never executed" (>2 hours past next_run_at, never ran)

2. **Manifest router** — `GET http://localhost:2099/health`
   - 200 = alive, anything else = alert

3. **Honcho API** — `GET https://api.honcho.dev/health`
   - 200 = reachable, anything else = alert

## Implementation

Script at `~/.hermes/scripts/heartbeat.py`. Pure stdlib Python — no Hermes SDK, no LLM. Exit code 0 + empty stdout = all healthy.

Cron setup:
```
no_agent: true
deliver: <user's DM>
schedule: 0 6 * * *  (daily at 06:00 UTC)
script: heartbeat.py
```

## Behavior

- **Healthy:** Zero output, exit 0. No message delivered. User hears nothing.
- **Broken:** Prints alert summary to stdout, exit 1. `no_agent` cron delivers stdout verbatim as a Telegram DM.

Example alert output:
```
⚠ 2026-06-04 06:00 — 2 alert(s):
  • Daily Hermes Backup: last run failed (status=error)
  • Honcho unreachable: HTTPError 503
```

## Why this pattern

The Honcho→Obsidian bridge and daily backup are `no_agent` scripts with `deliver: local` — no visibility. A dead bridge can rot unnoticed for weeks. The delegation audit delivers to Discord so you'd notice silence eventually, but the heartbeat catches all of them in one daily sweep with zero token cost.

## Adding new checks

Append to the `FAIL` list in the script. Keep it stdlib-only — if a check needs `pip install`, it should be a separate watchdog.
