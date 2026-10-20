# Scheduler Recovery — Option A: Documented Recovery

## ⚠️ FAILURE MODE: One malformed job freezes the ENTIRE ticker silently (verified 2026-06-25)

Symptom: `hermes cron status` cheerfully reports `✓ Gateway is running — cron jobs will fire automatically` with N active jobs, **but nothing has fired for hours/days** — every job's `last_run_at` and `next_run_at` are stuck at the same past timestamp. The gateway is alive; the cron *tick* is dead.

Root cause: the tick iterates all jobs and calls `schedule.get("kind")` on each. If ONE job has its `schedule` field stored as a **bare string** instead of a dict (e.g. a hand-added or legacy one-shot with `"schedule": "once"` instead of `"schedule": {"kind": "once", ...}`), the tick throws `AttributeError: 'str' object has no attribute 'get'` in `cron/jobs.py:_get_due_jobs_locked` and aborts — taking down dispatch for ALL jobs, not just the bad one. There is no per-job isolation; one poisoned entry stops the whole scheduler.

Diagnose: `hermes cron tick` runs the tick in the foreground and surfaces the traceback (the gateway swallows it). Then find the offender — iterate `jobs.json`'s `jobs` list and flag any entry whose `schedule` is a `str` not a `dict`:
```python
import json
j = json.load(open(os.path.expanduser('~/.hermes/cron/jobs.json')))   # top-level dict: {"jobs": [...], "updated_at": ...}
for item in j['jobs']:
    if isinstance(item.get('schedule'), str):
        print('BAD:', item.get('name', item.get('job_id')), '→ schedule=', repr(item['schedule']))
```
Note `jobs.json` is a **dict with a `jobs` key**, not a top-level list — iterate `j['jobs']`, and skip the sibling `updated_at` string key.

Fix: back up first (`cp jobs.json jobs.json.bak-$(date +%s)`), then either normalize the malformed `schedule` to a proper dict or, if it's a stale already-run one-shot, drop the entry entirely:
```python
import json, shutil, time
shutil.copy(os.path.expanduser('~/.hermes/cron/jobs.json'), fos.path.expanduser('~/.hermes/cron/jobs.json.bak-{int(time.time())}'))
j = json.load(open(os.path.expanduser('~/.hermes/cron/jobs.json')))
j['jobs'] = [x for x in j['jobs'] if x.get('job_id') != '<offender-id>']
json.dump(j, open(os.path.expanduser('~/.hermes/cron/jobs.json'),'w'), indent=2, default=str)
```
Verify: `hermes cron status` now shows `next_run` in the **near future** (next tick boundary), not the old frozen timestamp. After repair, manually fire any jobs that missed their window with `hermes cron run <job_id>` (one id per call — `--all-due` is not a flag; the subcommand requires a single `job_id`). For overdue script-only (`no_agent`) jobs this is safe and zero-cost.

Prevention: never hand-write a job into `jobs.json` with a string `schedule`. Use `hermes cron create`/`add` which builds the dict shape. A one-shot uses `{"kind": "once", "run_at": "<iso>"}`, not `"once"`.

# Scheduler Recovery — Option A: Documented Recovery (backup/restore)

## State File

Scheduler state lives in `~/.hermes/cron/jobs.json` (~5KB). Contains every job's schedule, last run time, last status, next run time, and delivery targets.

## Backup Coverage

The daily 03:00 backup (`backup.sh`) includes `cron/` directory. At most 24 hours of scheduler state is unprotected — the database of record is `jobs.json`, not the execution history.

## Recovery Procedure

If the Hermes host is lost or the scheduler stops:

### 1. Restore the state file

```bash
# From the latest daily backup:
tar xzf ~/.hermes/backups/hermes-backup-YYYY-MM-DD.tar.gz -C /tmp/hermes-restore
cp /tmp/hermes-restore/cron/jobs.json ~/.hermes/cron/jobs.json

# Or if restoring to a new host:
scp ~/.hermes/backups/hermes-backup-YYYY-MM-DD.tar.gz user@new-host:/tmp/
ssh user@new-host 'tar xzf /tmp/hermes-backup-*.tar.gz -C /tmp/hermes-restore'
```

### 2. Start Hermes

```bash
hermes gateway start
# Scheduler reads jobs.json, picks up next_run_at, resumes
```

### 3. Verify

```bash
hermes cron list --all
# All jobs should show next_run_at in the future
```

## Expected Data Loss

- At most **one missed tick** per cron job — the tick that was due between the last backup and the outage.
- `no_agent` jobs (backup, dedup, heartbeat, watchdog) cost zero tokens — missed tick means zero cost.
- The one LLM job (Delegation Audit at 09:00) may skip a day. It self-corrects on next run (scans last 24 hours regardless).

## Recovery Time

Under 2 minutes from backup file to running scheduler.

## Test the Recovery (Dry Run)

```bash
# Create a test profile with a copy of the scheduler:
hermes profile create scheduler-test --clone-all
# Verify jobs load:
hermes --profile scheduler-test cron list --all
# Delete test profile after:
hermes profile delete scheduler-test
```

## Gaps This Doesn't Cover

- **Multi-Hermes HA**: Only one Hermes instance runs cron. No leader election.
- **Automatic failover**: Recovery is manual — restore backup, restart.
- **Real-time replication**: The 24-hour backup gap means a crash at 02:59 loses a full day of scheduler state. Acceptable at current scale (5 jobs, 4 of which are no_agent/zero-cost).
