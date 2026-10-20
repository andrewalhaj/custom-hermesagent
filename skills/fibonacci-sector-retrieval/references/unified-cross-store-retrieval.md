# Unified Cross-Store Retrieval + Qwen3 Embedding Migration (2026-06-27)

The taxonomy was extended from "rank knowledge facts" to a **universal cross-store retrieval
layer**: one query fans out across knowledge + skills + references + kanban, merged by the same
Fibonacci sector weight.

## unified_search.py — the fan-out module

`scripts/knowledge/unified_search.py` — `unified_search(query, top_k=10, stores=None)`:

1. Classify the query into a `sector_path` via `classifier.classify(query)` (centroid pipeline).
2. Fan out to four stores in parallel:
   - **knowledge** — existing `knowledge.search()` (Supabase pgvector, sector-weighted)
   - **skills** — scan `~/.hermes/skills/**/SKILL.md`, parse YAML frontmatter `sector_path`,
     score by keyword overlap × Fibonacci sector boost
   - **references** — keyword-match filename + first 500 chars of each `~/.hermes/references/*.md`
   - **kanban** — keyword-match active task title/body in `kanban.db` (excludes archived/done)
3. Apply Fibonacci sector boost to every result (`_fib_boost(overlap)` where overlap = matching
   sector-path levels, weights `[8,5,3,2]`).
4. Merge, dedup by title prefix, sort by score, return top_k.

Each result dict: `{store, id, title, content, score, sector_path, url}`.

## Wired into memory_search

`plugins/supabase/__init__.py::_search()` now calls `unified_search()` as the primary path with a
fallback to direct `knowledge.search()` on error. So every `memory_search` tool call fans out
across all four stores. Result normalization: `content` (not `text`) is the body field
`unified_search` returns; map it to the `text` key the tool consumers expect, and carry
`sector_path` into `tags`.

## Qwen3-Embedding-4B migration (Phase 1B)

- Model: `nomic-embed-text` (768-dim) → `qwen3-embedding:4b` (**2560-dim**), already on the Mac
  Studio Ollama. `.env`: `EMBEDDING_MODEL=qwen3-embedding:4b`, `EMBEDDING_API_URL` unchanged at
  `:11434`.
- Supabase vector column resized 768→2560 via `ALTER COLUMN vector TYPE vector(2560)`.
- `embed.py::_embed_remote()` gained ≤32-text batching to stop the `/api/embed` HTTP 400 that was
  silently dropping centroid builds to the 768-dim local fallback.
- Centroids rebuilt in 2560-dim Qwen3 space — verify `anchors.pkl` matrix shape is `(N, 2560)`.
- **Verified 2026-06-27: P@5 0.80 (nomic-768) → 0.94 (Qwen3-2560), MRR 0.6817 → 0.8967.** The
  re-embed is a genuine quality win, not just a dim change.

## 🛑 PITFALL: 2560-dim is UN-INDEXABLE — pgvector has a hard 2000-dim ceiling (verified live)

The earlier note that you "re-index" or "recreate the IVFFlat index on the 2560-dim column" is
**WRONG and will waste a session.** Tested directly on the live store (pgvector 0.8.0):

| index method @ dim | result |
|---|---|
| ivfflat @ 2560 | **FAIL** — "column cannot have more than 2000 dimensions for ivfflat index" |
| hnsw @ 2560 | **FAIL** — same 2000-dim ceiling |
| hnsw @ 1024 | OK |

So the migration to native 2560 **dropped the vector index and cannot get it back.** The store now
does a **sequential scan** — measured 0.42s/query at 188 rows (dominated by remote-embed RTT, not
the scan). Fine at this size; will degrade past ~5–10k rows. The two real options:
- **Accept seqscan** (simplest; revisit when the store grows).
- **Qwen3 MRL-1024**: Qwen3-Embedding supports Matryoshka truncation via the **top-level
  `dimensions` param** (`{"model":"qwen3-embedding:4b","input":[...],"dimensions":1024}` → 1024-dim;
  note `options.dimensions` is IGNORED, returns 2560). 1024-dim is hnsw-indexable. Cost: re-embed +
  `ALTER COLUMN vector TYPE vector(1024)` + rebuild centroids at 1024. The RPC `match_knowledge_v4`
  takes an UNPARAMETERIZED `vector` arg (no hardcoded dim) so it works at any dim with no rewrite.

## 🛑 PITFALL: the gateway does NOT load `.env` into os.environ — silent 768 fallback breaks search

This caused a **live outage** this session: after the store went to 2560, every `memory_search`
returned `HTTP 400: "different vector dimensions 2560 and 768"`. Root cause: `embed.py` read
`EMBEDDING_API_URL`/`EMBEDDING_MODEL` from `os.environ` at import time, but **Hermes does not load
`~/.hermes/.env` into the gateway's `os.environ`** — so the live gateway saw empty config and fell
back to the local all-mpnet **768-dim** embedder, mismatching the 2560 store. (`supabase_client.py`
works live only because `get_supabase()` reads `.env` *directly*.) Fix: `embed.py` now has an
`_env_fallback(key)` that reads `~/.hermes/.env` when the var is absent from `os.environ` — mirroring
the supabase_client pattern. **Lesson: any config the gateway needs at runtime must be read from
`.env` directly, not assumed to be in `os.environ`.** Note these are read at MODULE IMPORT time
(frozen per-process) — a running gateway with the old module cached needs a restart to pick up the
fix; a never-imported module (e.g. the brand-new `unified_search` chain) picks it up on first call.

## ⚠️ Concurrency hazard: this cron can race ITSELF

This session detected TWO concurrent sibling subagents ('sa-0-…' editing `embed.py`, 'sa-1-…'
editing the report) — a parallel run of the SAME cron task executed the destructive Phase 1B
migration while this run was staging it. They didn't collide (disjoint edits), but a re-embed
racing another re-embed could corrupt the store. **Put an idempotency lock on any
embedding-migration cron.** The write tool's "modified by sibling subagent" warning is the live
tell — when you see it, STOP and reconcile (re-read the file) rather than clobbering.

## Write-time routing (Phase 3)

- **Kanban**: added a `sector_path TEXT` column to `tasks`; backfilled active cards via
  `classifier.classify(title + body)`. ~24% classify, rest abstain (titles too short — expected,
  same abstention behavior as facts). The dispatcher does NOT yet route by sector — column exists,
  routing logic is future work.
- **Skills**: 22/150 SKILL.md got `sector_path` frontmatter; 128 abstained. Coverage is low
  because the centroid classifier's anchors don't cover most skill domains. To raise it, add
  skill-domain anchors to `taxonomy.json` and rebuild centroids — do NOT hand-roll a keyword
  fallback (that's the prohibited 100%→60% regression path).

## All agents on Supabase

All 27 Hermes profiles inherit `memory.provider: supabase` from the main config — none override to
`honcho`. So every profile shares the same knowledge store and the same `memory_search/add/remove`
tools (the supabase plugin loads globally, not per-profile).

## Known gaps after this session

- P@5 re-benchmarked 2026-06-27: **0.94** (47/50) at Qwen3-2560 + unified search — confirmed good.
- The 2560-dim column is UN-INDEXABLE (seqscan only) — see the pgvector-ceiling pitfall above. NOT
  a "recreate the index" gap; it's a strategy decision (accept seqscan vs MRL-1024).
- Gateway restart still needed to activate the `unified_search` plugin edit + `embed.py` `.env`
  fallback in the RUNNING gateway (changes verified on disk; running process has old code cached).
  This is a gated state change — leave it for the human, don't restart from a headless cron.
- Skill sector coverage now **35/150** (22 pre-existing + 13 added this session; 3 suspect
  assignments correctly dropped by a name/domain sanity filter). To raise further, add skill-domain
  anchors to `taxonomy.json` and rebuild centroids — do NOT hand-roll a keyword fallback.
- Classifier calibration shows 0.46 selective-risk (legacy 768-era labels disagree with Qwen3
  geometry) → it picked a strict 0.8 threshold and abstains often. This is the SAFE failure mode;
  `classify()` still scores 5/5 domain-correct on clear queries via keyword + high-margin paths.
  Let auto-classify + webhooks re-accumulate; do not bulk re-label.
