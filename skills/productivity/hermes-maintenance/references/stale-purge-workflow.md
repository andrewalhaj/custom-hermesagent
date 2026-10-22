# Stale-state purge: audit → scripted archive-then-delete → one armed apply

Use when the user asks to "purge / retire / clean out everything stale" across the Hermes host. The deep-audit
checklist (SKILL.md §0) finds the state; this is the procedure that removes it without losing anything.

## 1. Parallel READ-ONLY auditors, one per surface

Fan out 5–6 subagents at once (filesystem+backups+cache, code+env flags+guards, skills, cron+systemd+logs,
memory layer, repos+kanban+sessions). Every brief carries the same contract:
- READ-ONLY; one markdown report with a table `| # | item | size | evidence (command + output) | verdict DELETE/ARCHIVE/RETIRE/KEEP | risk + rollback |`.
- Every DELETE needs a receipt that nothing references it: grep across `scripts plugins skills cron hooks config.yaml .env`,
  `~/.config/systemd/user`, `crontab -l`, and the dispatcher repo. "Not checked" is a legal cell; a bare verdict is not.
- Managed interpreter path, host aliases, never real IPs or employer names in the report.
- **Report path must not trip the write gate.** The gate scans content, so a report that *names* `config.yaml.bak`, `.env`
  or `kanban.db` gets an approval prompt the auditor cannot answer; it expires and the auditor (correctly) stops. Have
  auditors write under `cache/scratch/<area>/` and keep their table in a `rows.json` too, so a blocked final write is
  recoverable by the orchestrator (render rows.json yourself; do not re-run the audit).
- Anything a subagent must read from Supabase: dump it yourself first (`_supa_select('select=<cols>&order=stored_at.asc&offset=N&limit=500')`, paged;
  the column is `stored_at`, there is no `created_at`) to a jsonl the auditor reads — a subagent's own query will hit the gate.
- A steer sent after a child finishes is lost; check `delegate_task(action='list')` before steering, and re-dispatch the
  missing part as a new task that reads the dump.

Expect premise corrections in the reports (counts drift, "two X skills" is one, a flag is `enabled: false` not removed) —
that is the audit working; carry the corrected facts into the scripts.

## 2. Scripts, not hands: dry-run default, `--apply` archives first

One generator subagent per report (or pair) writes `cache/scratch/purge/purge_<area>.py` with this contract, and runs only `--dry-run`:
- A list of `(path, kind, reason)` at the top; a generic loop below. Idempotent; final line `RECLAIMED <bytes>`.
- `--apply`: tar every target (zstd, RELATIVE paths from `/`) into `~/backups/purge-<date>/<area>.tar.zst` + a manifest
  JSON (path, size, sha256, mtime), **verify the tar lists every path, then delete**. Files with credentials (`.env.bak*`,
  password-hash backups, scripts with embedded keys, `honcho.json`) go to a separate `<area>-secrets.tar.gz` mode 600 and
  are listed in the summary → that list is the user's rotation list.
- Deletions via `shutil`/`os`, never shell globs; `git worktree remove --force` for registered worktrees; `git branch -D`
  only for refs `git branch --merged master` / `git cherry` prove merged; unit files copied into the tar before removal,
  then `systemctl --user daemon-reload`; big append-only logs are archived whole then **truncated** (writers hold them open).
- Never-touch list in every brief: the newest pre-update zip (it is the rollback), live DBs, `.env`/`config.yaml` themselves
  (keys are commented out after a 600-mode backup, never deleted), anything modified <24 h unless the report says so,
  model caches a live fallback path still loads (store-gate local backend → `cache/laya-exp/{venv,models}`).
- Supabase rows: tiered (`--tier 1` test/dupe/dead-integration/credential rows, `--tier 2` superseded snapshots, `--tier 3`
  digests/fragments = owner review only); write every row to `knowledge-deleted-tier<N>.jsonl` BEFORE deleting through
  `knowledge_maintenance.delete_fact(..., reason='purge-<date>-tier<N>')`; cross-check every id against the dump and skip unknowns.
- Judgment items are NOT scripted: skill merges, description rewrites, cron prompt fixes, kanban archiving (no API field
  for it — `hermes kanban archive <ids>`), session pruning, system-scope `sudo systemctl` — they go to `<area>_fixes.md`
  / `kanban_review.md` for the user.

## 3. Apply

1. Skim each dry-run: counts per verb, that live DBs/`scripts/knowledge`/`plugins` never appear, that secrets are in the separate tar.
2. `write_gate.py arm "purge-<date>: <user's words> — <areas>" --ttl 1800` in its OWN terminal call.
3. Run `--apply` in order: fs/code → `--env-keys` → skills → cron/systemd/logs → repos → memory tier 1 → tier 2 → `--refs`.
   Check after each: the target class is gone (`ls | grep -c`), the tar exists, exit 0.
4. `write_gate.py disarm`. `du -sh` the backup dir and `df`: **net free space does not move until the tar leaves the box** — say so,
   and ask where it goes (GPU host / external) before deleting the local copy.
5. `memory_add` one row: what was removed per area, backup paths, the held items, the rotation list.

## Pitfalls

- A gateway restart kills every in-flight subagent: never queue one while auditors or generators run; batch flag flips behind the audit.
- Restored-from-archive skills keep `state: archived` in `skills/.usage.json`; clear it or the curator re-archives them.
- Retired skills that are git-tracked still need `git rm` on master; the move alone leaves them "deleted" in `git status`.
- A test suite that writes audit lines to the live log (`cold-store-audit.log`) pollutes the very evidence the audit reads; stub the logger in fixtures.
- The 10-day "review the shadow" cron written when a gate was in log mode is stale the moment the gate enforces — rewrite its prompt in the same change.
