# Rewriting a stale tracking board after a project pivot

When a project's architecture changes and its existing tracking cards describe the OLD design,
the board is **stale, not editable** — `kanban edit` can't restructure card semantics. The move
is a full rewrite: archive the superseded cards, recreate against the new architecture, keep the
artifacts you can still trust. Proven 2026-06-09 (DM Voice Board: turn-based ElevenLabs TTS web
app → real-time RVC voice changer; 8 cards archived, 7 recreated).

## Procedure (gate it — this mutates the board)

1. **Inspect the live board first.** Read card titles AND bodies before judging staleness — a
   title may look fine while the body carries the dead architecture:
   ```python
   import sqlite3
   c=sqlite3.connect(os.path.expanduser('~/.hermes/kanban.db')); c.row_factory=sqlite3.Row
   for r in c.execute("SELECT id,title,status,body FROM tasks WHERE title LIKE '<project>%' AND status!='archived'"):
       print(dict(r))
   ```
   Note: the body column is `body` (not `description`) — confirm with `PRAGMA table_info(tasks)`.

2. **Present the rewrite for greenlight.** This archives N cards — show the old→new mapping,
   what survives, the rollback. Wait for "proceed." A board rewrite is a gated change.

3. **Back up the board first:** `cp ~/.hermes/kanban.db ~/.hermes/kanban.db.bak-$(date +%Y%m%d-%H%M%S)`.
   Rollback = restore the `.bak` (every archived card comes back).

4. **Archive the stale cards** (reversible — archive, NOT delete):
   `for id in t_a t_b t_c; do hermes kanban archive $id; done`

5. **Recreate as inert tracking cards** — `--initial-status blocked`, no `--assignee`,
   `--idempotency-key <task-date>` each, priority-ordered so the dashboard renders the phases in
   sequence. (See SKILL.md "Inert tracking cards" for the full inert-card doctrine.)

6. **Re-block after EVERY board mutation.** Creating, and especially `complete`, fire a gateway
   tick that re-promotes sibling `blocked`→`ready`. After the batch and after each `complete`,
   loop the positional block form and re-verify until all non-done cards read `blocked`:
   `for id in ...; do hermes kanban block $id "Inert tracking card — not for dispatch"; done`
   (`--ids` alone errors — see SKILL.md.)

7. **Complete a card ONLY against a verified on-disk artifact.** A guide/doc card is `done` only
   after you `test -f` the file AND spot-check its content is on the NEW architecture
   (`grep -ci` the forbidden old terms → must be 0). Never complete on intent or a subagent's
   self-report.

## Pairs with the docs rewrite

A board pivot usually rides alongside a design-doc pivot. When you overwrite an active design doc,
**preserve the superseded version** to `docs/_archive/<date>-<name>-SUPERSEDED.md` with a
do-not-build header BEFORE/AFTER overwriting (write_file overwrites in place — if you didn't copy
first, reconstruct the archive from the content you already read into context this turn). Mark the
old design's content invalidated, state WHY it was superseded, and point to the active doc.

## Refresh the dashboard immediately, don't wait for the tick

The wall-dash Projects tab renders `kanban.db` via `~/.hermes/scripts/kanban_export.py` every
5 min (cron "Kanban Dashboard Export", `every 5m`). To reflect a rewrite now, run it manually:
`python3 ~/.hermes/scripts/kanban_export.py`. It writes `/tmp/kanban-state.json` (shape:
`{boards:[{name,tasks:[...]}], totals:{status:n}}` — tasks are nested under `boards[0].tasks`,
NOT top-level) and scp's it to the wall-dash host. Verify by parsing that JSON, not by guessing
top-level keys. ACTIVE statuses rendered: ready/running/blocked/todo/triage; archived/done are
filtered or shown separately.
