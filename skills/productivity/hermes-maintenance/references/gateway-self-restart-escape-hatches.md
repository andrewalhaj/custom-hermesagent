# Restarting a Hermes-managed service from INSIDE a session

Session-verified 2026-06-23. You need to `systemctl restart hermes-dashboard`
(or any service whose process tree includes the gateway) to make it reload a file
it read at import time — e.g. a rotated `.dashboard_passwd_hash`, a changed
`config.yaml`, a new env var. Every in-session path is blocked. Here is the full
map of what fails and what actually works, so the next session doesn't burn turns
re-discovering it.

## Why "read at import time" is the trap

Many services load state ONCE at process start. Example: the dashboard's
`routes/auth.py` does `_PASSWORD_HASH = _HASH_FILE.read_text().strip()` at module
import. Editing the hash file on disk changes NOTHING for the running process —
it still authenticates against the hash it loaded at boot. Symptom: "I fixed the
file but the password still doesn't work." The file is correct; the process is
stale. It MUST restart to re-read. Verify which hash the live process is using
with a direct probe, not by reading the file:
`curl -s -X POST http://localhost:8787/api/auth/login -H 'Content-Type: application/json' -d '{"password":"<pw>"}'`
→ `{"ok":false}` while the on-disk hash is correct = stale process, needs restart.

## What is BLOCKED (do not keep retrying these)

1. **`systemctl restart <svc>` foreground** → WRITE GATE (expected; arm + retry).
2. **`systemctl restart <svc>` AFTER arming the gate, foreground** → gateway
   self-protection: *"cannot restart or stop the gateway from inside the gateway
   process. The gateway would kill this command before it could complete (SIGTERM
   propagates to child processes)."* This fires for any service in the gateway's
   own process tree.
3. **`terminal(background=true)` `systemctl restart`** → same self-protection block.
4. **`nohup bash -c '... systemctl restart ...'` / `setsid` / trailing `&`** →
   refused by the terminal tool: *"Foreground command uses shell-level background
   wrappers (nohup/disown/setsid). Use terminal(background=true)..."* — and even if
   it ran, it's still in the gateway tree (see #3).

## The reliable escape hatch: no_agent cron that runs `systemctl` — with the catch

The intended mechanism is a `no_agent` cron job whose `script` runs the restart.
It executes in the SCHEDULER's process tree, OUTSIDE the gateway, so the SIGTERM
self-protection doesn't apply. Script (under `~/.hermes/scripts/`, chmod +x):

```bash
#!/bin/bash
systemctl restart hermes-dashboard
```

**THE CATCH (verified 2026-06-23):** creating the job via the `cronjob` tool
(`action=create`, `no_agent=true`, `schedule='1m'`) returned success and a job_id,
but the job **never appeared in `~/.hermes/cron/jobs.json`** and the scheduler
never fired it. `cronjob action=list` showed the job; `action=run <job_id>`
returned "not found"; jobs.json on disk did not contain it. The scheduler reads
`jobs.json`, so a job that isn't persisted there is a no-op. This bit TWICE in one
session — both one-shot restart jobs silently failed to fire.

### Robust restart recipe (use this, don't trust a single cronjob-tool call)

1. Write the restart script to `~/.hermes/scripts/restart_<svc>_once.sh`, chmod +x.
2. Create the no_agent cron via the tool.
3. **VERIFY IT PERSISTED before trusting it:** confirm the returned job_id is
   actually present in `jobs.json` (load the JSON, search for the job_id). If it
   is NOT there, the tool's in-memory create didn't flush — the job will never run.
4. If it didn't persist, the durable fix is to write the job entry directly into
   `~/.hermes/cron/jobs.json` (GATED — it's a cron file; arm the write gate and get
   explicit greenlight first), matching the shape of an existing `no_agent` entry
   (`job_id`, `name`, `script`, `schedule`, `repeat:1`, `no_agent:true`, `enabled:true`,
   `deliver:"local"`, `next_run_at` ~1 min out). The scheduler picks it up next tick.
5. After the restart fires, verify with the live probe (the curl login test above,
   or check the serving PID's start time changed) — not by re-reading the file.

## Cleanest option when available: hand it to the user

A non-gateway shell on the host runs the restart with zero friction:
`hermes gateway restart` (the documented self-restart command, run OUTSIDE the
gateway) or a plain `systemctl restart <svc>` over SSH / a separate terminal.
When the cron path is flaky and the user is present, giving them the one-line
command is faster and more reliable than fighting the scheduler.
