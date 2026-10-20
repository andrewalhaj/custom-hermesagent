# Session Heartbeat — Watermark Bug

**Diagnosed:** 2026-07-08

The session heartbeat cron (`8896a47bc920`, no_agent script `session_heartbeat.py`, every 15 min) had been failing since 2026-07-06 with "Script timed out after 120s."

## Root cause

Two interacting bugs:

1. **Watermark saved only at exit.** `main()` in `session_heartbeat.py` calls `save_watermark(wm)` only at the END of the loop (line 188), after ALL active sessions are processed. If the 120s cron timeout kills the process mid-run, the watermark never advances → next run re-processes the identical backlog → identical timeout → permanent failure loop.

2. **Per-fact cost exceeds the 15-min interval budget.** `process_session()` calls `is_duplicate()` for every extracted fact, which calls `search()` from `scripts/knowledge/search.py`, which calls `_cosine_rerank()` for hits, which lazily loads the `mxbai-rerank-base-v1` cross-encoder from HuggingFace (~202 weight parameters). This loading alone takes measurable seconds on first call. Accumulated across sessions with large message volumes, the total exceeds 120s.

3. **No mid-run progress check.** The script has no wall-clock guard. It runs until all sessions are processed or the cron kills it.

The trigger: on or around July 6, a session with enough messages accumulated that extraction + dedup crossed the 120s boundary. Every subsequent run paid the same cost on the same backlog — plus growth from new messages.

## Failed runs

Last audit log entry: `2026-07-06T20:39:55` (normal run). All subsequent ~300 runs since have timed out with no watermark advancement.

State DB at time of diagnosis: 32,898 messages across 1,291 sessions. A manual run of the script timed out at 300s (the cron timeout is 120s).

## Fix (not yet applied)

1. Save watermark **per session** (inside the `for session_id in sessions:` loop), not only at exit. Progress survives a kill.
2. Cap wall-clock time per run: `time.monotonic()` guard, bail after ~30s of processing. The 15-min cron interval is ample — the script should process what it can and defer the rest to the next tick.
3. Optional: separate the embed model loading from the cron path — the warm `hermes-kb-daemon` already holds Qwen3 embeddings in memory (~550MB RSS at `~/.hermes/run/kb.sock`); the heartbeat shouldn't need to load `sentence_transformers` at all.

## Related: search NoneType error

`memory_search` → `unified_search()` → `search.py` → `_cosine_rerank()` can return results where `scores` is `None` (cross-encoder model loaded but `predict()` returned None for a single-pair input). The `scores.min()` call then throws `AttributeError: 'NoneType' object has no attribute 'min'` — caught by the bare `except` block, but the downstream iteration (`for h in hits`) fails with `TypeError: 'NoneType' object is not iterable` because the except path doesn't ensure `hits` is always a valid list.

Null-guard needed: validate `scores` is not None before `scores.min()`, and ensure all return paths produce a valid list.
