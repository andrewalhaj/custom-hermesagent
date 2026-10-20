# Sector Boost Run — 2026-07-09

## Outcome snapshot

- Store: 202 facts → 195 after cleanup (7 deleted, full-row backup at
  `~/backups/knowledge-store/sector-boost-removals-20260709-1418.json`)
- Coverage: 181 classified / 14 unclassified-by-design (centroid abstentions — left alone per skill)
- CLI-garbage check: 0
- Flags verified on hermes-gateway + hermes-kb-daemon: SECTOR_BOOST=true, GRAPH_TRAVERSE=true
- Benchmark: launched in background (see "Runtime" below); report the post-write run only.

## NEW ROT SOURCE FOUND: session-heartbeat conversational fragments

The junk this pass removed was NOT CLI garbage (the audit query for `--text`/`--tags` found 0).
It was **session-heartbeat writes storing mid-conversation sentences as facts**, tagged
`session-heartbeat`. Examples removed:

- "The fixes are **live in the running process** (dispatcher was restarted) but not committed."
- "These are read-only commands plus a write to /tmp (non-config, ungated), so no greenlight needed."
- "The Playwright browser runs on the Mac, so it needs a network-reachable URL."
- "Config: OOTB = continuity" (truncated fragment)
- Session digest with outcome text "Done, complete, done, finished, pass"

**Audit query for this class** (add alongside the CLI-garbage check):

```sql
SELECT id, left(text,110), stored_at FROM knowledge
WHERE 'session-heartbeat' = ANY(tags)
   OR text ~* '^(The|These|It|That) [a-z]'  -- sentence-fragment shape
ORDER BY stored_at DESC;
```

Review hits manually — heartbeat facts CAN be legitimate; the tell is present-tense narration
of an in-flight task ("is running", "not committed", "already in use") vs. a durable fact.
Root fix belongs in the heartbeat's junk filter (`_is_junk()` in plugins/supabase), not in
repeated manual cleanup — flagged to Andrew 2026-07-09, not yet actioned.

## Runtime expectations (this box, 2026-07-09)

- The 50-query benchmark takes **>10 minutes** — embed round-trips to the Mac Studio per query.
  Run it `background=true notify_on_complete=true` and do NOT block the turn polling `wait`
  in 60s increments (the process tool clamps wait to 60s; nine sequential waits burned a turn).
- `eval.py` is a **library with no `__main__`** — running `python3 scripts/knowledge/eval.py`
  exits silently with 0. Invoke via:
  `cd ~/.hermes/scripts && python3 -c "from knowledge.eval import eval_benchmark; r=eval_benchmark(); ..."`
- Set `HF_HUB_OFFLINE=1` — otherwise sentence_transformers pings HF Hub on import (rate-limit
  warning noise, and slower start).
- Local-model "Loading weights" in the log = at least one remote embed FELL BACK to local
  768-dim. If the run is unexpectedly slow, check Ollama reachability via the module itself:
  `python3 -c "from knowledge.embed import EMBEDDING_API_URL; ..."` (embed.py reads .env
  directly; plain env vars may be empty in your shell).

## Deletion mechanics that worked

- PostgREST/SQL: 8-hex IDs from audit output are truncated UUIDs — match with
  `left(id::text,8) IN (...)`, and `DELETE ... RETURNING left(id::text,8)` confirms each removal.
- Backup BEFORE delete: `SELECT *` (includes vector column — rows are re-storable), save the
  persisted tool output to `~/backups/knowledge-store/`, then verify every target ID appears
  in the backup file before running the DELETE.
