# Memory Provider Migration: file → Supabase (agent-driven, no prefetch)

Migrating Hermes hot memory off the `file` provider (MEMORY.md injected every turn)
onto a **Supabase pgvector** provider where the agent SEARCHES its own memory with a
tool instead of having it pre-injected. End state: no MEMORY.md read, no B-full
injection patch, no prefetch layer. The agent owns retrieval.

## The architecture decision (why no prefetch)

`prefetch()` and the B-full gateway patch both exist to compensate for a *blind*
`file` provider — they inject search results before the model sees the message.
If Supabase IS the memory layer and the agent has a `memory_search` tool, that
compensating injection is redundant duplication. Strip it. The provider needs only
THREE responsibilities:

1. **`on_memory_write`** — intercept built-in `memory()` tool calls, write to Supabase instead of MEMORY.md.
2. **`get_tool_schemas`** — expose `memory_search`, `memory_add`, `memory_remove`.
3. **`system_prompt_block`** — one paragraph telling the agent its memory is in Supabase and to `memory_search` before answering infra/config questions.

Provider lives at `~/.hermes/plugins/<name>/__init__.py`, subclasses
`agent.memory_provider.MemoryProvider`, registered via `register(ctx)` →
`ctx.register_memory_provider(...)`. The `_add`/`on_memory_write` paths call the
knowledge-store `store()` (loaded by importlib from `~/.hermes/scripts/knowledge.py`).

## Migration sequence (gated steps marked)

1. **Migrate facts FIRST, while the old provider is still live.** Read MEMORY.md
   entries, `store()` each into Supabase. Verify a `memory_search` round-trips
   before you cut over. Never delete the source before the cold copy is proven.
2. **GATED — remove the compensating injection** (B-full block in `gateway/run.py`:
   the `_bfull_*` functions + the injection call). Verify `grep -c _bfull` == 0 and
   `python3 -c "import ast; ast.parse(open('gateway/run.py').read())"` clean.
3. (Was: remove patch_guard goldens for B-full — patch_guard retired 2026-10-16, nothing to do.)
4. **GATED — flip the config:** `memory.provider: file → supabase` in `config.yaml`
   (python3 yaml round-trip — `patch`/`write_file` refuse config.yaml).
5. **GATED — restart the gateway.** Config change does NOT take effect in the live
   session; the running process still injects MEMORY.md until restart. Do NOT claim
   "memory is now database-backed" before the restart — verify with a live
   `memory_search` AFTER it.
6. **Compact then delete MEMORY.md.** Once the provider no longer reads it, MEMORY.md
   becomes stale cruft. Replace with a tombstone or delete (keep the `.bak`).

## Verify cutover actually happened

The trap: config says `supabase` but the running gateway predates the restart, so
MEMORY.md is *still* in context. Ground truth is a live tool call:
`memory_search(query="<distinctive recent fact>")` — a real hit from Supabase
(not from the injected MEMORY.md block) proves the cutover. Until then, report
"config flipped, restart pending" — not "done."

## KEEP USER.md on the file provider — do NOT migrate it

MEMORY.md (infra facts) migrates cleanly. **USER.md does NOT.** USER.md holds
behavioral preferences — tone, correction patterns, communication style — that must
inject EVERY turn with zero retrieval threshold. Route them through probabilistic
Supabase search and a low-scoring query silently drops the user's preferences
mid-session. The `file` provider's guaranteed injection is exactly the property you
WANT for USER.md. Migrate MEMORY.md; leave USER.md hot. (Recommend against it when
asked — this is the correct call, not a limitation.)

## Auto-classify + junk-filter on write (close the sector_path backlog)

After cutover, every new fact hits Supabase with no `sector_path` → invisible to the
Fibonacci sector boost → manual "sector boost it" backlogs forever. Fix at the
provider's write path (`_add` AND `on_memory_write`):

- **`_is_junk(content)` BEFORE store:** reject <30 chars, task-progress prefixes
  (`Done.`/`Fixed`/`Phase `/`PR #`/`Commit `/`Step `/`Task `/`Session started`/`Turn `),
  bare-URL-only (`^\s*https?://\S+\s*$`). Task noise never enters the DB.
- **`_auto_classify(content, fact_id)` AFTER a successful store:** call
  `knowledge/classifier.py::classify()` → if non-empty `sector_path`, PATCH the row
  (`PATCH {url}/rest/v1/knowledge?id=eq.{fact_id}`, body `{"sector_path":[...]}`,
  header `Prefer: return=minimal`). Classification/PATCH failure logs a warning but
  must NOT fail the store — the fact is already saved.

## Board-ops aside: `hermes kanban update --status` SILENTLY no-ops

Unblocking/archiving cards in bulk: `hermes kanban update <id> --status archived`
(and `--status ready`) returns no error but does NOT change the row — re-query and
the status is unchanged. Two reliable paths instead:
- **`kanban_*` MCP tools** — `kanban_unblock(task_id=...)` flips blocked→ready and
  returns `{"ok": true, "status": "ready"}`. Use for single-card state changes.
- **Direct DB UPDATE** for bulk — `UPDATE tasks SET status='archived' WHERE id IN (...)`
  against `~/.hermes/kanban.db` (no `sqlite3` CLI on host; use python3 `sqlite3`
  module). ALWAYS re-`SELECT id,status` to confirm the write landed before reporting.

## Classifier note (continued)

Note the classifier's centroid stage abstains on very short snippets (they score
0.01–0.12 vs the 0.40 L0 threshold), so short facts fall through to the keyword
fast-path; if a fact matches no keyword rule it stays unclassified. Expanding
`_KEYWORD_RULES` or lowering centroid thresholds + rebuilding centroids is the lever
when too many short facts abstain.
