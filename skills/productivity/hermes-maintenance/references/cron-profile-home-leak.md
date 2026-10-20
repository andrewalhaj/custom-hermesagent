# Cron "Script not found" — profile-home leak in the scheduler

Worked example, 2026-06-10 02:01 UTC: two default-profile `no_agent` script jobs
(`projects_tab_ping.py`, `infra-watchdog.py`) failed with
`Script not found: ~/.hermes/profiles/ha-bot/scripts/<name>.py` — note the
**wrong profile** in the path. Both scripts existed at `~/.hermes/scripts/`.
Next tick: both ok. Self-recovering, recurring.

## Root cause (read from source, `cron/scheduler.py`)

- `_job_profile_context` (≈ line 238) runs a job under a different profile by:
  1. setting a **context-local** ContextVar override (`set_hermes_home_override`) — correctly scoped;
  2. **also assigning the module-global `_hermes_home`** — NOT scoped;
  3. mutating `os.environ` (snapshot/restore on exit) — NOT scoped.
- Profile jobs run in a **sequential pool** (max_workers=1) to isolate that mutation
  from each other — but **parallel-pool jobs firing in the same window read the
  leaked global** via `_get_hermes_home()` (line ≈ 226: `return _hermes_home or get_hermes_home()`).
- A script job resolving `scripts/<name>` against the leaked home →
  `profiles/<other>/scripts/<name>` → not found.

**Trigger condition:** any job with `profile: <non-default>` fires on the same tick
as a relative-path script job. Three ha-bot memory jobs ran at the 02:00 tick;
collision confirmed by output timestamps in `cron/output/<job_id>/`.

The same leak window plausibly explains **intermittent Discord 404 delivery errors**
on otherwise-healthy jobs (wrong-profile config loaded at delivery time). Verify the
route with a manual `send_message` to the target before chasing the channel itself.

## Trap: the "obvious" fix is rejected AND a placebo

Pinning `script` to an absolute path fails twice:

1. **API rejects it.** `cronjob update script=~/.hermes/scripts/x.py` →
   `"Script path must be relative to ~/.hermes/scripts/"`. Scheduler security
   (anti-path-traversal, `scheduler.py` ≈ 984–1002) requires bare filenames,
   validated with `path.relative_to(scripts_dir_resolved)`.
2. **Even if accepted it wouldn't help.** The containment check resolves
   `scripts_dir` from `_get_hermes_home()` — i.e. against the **leaked** home —
   so during a leak window an absolute default-profile path fails
   "outside the scripts directory" instead of "not found". Same failure, new message.

Lesson: **read the resolution + guard code before proposing a path fix.** The guard
rejecting the edit revealed the fix was wrong.

## Diagnostic recipe

```bash
# 1. Which jobs have profiles / scripts? (jobs.json may be ~/.hermes/cron/ or profiles/<p>/cron/)
python3 -c "import os; import json; [print(j['id'], j.get('profile'), j.get('script'), j['name']) for j in json.load(open(os.path.expanduser('~/.hermes/cron/jobs.json')))['jobs']]"
# 2. Correlate the failure tick: what else ran within ±2 min?
ls -la ~/.hermes/cron/output/*/2026-06-10_02-0*
# 3. Confirm transient: did the SAME job succeed on the next tick? (last_status in jobs.json)
# 4. Gateway log (remember: systemd --user)
export XDG_RUNTIME_DIR=/run/user/$(id -u)
journalctl --user -u hermes-gateway.service --since "<window>" --no-pager | grep -iE "profile|Script not found"
```

Note: `grep -rl <job_id> ~/.hermes/cron` explodes on `cron/output/` (hundreds of
per-run md files). Query `jobs.json` directly instead.

## Fix options, ranked by leverage

1. **Config-level, update-proof (preferred):** set `profile: "default"` on every
   relative-path script job (`cronjob update job_id=<id> profile=default`).
   Forces them into the sequential pool with an explicitly-correct home — immune
   to the leak, no core patch. Cost: script jobs can queue behind a long profile
   agent job on the same tick (minutes of delay, occasional).
2. **Core patch:** remove the module-global `_hermes_home` assignment in
   `_job_profile_context` (the ContextVar already scopes correctly). Surgical but
   reverts on update — must be re-applied by hand after each update (patch_guard retired 2026-10-16). Candidate for an
   upstream issue (same family as #18594 referenced in `hermes_constants.py`).
3. **Accept:** rare same-tick collision, self-recovers next run; cost = alert noise.

## Related

- Memory entry "HERMES_HOME leak family" — `hermes config set` writing to the wrong
  profile is the CLI face of the same class.
- Cleanup precedent: one-shot completion-ping crons (latch-pattern watchers) become
  permanent idle churn once their task completes — remove them once latched.
