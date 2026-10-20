---
name: knowledge-store
description: "Use when storing/searching the semantic knowledge base"
version: 3.5.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [knowledge, memory, vector-db, supabase, pgvector, semantic-search]
    created_by: agent
load_when:
  - "user asks about a system configuration, tool quirk, or past bug fix"
  - "agent needs to recall institutional knowledge about this user's setup"
  - "hot memory is full and knowledge offloading is needed"
sector_path: [infrastructure, storage, database]
---

# Knowledge Store — Supabase pgvector Semantic Memory

> **⚠️ Backend migrated from LanceDB → Supabase pgvector at v3.0 (2026-06).** The code in `knowledge.py` is Supabase-only. Some historical sections below reference LanceDB — those are records of past sessions/decisions, not current operational instructions. The live backend is Supabase pgvector. `~/.hermes/knowledge_db/` (LanceDB) no longer exists.

> **⚠️ `store()` has NO `context_prefix=` kwarg** — stale callers raise TypeError that ingestion crons swallow as "skipped" (silent zero-store for days). Playbook: `references/ingestion-pipeline-debugging.md`.

Persistent, searchable knowledge base for everything the agent needs to remember about this user's system, tools, and environment. Complements hot memory (session-critical context). Note: Honcho (user modeling) was fully removed from Hermes as of 2026-07-01 and is no longer part of the memory architecture.

## Quick Reference

```bash
# Search
python3 ~/.hermes/scripts/knowledge.py search "manifest routing fallback"

...

**Supabase → Hermes webhook (auto MEMORY.md append):** `references/supabase-webhook-pg-net.md` — pg_net trigger setup, Hermes FastAPI endpoint, Cloudflare Bot Fight Mode workaround, secret rotation.
**Neo4j graph layer (complements pgvector):** `references/neo4j-graph-layer.md` — Docker setup, handler pattern, relationship extraction, backfill, query patterns.
**Metadata filtering layer (v3.4):** `references/metadata-filtering-architecture.md` — jsonb schema, query-side regex extraction, store-side qwen-32b extraction, enrichment cron architecture.
**Supabase RPC pitfalls:** `references/supabase-rpc-pitfalls.md` — function overloads, parameter name matching, column ambiguity, jsonb vs text[] types.
**Supabase hygiene (v3.5):** `references/supabase-hygiene.md` — audit queries, garbage patterns (CLI arg leakage), webhook hygiene rules, batch classification via `_detect_query_sector`, Kanban idempotency.
**Embedding classifier + module map (v3.6, 2026-06-26):** `references/embedding-classifier.md` — `knowledge.py` is now an 11-MODULE PACKAGE (`knowledge/`), not a flat file; the `_detect_query_sector` keyword regex was replaced with an embedding classifier (`knowledge/classifier.py`) that can ABSTAIN. Contains the module map (where each function moved), the 4-stage pipeline, the centroid-cancellation bug + SVD/PC1 fix, the kNN reject scorer, and calibration. **Read this FIRST when touching knowledge.py internals** — pitfalls below citing `knowledge.py` line numbers refer to files inside `knowledge/` now.

# Store a fact
KNOWLEDGE_TAGS="govee,bug" KNOWLEDGE_PRIORITY="high" \
  python3 ~/.hermes/scripts/knowledge.py store "Fact text here"

# Store with contextual prefix (overlap chunking + Haiku/synthetic prefix)
KNOWLEDGE_TAGS="infrastructure" \
  python3 ~/.hermes/scripts/knowledge.py store --contextualize "Fact text here..."

# Index vault changes (standard heading-based chunking)
python3 ~/.hermes/scripts/knowledge.py index-vault

# Index vault with contextualized chunking
python3 ~/.hermes/scripts/knowledge.py index-vault --contextualize

# Contextualize a single file (chunk → prefix → embed → store)
python3 ~/.hermes/scripts/knowledge.py contextualize-file ~/path/to/file.md

# Status
python3 ~/.hermes/scripts/knowledge.py status

# Unified tiered retrieval across ALL sources (Supabase + MEMORY.md + USER.md + reference docs)
python3 ~/.hermes/scripts/query_router.py instant "<query>"   # Supabase-only, fastest
python3 ~/.hermes/scripts/query_router.py classic "<query>"   # merge all sources, ranked + source-attributed
python3 ~/.hermes/scripts/query_router.py agentic "<query>"   # plan + graph neighbors + suggested reads
# add --json to any tier for machine-readable output. READ-ONLY (never mutates the DB).
# Note: Honcho was fully removed 2026-07-01 — there is no separate user-modeling store to query.

# Recent entries
python3 ~/.hermes/scripts/knowledge.py recent 10

# Run benchmark evaluation (P@5/MRR; `sweep` for fixed top_k; eval_v2 is the rigorous one)
python3 ~/.hermes/scripts/kbench.py eval

# Extended benchmarks: Recall@10 + Multi-hop recall (v3.5 — requires Neo4j)
python3 ~/.hermes/scripts/benchmark_extended.py

# Check for stale infra/config facts (dry-run default; --live for connectivity probes)
python3 ~/.hermes/scripts/knowledge.py stale-check

# Index changed reference/vault files since last run (mtime-based; safe for cron)
python3 ~/.hermes/scripts/knowledge.py auto-index

# Compile search results with citations + gap analysis (no LLM, pure compilation)
python3 ~/.hermes/scripts/knowledge.py summarize "how does memory work across sessions"
```

### v2.0: Contextualized Embeddings

The contextualized embedding pipeline wraps each chunk with a **situating prefix** before embedding, so the vector captures *what document this is from and where it sits in the document structure*, not just the raw text.

**Pipeline:**
1. **Overlap-based paragraph chunking** — splits markdown into paragraphs on `\n\n+`, groups into ~800-char chunks with 1-paragraph overlap. Each chunk tracks its heading breadcrumb.
2. **Contextual prefix generation** — sends chunk + source context to Manifest's Haiku route (`claude-haiku-4-5`) for a 1-sentence situating sentence.
3. **Synthetic floor** — if Manifest/Haiku is unreachable (Anthropic provider inactive, timeout, etc.), falls back to `"This passage is from {filename}: {heading context}."` — measurably better than plain text via title-keyword re-injection.
4. **Embed** `"{prefix}\n\n{chunk_body}"` — the vector now encodes both *what* the text says and *where* it's from.
5. **Sha256 cache** — chunks are cached by body hash. Re-contextualizing the same file skips unchanged chunks.

**Columns:** `context_prefix` (the situating sentence) and `body_hash` (sha256 for cache dedup) — both added via schema migration on first v2.0 access. Existing plain-text embeddings survive untouched with NULL values.

**Cost:** Haiku prefix generation requires the Anthropic provider. Since Hermes now uses `hermes-claude-auth` for direct Anthropic access (`provider: anthropic`), Haiku is reachable. If the Anthropic provider is unavailable, the synthetic floor (`"This passage is from {filename}: {heading context}."`) handles everything automatically — measurably better than plain text via title-keyword re-injection.

### v3.0: Retrieval Pipeline (2026-06 status)

`search()` uses a multi-stage pipeline, but **hybrid RRF was deployed 2026-06-25 — check which RPC knowledge.py calls before assuming it's active:**

1. **LRU embedding cache** — repeated queries skip re-encoding (256-entry cap, 30-min TTL). Transparent.
2. **Hybrid vector + full-text RRF** — **`match_knowledge_v3` deployed 2026-06-25 with reciprocal rank fusion (RRF, k=20, was k=60 in v2) between pgvector cosine and tsvector GIN search.** Lower k = better discrimination at 152-fact scale (3.3× vs 1.8× range). `match_knowledge_v2` is deprecated. The `match_source` column ('vector' or 'fts') shows which engine found each result. knowledge.py search() calls v3 as of 2026-06-25.
3. **Dual-threshold scoring** — `cosine ≥ 0.80 OR (cosine ≥ 0.55 AND token_overlap ≥ 60%)`. Patched into search() 2026-06-25 alongside v2. Proven: 67% → 93% recall.
4. **Priority + recency scoring** — importance multiplier + exponential recency boost (14-day half-life).
5. **MMR diversity** — near-duplicates (cosine > 0.85) dropped when top_k > 3.
6. **Knowledge graph boost** — confirmatory boost for results whose source is a graph-neighbor of a top-3 hit. Requires `build-graph` to have run.

### v3.4: Metadata Filtering Layer (2026-06-25)

Additive layer on top of vector search — no re-indexing required. The `metadata jsonb` column stores structured facts extracted by qwen2.5-32b on LH02, and the `match_knowledge_v2` RPC accepts an optional `metadata_filter` parameter that filters rows BEFORE vector search via `WHERE k.metadata @> metadata_filter OR k.metadata IS NULL`.

**Schema:**
```json
{"hosts": ["VPS-A", "<VPS-A-ip>"], "ports": [5051],
 "services": ["nginx"], "file_paths": ["~/wall-dash/"], "systems": ["wall-dashboard"]}
```

**Query-side extraction** — `_extract_query_metadata()`: fast regex patterns matching known hostnames, IPs, ports, services, and systems from the user query. Returns a dict suitable for `metadata_filter`. Example: "wall dashboard VPS-A port 5051" → `{"hosts":["vps-a"],"ports":["5051"],"services":["dashboard"]}`.

**Store-side extraction** — qwen2.5-32b on LH02 (llama.cpp :8080) with a 3-line system prompt. Produces valid JSON in ~2s per fact. Zero API cost, local, already running.

**Enrichment architecture** — three layers, zero manual intervention:
- **New facts:** `knowledge.py store()` creates a kanban card → dispatcher enriches within 60s
- **Delta:** hourly cron scans for `metadata_updated_at < text_updated_at` → enriches changed facts (~6s when idle)
- **Full sweep:** daily 2 AM cron re-extracts all facts → catches prompt drift, model changes (~4 min for 135 facts)

**Ranking weight fix (2026-06-25):** importance multiplier (`score *= 0.7 + 0.3*weight`) → additive tie-breaker (`score += 0.04 - rank*0.01`). Recency bonus reduced from `*0.10` → `*0.02`. Both were overpowering cosine similarity — a stale high-priority Sentry alert beat a relevant normal-priority fact. Now they're tie-breakers after relevance is established.

**Full pipeline:** `references/metadata-filtering-architecture.md`

### v3.1: Search-time Feature Gates (2026-06-25)

Three optional, independently-gated retrieval enhancements in `search()`. All default off — backward compatible. Control via env vars (checked at call time via functions, not module load — see pitfall).

| Gate | Env var | What it does | Latency | Verdict |
|---|---|---|---|---|
| Query enrichment | — | **Deleted 2026-10-16** (enrich_claim.py and the `ENRICH_QUERY`/`KNOWLEDGE_ENRICH` gates). | — | Was harmful at <500 facts. |
| Query expansion | `QUERY_EXPAND=true` | Generates 2–3 deterministic query variants, merges via RRF. | +2–3 extra RPCs | **Preserves recall.** |
| Cosine rerank | `RERANK=true` | Re-ranks pool via cross-encoder (`mxbai-rerank-base-v1`). Min-max normalizes to 0–1. | +N predictions, ~6s first load | **Semantic, not exact-match.** See below. |
| Graph expand | `GRAPH_EXPAND=true` | Appends Neo4j neighbors at 0.90× score. | +0.17s | **DO NOT USE** — facts rank at 11+. |
| Graph traverse | `GRAPH_TRAVERSE=true` | Interleaves 1-hop Neo4j neighbors at competitive scores (0.60–0.65). | +0.17s | **+12.6% R@10 macro.** Ships. |
| Chain traverse | `GRAPH_TRAVERSE_CHAIN=true` | 2-hop iterative chain traversal, cross-community bridging. | +0.17s | **+133% multi-hop end recall.** Ships for multi-hop. |
| Sector boost (REMOVED 2026-10-19; no-op under RERANK, MRR 0.9187 unchanged) | — (flag no longer read) | Fibonacci sector weighting (Domain→Capability→Process→Document). | ~0ms | **+12.5% R@10 alone, +22.6% with traverse.** **SHIPPED as production default 2026-06-25.** |
| Temporal decay (REMOVED 2026-10-19 with the boost) | — | Per-sector exponential decay (3–90 day half-life). | ~0ms | **+3% baseline R@10.** Ships always-on. |
| Confidence bands | Always active | Multi-signal agreement score (0–1) per hit: vector+fts+sector+graph. | ~0ms | **Metadata, not scoring.** Always active in hit dict. |
| Graph traverse | `GRAPH_TRAVERSE=true` | Interleaves 1-hop Neo4j neighbors at competitive scores (0.60–0.65). | +0.17s | **+12.6% R@10 macro.** **SHIPPED as production default 2026-06-25.** |
| Chain traverse | `GRAPH_TRAVERSE_CHAIN=true` | 2-hop iterative chain traversal, cross-community bridging. | +0.17s | **+133% multi-hop end recall.** Ships for multi-hop. |

**Production deployment (2026-06-25):** Systemd drop-in env overrides on hermes-gateway, hermes-gateway-ha-bot, and hermes-kb-daemon. Every `knowledge.py` call inherits `SECTOR_BOOST=true GRAPH_TRAVERSE=true`. Default `top_k=8` in `knowledge.py`, `kb_client.py`, `offload_probe.py`, and `query_router.py`. P@5: 86.7% → 93.3%.

**⚠️ Cross-encoder ranks semantically, not exactly (proven 2026-06-25).** The `mxbai-rerank-base-v1` model scores (query, document) relevance. For the query "Linear integration webhook", it correctly gives the highest score to a "Dispatcher webhooks" fact — semantically related but NOT the exact fact the benchmark expects. This DROPS the benchmark (87% → 40%) but IMPROVES real-query results (surfaces related infrastructure knowledge). Keep the cross-encoder gated opt-in: use `RERANK=true` for real queries where semantic breadth is valuable, leave it off for benchmark/matching tasks. The model is cached at `~/.cache/huggingface/` and lazy-loaded on first call. Pre-warm with `knowledge._load_cross_encoder()` before benchmarking to avoid timeout.

**Recommended default:** `GRAPH_TRAVERSE=true SECTOR_BOOST=true` (shipped as production defaults via systemd env overrides 2026-06-25). For multi-hop queries, add `GRAPH_TRAVERSE_CHAIN=true`. Default `top_k=8` (was 5) — recovers isolated facts ranking 6-8. Do NOT enable `ENRICH_QUERY` until the KB has >1,000 domain-diverse facts.

### v3.5: Neo4j Graph Traversal at Query Time (2026-06-25)

Two graph retrieval modes, both gated and off by default. The key design insight: **interleave beats append** for top-K results.

| Gate | Env var | What it does | Top-K impact | Verdict |
|---|---|---|---|---|
| Graph expand | `GRAPH_EXPAND=true` | Appends 1-hop + community neighbors at 0.90× source score | **Zero** — facts rank 11+ | Keep for deep-recall (top_k≥20) |
| Graph traverse | `GRAPH_TRAVERSE=true` | Interleaves 1-hop (0.60–0.65) + 2-hop (0.55) neighbors at competitive scores | **+12.6% macro R@10** (0.6446→0.7260) | Use for recall-sensitive queries |

**Interleaving algorithm:** `_graph_traverse()` in `knowledge.py` takes top-3 vector hits, queries Neo4j for `[:RELATED_TO]` neighbors (direct + 2-hop with community scoring), then weaves them:
```
position 0: vector rank 0
position 1: best neighbor of vector rank 0
position 2: vector rank 1
position 3: best neighbor of vector rank 1
...
```
Each neighbor gets `match_source='graph_traverse'` and `graph_source='neo4j:1hop:<id>'` for attribution. Same-community neighbors score 0.65; cross-community 0.60; 2-hop 0.55. Silently falls back to original hits on any Neo4j failure.

**Benchmark results (2026-06-25, 15-query harness, 194 facts, 128 edges):**

| Metric | Baseline | GRAPH_EXPAND | GRAPH_TRAVERSE |
|---|---|---|---|
| Macro R@10 | 0.6446 | 0.6446 | **0.7260** |
| Micro R@10 | 0.5000 | 0.5000 | **0.5882** |
| Facts surfaced | 34/68 | 34/68 | **40/68** |

Notable per-query wins: Neo4j (0.67→1.00), Sentry (0.40→0.60), HA dashboard (0.40→0.60), Neo4j MCP (0.67→1.00). Two queries stall at 0.00 — "Hermes config" and "Hermes WebUI" have zero `[:RELATED_TO]` edges (isolated nodes). The remaining gap is cross-community traversal (see multi-hop below).

**Multi-hop recall — unsolved (proven 2026-06-25):** A separate benchmark (`scripts/benchmark_extended.py`) constructs 15 fact chains (A→B→C via `[:RELATED_TO]`, different communities) and measures whether the END fact surfaces from a query built on the START fact's keywords. Results:

| Metric | Baseline | GRAPH_TRAVERSE | What this means |
|---|---|---|---|
| Start recall | 0.9333 | 0.9333 | Vector search finds the chain start |
| End recall | 0.2000 | 0.2000 | The end fact is 2-3 hops + different community — vector cosine doesn't bridge the gap |
| Both recall | 0.2000 | 0.2000 | Graph traverse adds START neighbors, not END neighbors |

The 3 successes are topology chains (hermes-dispatcher→Tailscale→LH01, Wall Dashboard→VPS-A) — same semantic domain. The 12 failures cross domain boundaries (e.g., Write Gate→Neo4j, Neo4j→Sentry). Solving this needs **iterative chain traversal**: take top hit → follow edges → follow THEIR edges → check 2nd-hop results against the query. That's a different function (`_graph_traverse_chain`, not yet built).

**Reusable benchmark script:** `scripts/benchmark_extended.py` — runs Recall@10 (three modes) + Multi-hop (two modes) with Neo4j integration. Usage:
```bash
python3 ~/.hermes/scripts/benchmark_extended.py              # all benchmarks
python3 ~/.hermes/scripts/benchmark_extended.py --json        # machine-readable
python3 ~/.hermes/scripts/benchmark_extended.py --skip-recall # multi-hop only
python3 ~/.hermes/scripts/benchmark_extended.py --skip-graph  # recall only
```
Requires `NEO4J_URI`/`NEO4J_PASSWORD` env vars. Runtime: ~70s for full suite (45 searches for recall + 30 for multi-hop).

**Enrichment evolution (2026-06-25, three iterations, all worse than baseline):**
1. Template-based (`enrich_template`) — prepended ~500 chars of domain prose to query. Result: **1/5** (template dominates embedding, dilutes query signal).
2. Identifier injection (`enrich_query_mode` v1) — appended hostname/IP/port/function-names from local source files. Result: **2/5** (generic identifiers added to every query; file scanning can't reach fleet-wide context like "VPS-A nginx 5051").
3. KB-only term injection (`enrich_query_mode` v2, current) — searches KB with claim + domain terms, extracts key words from top hits, appends. Result: **2/5** (at 353-fact scale, top hits are the same ~20 high-priority facts regardless of query; injected terms converge to noise).

The root cause is scale: at <500 domain-diverse facts, the KB can't produce query-discriminating terms. Enrichment needs ~1,000+ facts with genuine topical breadth before the injected terms add signal. The architecture is correct; the data isn't dense enough yet.

**Implementation detail — `_single_search()`:** The expansion path calls a shared `_single_search()` helper that wraps one `match_knowledge_v2` RPC call + the full post-processing pipeline (importance weight, recency boost, hit dict construction). The main `search()` path also uses it; both paths are identical at the RPC level. Dual-threshold filtering is applied per-variant before RRF merge.

**Implementation detail — `_rrf_merge()`:** Reciprocal rank fusion with k=60, matching the DB-side RRF in `match_knowledge_v2`. Variant results are merged by id with cumulative RRF score; ties go to the first variant that returned the hit.

### Synthesis convention: name the gaps (anti-fabrication)

When synthesizing an answer from `search()` results, **explicitly flag what the knowledge base does NOT contain** rather than papering over it. After presenting what was found, add a short "Gaps" note listing query terms that returned zero or weak (low-score) hits. This is the anti-fabrication principle (SOUL.md) applied to retrieval — borrowed from GBrain's `think` command. A retrieval answer that hides its blind spots is worse than one that names them. If every relevant query came back empty, say so plainly: "Nothing in the knowledge base covers X" — don't improvise.

## How Hermes Uses It

**Session-start ritual (MANDATORY):**
When the user's first message relates to infrastructure, system config, tools, or any topic that might have been discussed before, query Supabase BEFORE responding:
```bash
python3 ~/.hermes/scripts/knowledge.py search "<topic from user's first message>"
```
This prevents reteaching. The most common failure mode: agent answers from hot memory only, misses relevant facts in Supabase, user has to repeat themselves. Do NOT wait until you "need" deeper knowledge — assume the user's first infrastructure question needs a Supabase check.

**During a session:**
- Hot memory (injected every turn) contains 2-3 critical pointers only
- When anything technical arises — even if it feels familiar — do a Supabase search. The hot memory store (config-driven cap, ~3,000 chars) is NOT a reliable source for config details, server topology, or tool quirks. Hot memory is a pointer to the real knowledge in Supabase.
- New things I learn get stored immediately with appropriate tags and priority

**Post-session auto-capture:** automatic, nothing to run by hand. `session_heartbeat.py` (cron, 15 min) extracts
durable facts (config, infra, decisions, diagnostics, preferences) from active sessions via
`session_capture.extract_facts`; `session_distill.py` (cron, 4h) stores one digest per session.
A digest lands only after the session ends (a Telegram session ends when the next one starts). A distill run
that hits the 600 s cron timeout fails silently, so read `last_status` in `cron/jobs.json`. Prefetch injects
Supabase facts only; for threads that span sessions, use `session_search`.

**Search is semantic** — "env secrets file" finds ".env read_file restriction" because the embedding matches conceptually, not literally.

## Priority Levels

| Priority | When to use |
|----------|------------|
| `critical` | The one thing that prevents disaster if forgotten |
| `high` | Config details, tool quirks, device maps — reviewed every related query |
| `normal` | Procedural notes, historical context |
| `low` | Nice-to-have but not essential |

## Tags (Current)

`govee`, `config`, `env`, `secrets`, `gateway`, `discord`, `telegram`, `obsidian`, `vault`, `memory`, `cron`, `jobs`, `profiles`, `executor`, `user`, `preferences`, `communication`, `bug`, `workaround`, `script`, `commands`, `system`, `server`, `routing`, `devices`, `audit`, `skills`, `rules`, `factual`

## Storage

- **Location:** Supabase pgvector backend (the `knowledge` table; `~/.hermes/knowledge_db/` LanceDB no longer exists).
- **Embedding model (CURRENT, 2026-06-26):** `qwen3-embedding:4b` served by LH02 Ollama at `http://<LH02-tailnet-ip>:11434 (real address: memory_search "Tailnet")` (set via `EMBEDDING_API_URL` + `EMBEDDING_MODEL` in `.env`, read at call time via `embed.py::_env_fallback` because the gateway does NOT load `.env` into `os.environ`). The `knowledge.vector` column is **`halfvec(2560)`** (native Qwen3 dim) with an hnsw index (`halfvec_cosine_ops`, m=16, ef_construction=64). History: `all-mpnet-base-v2` 768 → nomic 768 → qwen3 native 2560 (seqscan, un-indexable) → qwen3 MRL-1024 (indexable, P@5 0.88) → **qwen3 halfvec(2560)** (indexable AND full quality, P@5 1.0). **IMPORTANT: changing the embedding model OR dimension requires re-embedding all vectors + a column swap — see the halfvec migration pitfall below.**
- **Concurrent-safe:** Supabase supports multi-process reads/writes
- **Backup:** Included in `hermes-backup-*.tar.gz` via the daily backup cron

## Indexing the Vault

After writing new changelogs or snapshots to the Obsidian vault, re-index:

```bash
# Standard heading-based chunking (legacy, plain text embeddings)
python3 ~/.hermes/scripts/knowledge.py index-vault

# Contextualized chunking (overlap chunks + situating prefixes)
python3 ~/.hermes/scripts/knowledge.py index-vault --contextualize
```

The contextualized mode chunks all markdown files by paragraph overlap, generates situating prefixes via Manifest Haiku (with synthetic fallback), and embeds `{prefix}\n\n{body}` instead of raw text. Archive and backup directories are excluded in both modes.

## Memory Architecture

| Layer | System | Purpose | Size Limit |
|-------|--------|---------|------------|
| Hot | `MEMORY.md` | Session-critical pointers only | 3,000 chars (USER.md 1,375) |
| Knowledge | Supabase pgvector | Agent institutional knowledge | Unlimited |

Note: Honcho (person/user modeling) was fully removed 2026-07-01. There is no separate user-modeling store — behavioral facts belong in USER.md or Supabase.

Caps are config-driven (`memory.memory_char_limit` / `memory.user_char_limit` in config.yaml) — read them live rather than trusting any number written here; this table goes stale when the cap changes.

Hot memory should NEVER contain knowledge that Supabase can serve. If you see a fact in hot memory that belongs in Supabase, move it.

## Staleness Audit Methodology (proven 2026-06-08)

The cold store rots silently: a fact stored as true in June is false in July when the system changes outside your view. Before relying on cold-tier recall — and ESPECIALLY before offloading fresh hot-tier facts into Supabase — audit it. A polluted store buries good data among contradicting stale rows, and semantic recall surfaces both with no signal which wins. Reusable probe: `scripts/staleness_scan.py` (see below). The method, in order:

1. **Deterministic dead-term scan FIRST — not LLM judgment.** Build a `(dead_pattern, why_dead, correct_fact)` list from VERIFIED-dead literal strings (retired services, decommissioned ports, renamed hosts), then pure-substring scan every row + reference doc. Zero hallucination — a regex can't confabulate. This catches the bulk. Do NOT open with "ask a subagent to judge staleness from its own knowledge" — that just launders the model's stale priors into the kill list.
2. **VERIFY the kill-list premise against the LIVE filesystem before trusting it.** The dead-term set is built from your own notes/memory — and the standing rule is *the filesystem wins over stored notes*. Before nuking 9 docs on the premise "service X is dead," prove X is actually gone on the live host (`docker ps`, port probe, `ls` the dir). This session the premise held (Manifest/ha-fusion confirmed absent, wall-dash live) — but if it had inverted, the entire kill list would invert. One live check guards against acting on a stale dead-term set.
3. **FLAGGED ≠ KILL. Classify every hit into four buckets** — a raw flag list presented as a kill list is the trap:
   - **CORRECT** — a stale *current-state claim* (e.g. "dashboard is ha-fusion at :5050"). Rewrite the fact in place; don't delete the row.
   - **KEEP-historical** — session transcripts, changelogs, post-mortems. These are TRUE records of past events; the dead term appears because the event really happened. Deleting them erases history.
   - **PROTECT-meta** — the confabulation blocklist, staleness-audit logs, `.bak` files, and a doc's own "the prior version described X / there is no X" correction preamble. These are SUPPOSED to name dead terms; killing them guts the defense that records what's false.
   - **REINGEST-chunk** — chunked copies of a reference doc that was already rewritten. The source doc is fixed but the cold store still serves the dead version. Re-chunk from the current doc (don't hand-edit the chunk).
4. **Keyword classification needs a content read for the borderline cases.** Auto-bucketing by keyword mislabels a blocklist row that *asserts* "X is false" as a stale-X claim, and can miss a meta-row that itself went stale (e.g. a blocklist line listing "LanceDB usage" as a confabulation — now wrong, LanceDB is real). Read the handful of borderline rows before finalizing.
5. **Split the fix into mechanical vs reasoned (this is the Phase-B shape).** Mechanical = literal dead-string swaps ("Manifest router"→"direct routing", ":5050"→":5051", "backup VPS-A"→"prod host VPS-A") — scriptable, low-judgment, one gated diff. Reasoned = docs ABOUT a system that no longer exists (rewrite-vs-archive is a user decision, not a find/replace) + surgical row corrections. Only the reasoned half is swarm-shaped.

The whole sequence is read-only through classification; mutation (delete/rewrite/reingest) is a separate gated phase presented as a kill/correct ledger. Write the ledger to a durable `references/staleness-audit-<date>.md` so it survives compaction.

**Reusable probe:** `scripts/staleness_scan.py` runs step 1 (read-only dead-term flag report over the cold store + reference docs). Edit its `DEAD` list to the currently-verified-dead facts, run it, then do steps 2–4 by hand. It deliberately prints FLAGS, not a kill list.

## ⚠️ HISTORICAL — Behavioral Facts Caveat (Honcho removed 2026-07-01)

Honcho was fully removed from Hermes on 2026-07-01. The section below is preserved as historical record.

Honcho conclusions (behavioral facts like "User prefers terse communication") embedded poorly with `all-mpnet-base-v2`: scores 0.08–0.09, nearly invisible in vector search. With Honcho removed, behavioral facts go directly into USER.md (push-based, injected every turn) or Supabase with appropriate tags.

## Pitfalls

**Skills are NOT embedded in Supabase + eval-failure triage has a THIRD bin (duplicate-cluster ranking jitter): `references/skills-store-audit.md`** — unified_search reads SKILL.md frontmatter live from disk (`.curator_backups` glob-leak fix, orphan/corrupted sector_path detection recipes), and a query that fails eval but hits rank-0 on a direct probe means near-duplicate facts flapping, fixed by dedup not more facts (proven 2026-07-01).

  1. **`.curator_backups` leak:** `_search_skills` (and any code globbing `skills/**/SKILL.md`) MUST skip paths containing `.curator_backups` — without the filter, DELETED skill copies are searchable and can outrank live skills (a deleted honcho skill ranked #1 at 1.00 for a memory query, and leaked into per-turn `prefetch()`). Fixed 2026-07-01 with a two-line skip in `_search_skills`; re-check after any unified_search edit.
  2. **Orphan sector_paths:** validate every skill's `sector_path` against the 63 nodes in `scripts/knowledge/taxonomy.json` (`set(tuple(n['path']) for n in t['nodes'])`). The classifier INVENTS plausible-but-nonexistent nodes (`development/quality`, `user/design`, `memory/retrieval` — 36 of 77 were orphans). Orphans silently get zero deep-level Fibonacci boost. Remap to the nearest real node; prefer an honest level-1 parent (`development`) over inventing a fake leaf.
  3. **Character-split corruption in Supabase `sector_path`:** a string passed where a list was expected stores as one-char-per-level: `h/e/r/m/e/s///a/g/e/n/t...`. Detect with `any(len(seg)==1 for seg in sector_path) and len(sector_path)>5`; fix via REST PATCH with a valid taxonomy list (no re-embed needed — sector_path is metadata).
  Also: `unified_search.py` lives in `scripts/knowledge/` but the gateway's prefetch may hold a cached module — edits need the gateway restart to take effect in-process.

**"Skills embedded into the DB" = sector_path FRONTMATTER + live disk scan, NOT Supabase rows (verified 2026-07-01 — this question has now recurred across 3 sessions; this is the definitive answer).** Skills were never chunked into the `knowledge` table (0 skill-content rows). The June 26/27 taxonomy cron classified `sector_path:` frontmatter in SKILL.md files, and `knowledge/unified_search.py::_search_skills()` reads those files live from `~/.hermes/skills/**/SKILL.md` at query time, scoring by keyword match × Fibonacci sector-overlap boost. Audit surface is therefore frontmatter + the search path, not DB rows. Three failure modes found on 2026-07-01:
  1. **`.curator_backups` leak (critical class of bug):** the glob `skills/**/SKILL.md` has no backup-dir filter, so DELETED skill copies under `skills/.curator_backups/` are searchable — the deleted honcho skill ranked #1 (score 1.00) for memory queries, above every live skill, and leaked into per-turn `prefetch()` (which calls `unified_search`). Any disk-scanning search over the skills tree MUST exclude `.curator_backups`. Verify with: `from knowledge.unified_search import _search_skills; _search_skills("honcho memory recall", [], top_k=8)` — no hit URL may contain `.curator_backups`.
  2. **Orphan sector_paths:** 36/77 classified skills carried paths NOT in `taxonomy.json` (63 nodes; validate with `set(tuple(n['path']) for n in taxonomy['nodes'])`) — the classifier invented plausible nodes (`development/quality`, `user/design`). Level-0 overlap still part-boosts, deeper levels never match. Audit frontmatter against the live taxonomy, don't assume classified = valid.
  3. **Character-split sector_path corruption signature:** Supabase rows with `sector_path` like `h/e/r/m/e/s///a/g/e/n/t/-/b/e/h/a/v/i/o/r` mean a STRING was passed where a list was expected — Postgres text[] coerced each char to an element. Detect with: any row where `len(seg)==1` for most segments and `len(sector_path)>5`. Sector boost is dead for these rows; fix via REST PATCH with a proper list.
  **Table-discovery trick:** `GET {url}/rest/v1/` with apikey headers returns the OpenAPI spec — `spec['paths']` lists every exposed table and RPC. Fastest way to answer "is there a skills/other table?" without guessing.

**Quick benchmark: `python3 ~/.hermes/scripts/kbench.py eval` (P@5/MRR; `--alert` prints only on regression; `sweep` for fixed top_k). Rigorous benchmark: `knowledge/eval_v2.py`.** (2026-10-16: `knowledge.py` is a CLI shim over the package; the LanceDB monolith and `knowledge_benchmark_cron.py` are gone.)
  **Operational tell:** `eval_benchmark()` lazy-loads the embedding model on first `search()` call (~2–3 min cold); a 60s foreground timeout WILL kill it — **run the benchmark in the background** (`terminal background=true notify_on_complete=true`) and read the result when it lands.

**A `lancedb`-flagged stale-fact hit can be a FALSE POSITIVE — the "Honcho/LanceDB fully removed" status notes are intentional records, not stale data (proven 2026-06-26).** During a stale-data audit, grepping the store for `lancedb`/`honcho` will surface facts that DOCUMENT those systems as removed ("Honcho fully removed", "backend migrated LanceDB→Supabase"). These are deliberate current-state notes — deleting them erases the record that the migration happened and re-opens the door to the reteaching trap. Same bucket as KEEP-historical / PROTECT-meta in the staleness methodology above: read the row body before nuking a keyword hit. A fact that *names* a dead system to say it's dead is not itself stale. — the indexable full-quality answer (proven 2026-06-26).** pgvector's `vector` type has a hard **2000-dim index ceiling** (ivfflat AND hnsw both fail at 2560 with "column cannot have more than 2000 dimensions"). Three ways to live with a 2560-dim native Qwen3 embedding, in order of quality:
  1. `vector(2560)` no index → seqscan, ~3s/query at 188 rows, P@5 0.94 — full quality but doesn't scale.
  2. `vector(1024)` via Qwen3 MRL truncation (top-level `dimensions:1024` param on `/api/embed`, NOT `options.dimensions`) → hnsw indexable, 136ms, P@5 0.88 — fast but loses ~0.06 P@5 to truncation.
  3. **`halfvec(2560)` (float16) → hnsw indexable (4000-dim ceiling), 1.7ms RPC, P@5 1.0 — the winner.** halfvec keeps the full 2560 dims at half precision; accuracy loss vs float32 is ~0.1% (negligible for retrieval). Use `halfvec_cosine_ops` (NOT `vector_cosine_ops` — wrong ops silently builds an L2 index). The existing `match_knowledge_v4` RPC takes a generic `vector` param and **auto-casts halfvec→vector at query time** — no RPC change needed.
  **Migration recipe (add/populate/drop/rename swap — direct `ALTER TYPE` between incompatible types fails):** `ALTER TABLE knowledge ADD COLUMN vec_new halfvec(2560)` → populate per-row via REST PATCH → `DROP COLUMN vector` → `RENAME vec_new TO vector` → build the hnsw index. DDL goes through the **Supabase Management API** (`POST https://api.supabase.com/v1/projects/<ref>/database/query`, `Authorization: Bearer <SUPABASE_ACCESS_TOKEN>` AND a `User-Agent` header — without UA, Cloudflare returns 403 error 1010); PostgREST/REST can't do DDL and psql/psycopg2 aren't installed. Reuse the prior migration's full row dump if dims already match (skip re-embedding — saves all the Ollama calls).

**Cross-encoder rerank (`RERANK=true`) is worth +2s/query for the MRR jump (re-decided 2026-06-26, supersedes the cautious v3.1 framing).** With the store on halfvec(2560)+qwen3, turning RERANK on moved **MRR 0.77→0.91 and rank-1 hits 66%→84%** with P@5 holding at 1.0. The v3.1 warning that rerank "DROPS the benchmark 87%→40%" was an artifact of the OLD benchmark keyed on `expected_id` and the OLD embedding model — with the current `expected_substring` benchmark + qwen3 vectors, rerank strictly helps. Cost is ~2s/query (model is `mxbai-rerank-base-v1`, cached at `~/.cache/huggingface/`, lazy-loaded; pre-warm with `knowledge._load_cross_encoder()` before benchmarking). Flag is read at call time, so adding `RERANK=true` to `.env` takes effect on the next gateway start. Keep it ON for interactive `memory_search`; the latency is invisible for background tasks.

**The benchmark ground-truth goes stale when the system changes — fix the harness, not the retrieval, and SEPARATE the two failure classes (re-proven 2026-06-26).** When `eval_benchmark()` shows failures after a system change, the failures split into two kinds and you must classify each before acting:
  1. **Stale ground-truth** — the `expected_substring` references something that no longer exists (e.g. a query `"LH02 embedding nomic"` expecting `nomic-embed-text` after migrating to qwen3; or an `expected_substring` that doesn't match the actual fact wording). FIX THE BENCHMARK ENTRY (`~/.hermes/knowledge_db/benchmark_queries.json`), don't chase a retrieval "regression" that isn't real. Tell: the expected string describes a decommissioned/renamed thing.
  2. **Genuine coverage gap** — the fact the query needs simply isn't in the store, or is too weakly-worded to rank. FIX THE DATA: `store()` a new atomic fact with query-shaped wording (see query-shaped-alias pitfall). This session added 5 facts (LH02/Ollama host, Coder fleet, Mealio, LH01 host, Neo4j MCP) and took P@5 0.90→1.0.
  The procedure that worked: run eval → for each failure, decide stale-GT vs coverage-gap → fix benchmark entries AND add facts in one pass → re-run. Two iterations took 0.90→0.96→1.0. Do NOT "fix" retrieval code when the harness or the data is the actual problem — the v3.1 generalization holds: any benchmark keyed on a mutable surrogate rots into false failures; key on durable content and KEEP THE GROUND-TRUTH CURRENT as the system evolves.

**`memory_add` MCP tool fails `No module named lancedb` because the memory PROVIDER plugin is wired to the dead `knowledge.py` — fix the plugin, not just the call site (root-caused 2026-06-29).** The `memory_add`/`memory_search`/`memory_remove` tools AND the `memory()`-tool interception all route through `~/.hermes/plugins/supabase/__init__.py` (`SupabaseMemoryProvider`). Post-migration that plugin still lazy-loaded the flat `scripts/knowledge.py` (`_get_k()` → `k.store()`/`k.search()`) which imports `lancedb` inside `get_db()` → every write dies `ModuleNotFoundError: lancedb`. **This is the real reason facts silently land in MEMORY.md instead of Supabase even with `memory.provider: supabase`:** the native `memory(action='add')` tool writes MEMORY.md unconditionally, and the provider's `on_memory_write` hook that's SUPPOSED to redirect that write into Supabase was itself crashing on the lancedb import — so the redirect never happened and the fact stayed in the hot file. **The fix is to repoint the plugin at the `scripts/knowledge/` package** (the Supabase backend), in ALL FIVE call sites: `_add` → `from knowledge.storage import store`; `_search` → `from knowledge.unified_search import unified_search`; `on_memory_write` → `from knowledge.storage import store`; `_auto_classify` → `from knowledge.classifier import classify` (the function is `classify`, NOT `classify_sector`) + a raw `urllib` PATCH to `/rest/v1/knowledge?id=eq.<id>` (use `get_supabase()` from `knowledge.supabase_client` for url/key — the helper there is `get_supabase()`, NOT `get_client()`, and `_supa_post` is POST-only so it can't do the sector PATCH); `_remove` → raw `urllib` DELETE. After patching, smoke-test the whole path (store → classify-PATCH → select-verify → delete) in the background — the qwen3 model warm-up makes a cold foreground run exceed a 60s timeout. **General rule: after ANY knowledge-store backend migration, grep every tool/plugin/cron for `_get_k(`, `knowledge.py`, `lancedb`, `import knowledge` — a flat-file `knowledge.py` import is the migration's stale-call-site signature; the live code lives in the `knowledge/` package.** Manual fallback that always works regardless of plugin state: from `~/.hermes/scripts`, `sys.path.insert(0, '.')` then `from knowledge.storage import store; store(text, tags=[...], priority='high')`. `store()` takes `text`, `tags`, `priority`, `source` — there is NO `sector_path` kwarg (sector auto-classifies on write). Returns the new row id.

  **EXACT function names in the `knowledge/` package (verified live 2026-06-29 — wrong names cost three patch iterations this session, so pin them):**
  - `knowledge.storage.store(text, tags=None, priority='normal', source='')` — the writer. NOT `add`, NOT `insert`.
  - `knowledge.classifier.classify(text, threshold=None)` — returns the sector_path list. The function is **`classify`, NOT `classify_sector`** (which does not exist and raises `ImportError: cannot import name 'classify_sector'`).
  - `knowledge.supabase_client.get_supabase()` — returns `(url, key)` tuple. The helper is **`get_supabase()`, NOT `get_client()`** (which does not exist → `ImportError: cannot import name 'get_client'`). There is no `supa.table(...).update().eq().execute()` ORM surface here — it's raw REST.
  - `knowledge.supabase_client._supa_post(path, payload, prefer=...)` is **POST-only** — it CANNOT do the sector-classify PATCH or a DELETE. For PATCH (set sector_path) and DELETE (remove fact) build a raw `urllib.request.Request(f"{url}/rest/v1/knowledge?id=eq.{quote(id)}", method="PATCH"|"DELETE")` with `apikey` + `Authorization: Bearer {key}` headers (Content-Type + `Prefer: return=minimal` for PATCH). `get_supabase()` gives the url/key.
  - `knowledge.unified_search.unified_search(query, top_k=N)` — the reader used by `memory_search`.
  **Smoke-test recipe after any plugin/package edit (run in BACKGROUND — cold qwen3 warm-up exceeds a 60s foreground timeout):** `store(content) → classify(content) → REST PATCH sector_path → _supa_select('id=eq.<id>&select=id,text,sector_path') to verify → REST DELETE to clean up`. A clean store→classify→verify→delete with zero `lancedb` in the traceback is the pass signal.

 Enabling `SECTOR_FILTER=true` (pre-filter the RPC pool to the query's classified sector) REGRESSED P@5 0.62→0.58 because the embedding classifier was only ~70% accurate — it routed queries to the wrong sector, so the correct fact got filtered out before retrieval saw it. The general rule: any pre-filter (sector, metadata, tag) that narrows the candidate pool can only help if the thing it filters on is MORE accurate than the cost of the recall it removes. Always A/B a new filter against the no-filter baseline FIRST, and keep it behind an env flag so it's instantly reversible. A broken filter silently caps your ceiling — it never errors, it just quietly returns fewer right answers. Same logic killed the metadata_filter (wired through against a store with no populated metadata → 0 hits). Full case + the `_build_search_context` two-bug fix (import-vs-call gating + unpopulated-store filter) that took P@5 0.62→0.74: `references/embedding-classifier.md`.

**NEVER bulk-classify sector_path across the store — every variant regresses retrieval (verified 2026-06-24 and 2026-06-25, three attempts).** When a fact is missing its `sector_path` and you want SECTOR_BOOST to help it, the instinct is to backfill sectors across all facts at once. This backfires every time. Three variants all failed: (a) `_detect_query_sector()` on full fact TEXT — caught by the >200-char guard (crashes by design); (b) the same classifier on joined TAGS (short enough to slip past the guard) — dropped P@5 86%→64%; (c) a hand-written tag→sector map PATCHed across all ~1100 facts — same 64% regression. Root cause: tags and text share tokens across domains (`dashboard` appears in kanban, infra, and home-automation facts), so a fact lands in the wrong sector and then out-boosts the correct answer for unrelated queries — cross-contamination is strictly worse than empty sectors. **Correct procedure is per-fact targeted:** identify the specific failing query, store/locate the one fact it needs, set THAT fact's `sector_path` with a single Supabase PATCH, re-benchmark. Sectors accrue at store-time (webhooks, manual enrichment), never via a store-wide pass. If the `references/supabase-hygiene.md` doc still frames "batch classification via `_detect_query_sector`" as routine, treat that as superseded — it is the exact anti-pattern. When asked to "apply Fibonacci to the whole KB / DB," the right move is to fix individual failing queries with targeted facts and say so, NOT run a bulk classifier.

**One stray `critical`-priority fact poisons unrelated queries (verified 2026-06-25).** The importance multiplier on `critical` is large enough to swamp vector similarity, so a single mis-prioritized fact (a Neo4j Aura row left at `critical`) ranked #1 for `fibonacci sector`, `memory offload`, and `memory sanitize` — none of which it answers. When the same wrong top hit keeps surfacing across unrelated queries, audit `SELECT id,text FROM knowledge WHERE priority='critical'` and downgrade general infra facts to `high`. Reserve `critical` for the one fact that must win its OWN domain.

**CLI `knowledge.py search` latency is COLD-START, not the DB — profile before "optimizing" retrieval (proven 2026-06-17).** A baseline measured `knowledge.py search` at **7.9s** and the instinct was "the DB is slow, build an index / switch models." Profiling the call into phases proved the opposite — the vector search itself is **~70ms**; the other ~6.7s is pure per-process cold-start:

| Phase | Time | Nature |
|---|---|---|
| `import sentence_transformers` (pulls torch) | ~3.6s | cold-start tax |
| `SentenceTransformer('all-mpnet-base-v2')` load (420MB) | ~1.9s | cold-start tax |
| `import lancedb` | ~1.2s | cold-start tax |
| db connect + open_table | 0.016s | instant |
| embed query | 0.039s | instant |
| **vector search (438 rows)** | **0.031s** | the actual retrieval |

Drop-in profiler (run via the venv python so torch/lancedb resolve):
```python
import time; t0=time.perf_counter()
import lancedb; t1=time.perf_counter()
from sentence_transformers import SentenceTransformer; t2=time.perf_counter()
m=SentenceTransformer('all-mpnet-base-v2'); t3=time.perf_counter()
db=lancedb.connect(os.path.expanduser('~/.hermes/knowledge_db')); tbl=db.open_table('knowledge'); t4=time.perf_counter()
qv=m.encode(['<query>'],normalize_embeddings=True)[0]; t5=time.perf_counter()
r=tbl.search(qv).limit(16).to_list(); t6=time.perf_counter()
for label,a,b in [('lancedb-import',t0,t1),('st-import',t1,t2),('model-load',t2,t3),('connect',t3,t4),('embed',t4,t5),('SEARCH',t5,t6)]:
    print(f'{label}: {b-a:.3f}s')
```

**The conclusions that follow from this (do NOT skip the profiling step and jump to a fix):**
1. **The hot path is ALREADY warm — verify before declaring a user-facing latency problem.** B-full's per-turn RAG loads the model ONCE at gateway start (`_bfull_engine()` caches it module-level in `gateway/run.py`, ~line 1488) and reuses it. Live in-conversation retrieval is ~70ms, NOT 7.9s. The 7.9s only hits **cold callers**: manual CLI `search` and crons that shell out (`auto-index`, dedup, KB audit). So this is a cron/CLI-efficiency question, not a latency emergency — reframe urgency accordingly.
2. **At small scale (438 rows) brute-force kNN at 31ms beats any ANN index.** Do NOT build an IVF_PQ vector index to "speed up search" here — it's premature until ~50–100k rows. The FTS index already present (for BM25 hybrid) is the only index this scale needs.
3. **Don't downgrade the embedding model for speed.** Switching mpnet→MiniLM saves only ~1.4s (the torch import dominates and stays) and sacrifices the recall the mpnet upgrade was made for. Reject it.
4. **The only real latency fix is killing the cold-start, not touching the DB:** a long-running warm search daemon (holds model + lancedb connection, exposes a localhost/socket endpoint; CLI becomes a thin client → 7.9s→~100ms for all callers — same pattern as B-full's warm engine and the codegraph daemon), OR offload embedding to a Metal `nomic-embed-text` on the inference node over the tailnet (kills torch entirely but couples ALL KB reads to that node being up — a resilience hit; usually not worth it). Gate either as a new supervised service + watchdog entry.

**Daemon SILENT-DEGRADATION failure: orphaned socket → every search cold-loads again (proven 2026-06-18).** The daemon can report `active (running)` for hours while `knowledge.py search` silently falls back to the 7.7s cold path — because the *socket file* `~/.hermes/run/kb.sock` got deleted out from under the live process (a previous restart's `_cleanup` unlinked it, a tmp sweep, or a manual `rm` of the run dir). The kernel socket (inode) stays open on the daemon's fd, but the filesystem entry is gone, so clients `connect()` → `FileNotFoundError` → transparent fallback to in-process cold load. Nothing errors; it's just slow forever. **Tells:** (a) `ls -la ~/.hermes/run/kb.sock` → "No such file or directory" while the unit is active; (b) `knowledge.py search` prints `Loading weights: 100%` and takes >7s; (c) `grep kb.sock /proc/net/unix` still shows the daemon bound to the path (kernel-side) but the dir entry is missing. **Diagnosis one-liner:** `ls -la ~/.hermes/run/kb.sock 2>&1; time python3 ~/.hermes/scripts/knowledge.py search test 2>&1 | grep -c Loading` — missing file + a `Loading` line = orphaned socket. **Fix:** restart the daemon (`systemctl --user restart hermes-kb-daemon`) — it re-creates the socket on boot. Verify: `ls -la ~/.hermes/run/kb.sock` shows a fresh `srw-` file, and a second `knowledge.py search` returns in ~0.5s with no `Loading weights` line. **Hardening gap to close if it recurs:** `infra_watchdog` checks the *unit* is active but NOT that the socket file exists AND answers — add a probe that `connect()`s to the socket (not just `os.path.exists`, since the orphaned case can leave a stale file too) and restarts on failure. The unit being "up" is not proof the fast path works.

**→ The warm daemon is BUILT and LIVE (2026-06-17). `knowledge.py search` is now ~0.5s.** Don't rebuild it — operate it. Full build details, the two correctness traps, and the verification recipe are in `references/warm-search-daemon.md`. Capsule:

**Sector classification (Fibonacci):** `references/sector-classification.md` — full tree, weights, query detection rules, pitfalls.
- **Two-part fix, BOTH required.** (a) `scripts/kb_daemon.py` = Unix-socket server at `~/.hermes/run/kb.sock` holding model+lancedb warm; `scripts/kb_client.py` = thin pure-stdlib client. (b) **The bigger win was making `lancedb`+`sentence_transformers` imports LAZY in `knowledge.py`** (moved inside `get_db()`/`get_model()`). Without the lazy-import half, `knowledge.py search` still paid ~4.8s just *importing* the heavy libs before it could even reach the daemon — the daemon alone only got it to 5.9s. Lazy imports + daemon together = 0.5s. **If you ever see `knowledge.py search` slow again, first check those two imports are still lazy** (a refactor that hoists them back to module top silently kills the fast path).
- **Supervision:** `~/.config/systemd/user/hermes-kb-daemon.service` (`Restart=always`, enabled at boot). `infra_watchdog.py` auto-restarts it if down — SILENT on clean heal, P1 only if restart fails (pure-acceleration service: in-process fallback means a dead daemon degrades, doesn't break).
- **Transparent fallback:** `knowledge.py search` tries the daemon via `kb_client`, falls back to in-process `search()` on ANY `DaemonUnavailable`. Force in-process for benchmarking with `KB_NO_DAEMON=1`. The daemon is never a hard dependency.

**Feature gate env vars must be checked at CALL TIME, not import time — module-level constants silently ignore `os.environ` changes after import (proven 2026-06-25).** The three search-time feature gates (`ENRICH_QUERY`, `QUERY_EXPAND`, `RERANK`) were initially implemented as module-level constants: `RERANK = os.environ.get("RERANK", "") == "true"`. This evaluates ONCE at `import knowledge` and never re-reads the environment — so `os.environ['RERANK'] = 'true'` after import has zero effect, and `search()` silently uses the stale `False` value. A test harness that imports knowledge then sets env vars will see all three gates permanently off regardless of what it sets. The tell: setting `os.environ['RERANK'] = 'true'` and calling `search()` returns RRF-scale scores (0.112) instead of cosine-scale (0.643). Fix: use functions that call `os.environ.get()` at invocation time: `def _rerank_enabled(): return os.environ.get("RERANK", "") == "true"`. Any new env-var feature gate in knowledge.py must follow this pattern — never a module-level constant.

**(History — enrichment code deleted 2026-10-16.) Query enrichment hurts retrieval at <1,000 facts — template-based enrichment dilutes query signal, KB-term injection adds noise (proven 2026-06-25, three iterations tested).** Three enrichment strategies were tested against a 5-query relevance benchmark, each gated by `ENRICH_QUERY=true`. All three produced WORSE recall than the no-enrichment baseline (3/5):
  - **Template enrichment** (`enrich_claim.py` default): prepends ~500 chars of domain template prose → 1/5 recall. The template dominates the 768-dim embedding; the original query terms contribute <10% of the cosine weight.
  - **Identifier injection** (v1 query mode): appends hostname/IP/port/function-names from local source files → 2/5. Generic identifiers (LH01, <LH01-tailnet-ip>, 8787) are added to EVERY query regardless of domain, adding noise.
  - **KB-only term injection** (v2 query mode, current): searches KB with claim + domain terms, extracts key words from top hits → 2/5. At 353-fact scale, the KB returns the same ~20 high-priority facts regardless of query; injected terms converge to "dashboard chat bug telegram streaming mode" for every query.
  The root cause is SCALE, not design: with <1,000 domain-diverse facts, the KB can't produce query-discriminating terms. The architecture (query-mode KB search → term extraction → injection) is correct but needs data density to work. **Do not enable `ENRICH_QUERY` until the KB is substantially larger.** The recommended pipeline is `QUERY_EXPAND=true RERANK=true` — preserves baseline recall (3/5) while producing actionable cosine scores (0.50–0.65).

**Embedding model migration requires full re-index — switching models silently breaks all retrieval (proven 2026-06-25).** Stored vectors are model-specific. Switching from `all-mpnet-base-v2` → `nomic-embed-text` (or any other model) produces vectors in a DIFFERENT space — all cosine distances to stored vectors become meaningless, and `match_knowledge` returns zero results. The tell: `knowledge.py search` returns 0 hits for queries that returned results before. The fix: re-embed ALL stored facts with the new model and update the `vector` column. Until re-indexed, search() must use the same model the vectors were embedded with. The embedding model for queries MUST match the embedding model for stored vectors. **Mixed vectors (some mpnet, some nomic) in the same index produce WORSE results than either pure model — a partial re-index is the worst of both worlds.** The tell: benchmark drops from 40% (pure mpnet) to 33% (mixed).

**RRF k=60 is too high for <500 facts — scores compress to 1.07× range (proven 2026-06-25).** At 152-fact scale, `1/(60+rank)` produces 0.015–0.016 for all results — no discrimination between rank 1 and rank 50. The fix: `match_knowledge_v3` uses k=20 (3.3× range, 0.048–0.014). Deployed via Supabase migration. Client-side `_rrf_merge()` also updated to k=20. Never revert to k=60 at this corpus size.

**Dedup can DELETE the ranking carrier — coverage-exists is not coverage-ranks (proven 2026-07-02).** Before deleting duplicate or kitchen-sink facts, it is NOT enough to verify every benchmark `expected_substring` still exists in ≥1 surviving row. The surviving canonical fact must also RANK for the affected queries — and often it can't, because the deleted duplicate was the copy whose wording bridged the query vocabulary. Live case: deleting 8 dup/kitchen-sink rows dropped P@5 98→96 deterministically; "where is Mealio hosted" lost because the surviving Mealio fact fell out of top-10, and "vector search knowledge base" lost because the surviving embed-model fact never contains the literal word "pgvector" (the expected substring) — the deleted kitchen-sink row had been the top-5 pgvector carrier. **Rule: dedup and alias-enrichment are ONE operation, not two.** Procedure: (1) full-row JSON backup; (2) for each kill-candidate, identify which benchmark queries it currently ranks for; (3) DELETE the dups AND DELETE+RE-STORE the surviving canonical fact with query-shaped aliases covering those queries' vocabulary (literal expected substrings + natural phrasings); (4) re-run the eval double-run — deterministic pass on both runs is the close signal. This sequence took the store to P@5=100% / MRR=0.9429 at 171 rows. A dedup pass that only deletes is a silent recall regression waiting for the next benchmark run.

**Multi-topic facts poison cross-domain retrieval — one fact mentioning 4 systems dominates queries for ALL of them (proven 2026-06-25).** A single fact like "Fleet repos: hermes-dispatcher, ha-dashboard, mealio, wall-dashboard..." creates an embedding that spans multiple domains. When querying "Hermes config" or "Hermes WebUI", this fact outranks the specific-domain enriched fact because its embedding captures terms from all domains it mentions. The fix: split multi-topic facts into atomic per-domain facts. Keep a high-level overview fact SHORT (≤1 sentence per item, no deep technical terms) and create separate enriched facts for each sub-domain with domain-specific identifiers, ports, and verification commands. The overview fact should be a pointer, not a competitor. Verify: after splitting, the domain-specific query should return the domain-specific fact at rank 0, not the overview fact.

- **`knowledge.py store --priority X --tags Y` leaks flags into the stored text (proven 2026-06-25).** The `store` command parser writes `--priority high --tags memory,embedding Actual fact text` verbatim — the CLI flags become part of the stored fact. Retrieved facts will display with these prefixes. Fix: use env vars instead (`KNOWLEDGE_PRIORITY=high KNOWLEDGE_TAGS=memory,embedding knowledge.py store "Fact text"`) — env vars are stripped before storage, flags are not.

**Query-shaped aliases fix domain-fact invisibility (proven 2026-06-25).** Domain-specific facts (e.g. "Coder fleet trimmed to 4: coder, coder-b...") fail retrieval because FTS token overlap is too low against natural queries (e.g. "coder fleet workers count"). The fix: append query-shaped aliases to the stored text — terms a human would use to search for this fact. Example: `"... coder fleet workers count 4 workers coding agents parallel coder-b coder-c coder-d"`. This adds no new information, just bridges the vocabulary gap between stored terminology and search terminology. Proven: 3 facts went from "not in top 50" → rank 0, P@5 rose from 86.7% → 100%. Procedure: (1) identify domain facts that fail retrieval, (2) DELETE old from Supabase, (3) RE-STORE with enriched text + same priority/tags via env vars, (4) verify with the target query. Always DELETE+RE-STORE (never PATCH) — the text change requires re-embedding.

**Supabase REST PATCH for cosmetic text fixes (proven 2026-06-25).** For CLI flag leakage or text trimming (cosmetic fixes where the embedding doesn't need regeneration), PATCH the `text` column directly via Supabase REST: `PATCH /rest/v1/knowledge?id=eq.<uuid>` with `{"text": "cleaned text"}`. Returns 204 on success. This does NOT re-embed — the vector stays the same. For factual corrections that change meaning, use DELETE+RE-STORE (below) to regenerate the embedding. The tell: patching text of a multi-topic fact won't fix its cross-domain ranking dominance — the old embedding still spans all domains.

**RRF threshold filtering kills all results — RRF scores are on a different scale than cosine (proven 2026-06-25).** The `_passes_threshold()` function applies cosine-scale thresholds (0.80/0.55), but RRF scores are `1/(60+rank)` — max ~0.016, min ~0.008. Every RRF result gets filtered out by the dual-threshold gate. Telltale: `search()` returns 0 hits while the underlying RPC returns 15. Fix: (1) add `rrf_mode` parameter that skips threshold when true, (2) pass `match_source` field through the hit dict in search() so `rrf_mode` can be detected, (3) call `_passes_threshold(hit, query, rrf_mode=('match_source' in hits[0]) if hits else False)`. The RRF fusion already handles ranking — no client-side threshold needed in RRF mode. Two-line bug, one-hour debug.

**Supabase RPC schema cache staleness — reload after EVERY function change.** PostgREST caches function signatures. After `CREATE OR REPLACE FUNCTION`, the REST API may return 400 (Bad Request) or 404 (Not Found) because it's using a stale cached signature. The fix is a one-liner: `NOTIFY pgrst, 'reload schema'` via `mcp_supabase_execute_sql`. Do this after EVERY migration that touches an RPC function. The tell: direct SQL works (`SELECT * FROM match_knowledge_v2(...)`) but REST returns 400/404.

**The `match_source` field MUST survive the hit dict construction (proven 2026-06-25).** The RRF threshold fix above is only HALF the bug. The `search()` function builds hit dicts at line ~676 with explicit keys — and the original construction OMITTED `match_source`. So `'match_source' in hits[0]` is ALWAYS False regardless of RPC output, and `rrf_mode` never activates. The tell: manual replication (importing `_supa_post` directly, keeping all RPC fields) works fine, but the actual `search()` function returns 0 hits. Fix: add `'match_source': r.get('match_source', '')` to the hit dict construction. This is the TINIEST missing line — one key in one dict — but without it the entire RRF/dual-threshold fix silently fails.

**The benchmark MUST match on `expected_substring`, NOT `expected_id` — IDs are volatile and a stale benchmark fakes a quality regression (proven 2026-06-17).** This bit hard: a baseline `eval` showed **P@5=50%, MRR=0.5** and the obvious read was "retrieval is broken, half the queries fail." It was a LIE. Every "failure" had an `expected_id` that **no longer existed in the DB** — the weekly dedup cron re-chunks facts and assigns NEW ids on every run, so an id-based benchmark self-invalidates after the first dedup. The facts were all still present and retrieving correctly under new ids; only the test's ground-truth pointers were dead. Diagnosis tell: when `eval` fails, check whether the `expected_id`s still exist (`tbl.search().where("id='<id>'")`) BEFORE concluding retrieval degraded. The fix (now in place):
  - Benchmark entries use `{"query": "...", "expected_substring": "<durable unique phrase from the target fact>", "description": "..."}`. Content survives re-chunking; ids don't.
  - `eval_benchmark()` matches `expected_substring.lower() in hit.text.lower()` (rank = first matching hit), backward-compatible with legacy `expected_id`.
  - **True quality after the fix: P@5=100%, MRR=0.92.** Retrieval was excellent the whole time. The lesson generalizes: **any benchmark/test keyed on a mutable surrogate id will rot into false failures — key it on durable content.** When a long-untouched eval suddenly "regresses," suspect the harness before the system.

 every infrastructure question triggers rediscovery — the user teaches you again, you store it again, and the cycle repeats. The fix is the session-start ritual above. If the user ever says "I have to reteach you" or "what happened to this?", the agent failed to query Supabase. Fix immediately.

**When the user reports the system is WRONG/BROKEN/changed, `session_search` is step ONE — not live investigation (proven 2026-06-30, the user had to drag me to the answer TWICE in one session).** This is the recall gate AGENTS.md mandates, and it fails exactly when it matters: under investigation pressure I pattern-matched "diagnose" → "query the live system from scratch" and skipped recall. When the user asked "are skills properly embedded?" and then "something is wrong, they were inserted into Domain," I went straight to Supabase/filesystem probing both times. The answer — a June 26 cron classified skills' `sector_path`, documented in a reference file — was one `session_search("skills sector_path classified")` away. Same failure recurred on the b-full question: the entire removal history (deployed 6/9 → graph-poisoned 6/25 → fixed 6/26 → removed 6/26) was reconstructable PURELY from `session_search` + backup filenames + the deleted skill in `.curator_backups/`, no live probing needed. **The discipline, in order, before ANY "what happened to X / why is X wrong / X is broken" investigation:**
  1. `session_search(query="<topic>", sort="newest")` — past sessions hold cross-session forensic state the live system can't show you (WHO changed it, WHEN, WHY). FTS-empty ≠ absent: vary the query terms, try `sort="oldest"` for origin questions, and check `role_filter="user,assistant,tool"`.
  2. `memory_search("<topic>")` — Supabase facts.
  3. Scan `~/.hermes/references/` and `skills/.curator_backups/` — cron reports and deleted skills land here as files, NOT as searchable knowledge rows (a structural gap: state-changing cron ops write a `.md` to references/ but no `knowledge.py store(...)` fact, so they're invisible to memory_search).
  Only AFTER recall comes up empty do you probe the live system. **The tell you're violating this: you're three tool calls into a live investigation and haven't run session_search.** A backup file named `rm-bfull` or a reference doc named `taxonomy-refactor-report` is the breadcrumb — the forensic record exists, you just have to search for it before deriving it.

**The reteaching trap applies to the SKILL too, not just the data (proven 2026-06-08).** Andrew corrected this twice in one session: asked to build a LanceDB auto-retrieval feature, the agent went straight to reading raw `knowledge.py` + gateway source and built a prototype — **without loading THIS skill first.** The skill already documented every finding the agent "discovered": the `auto-index` corpus-wide over-ingest pitfall (which the agent then hit and had to recover from), the `staleness_scan.py` reusable probe (which the agent hand-rolled from scratch in `/tmp`), and `references/auto-retrieval-architecture.md` (the entire cue-driven-retrieval investigation, already written). The lesson: **when a task touches LanceDB / the knowledge store, `skill_view('knowledge-store')` is step ONE — before reading source, before prototyping.** Re-deriving from source what a skill already holds IS the reteaching trap, just aimed at institutional knowledge instead of user data. Generalize: for any task in a documented domain, load + sweep the relevant skills before acting; the skill may already contain the answer, the pitfall, or the reusable script. Note also: the skill library is curated in near-real-time (reference docs appear mid-session), so a skill may already hold work done minutes ago — another reason to load before re-deriving.

**CLI `store --contextualize` yields only the SYNTHETIC-FLOOR prefix, not a real breadcrumb (proven 2026-06-08).** Running `knowledge.py store --contextualize "<fact>"` from the command line produces the generic fallback prefix `"This passage is from cli input."` — NOT a topic-aware situating sentence. The contextual-prefix pipeline derives its situating sentence from document structure (heading breadcrumb + surrounding chunks); a bare CLI string has no structure to anchor to, so it falls to the synthetic floor. Consequence: offloading a hot-memory fact via CLI `--contextualize` gives only a marginal retrieval gain over plain `store`. The REAL contextualization value comes from the fact living in a reference doc (e.g. `infrastructure-summary.md`) that gets chunked via `contextualize-file`/`auto-index`, where the heading context is real. So when offloading topology/infra facts: put the authoritative copy in the reference DOC (chunked, real breadcrumbs) and treat the CLI row as a secondary backstop — don't expect the CLI `--contextualize` flag alone to deliver the prefix benefit.

**Durable reference files beat Supabase for topology.** For complex infrastructure (multi-host, nginx configs, DB locations), write a reference file under `~/.hermes/references/` in addition to Supabase facts. Supabase is semantic search — great for "how do I reset the admin password?" but less reliable for "what's the current state of ALL my servers?" A single markdown file read at session start is stronger than 10 fragmented Supabase facts.

**Indexing coverage silently lags — audit it, don't assume the cron caught everything (proven 2026-06-16).** A retrieval-improvement pass found only **8 of 28** `references/*.md` docs actually had chunks in Supabase, and the every-turn-injected `AGENTS.md` + `SOUL.md` had NEVER been indexed (so their dense rule/procedure content was un-searchable — you could only get it from the raw injected blob, not a targeted `knowledge.py search "write gate procedure"`). The `auto-index` cron is a backstop, not proof of coverage. To audit which docs are missing:
```python
import lancedb, os
df = lancedb.connect(os.path.expanduser('~/.hermes/knowledge_db')).open_table('knowledge').to_pandas()
indexed = set((s or '').split('/')[-1] for s in df['source'].fillna(''))
refs = {r for r in os.listdir(os.path.expanduser('~/.hermes/references')) if r.endswith('.md')}
print('UNINDEXED:', sorted(refs - indexed))
```
Then `contextualize-file` each missing doc (targeted, NOT `auto-index` — see the corpus-wide over-ingest pitfall). **Deliberately SKIP from indexing:** drafts (`*-draft.md`), dated audit logs (`staleness-audit-YYYY.md`), archives, the `honcho-confabulation-blocklist.md` (indexing a doc that NAMES false facts risks retrieval surfacing them as true), and offload audit-logs — these are frozen history/meta, not retrieval targets. AGENTS.md/SOUL.md DO belong indexed but score ~0.76–0.79 (just below the 0.80 B-full floor), so they're manually searchable without B-full double-injecting content that's already in the system prompt — exactly right.

**`stale-check` is mtime-based and misses CONTENT staleness — a recently-touched doc can hold badly-wrong facts (proven 2026-06-16).** `knowledge.py stale-check` reported "0 stale facts" while `infrastructure-summary.md` still described the OLD Hetzner `vps-b` topology (Hermes had migrated to the local LH01) and a `2026-06-02-state` snapshot still served "Manifest at localhost:2099" (decommissioned). mtime says "fresh"; the CONTENT is a lie. The tell: a retrieval probe returns a high-scoring hit whose facts contradict the live system. Fix pattern (two distinct cases):
  1. **Live-state reference doc** (`infrastructure-summary.md`): verify against the live system FIRST (`hostname`, `pgrep`, `tailscale status`, port probes — never trust the injected memory block, which itself lagged this session), back up the doc + full-row LanceDB JSON, patch the stale lines in the doc, then PURGE its old chunks and re-index: get the chunk IDs by source basename, `tbl.delete('id IN (...)')`, then `contextualize-file` the corrected doc. Re-`build-graph`. Verify with a probe that the corrected fact now ranks #1.
  2. **Dated snapshot** (`YYYY-MM-DD-state.md` in the Obsidian archive): this is FROZEN HISTORY — do NOT edit the file (deleting a record of a past state is wrong). Instead purge ONLY its LanceDB chunks so retrieval stops serving stale state as current; the archive file stays intact. Verify 0 chunks remain for that source.
  Always: full-row JSON backup before the delete, verify the row-count delta matches expectation, re-`build-graph` after.

**Contextualized vs plain-text coexistence:** The v2.0 schema adds `context_prefix` and `body_hash` columns to the existing table via auto-migration. Old rows get NULL values — they continue to work for search but lack the situating prefix. Re-indexing with `--contextualize` will add new contextualized entries alongside them (different body hashes due to different chunking, so no collisions). Old entries can be deleted manually if desired, but coexistence is harmless — search returns both.

**Bulk-delete by filter (e.g. purging stale source rows):** LanceDB's `tbl.delete()` takes a SQL-like WHERE expression. To delete rows matching specific IDs:
```python
import lancedb
db = lancedb.connect(os.path.expanduser('~/.hermes/knowledge_db'))
tbl = db.open_table('knowledge')
df = tbl.to_pandas()
ids = df[df['source'].str.contains('stale-topic', na=False)]['id'].tolist()
tbl.delete('id IN ("' + '", "'.join(ids) + '")')
print(f'Remaining: {tbl.count_rows()}')
```
This is the safe pattern for purging all rows from a removed reference file — get IDs via pandas filter, then delete with the IN expression. Do NOT delete by `source` regex directly (LanceDB SQL dialect is limited) — get IDs first, then delete.

**`auto-index` is CORPUS-WIDE, not single-file — use `contextualize-file` for targeted reingest (proven 2026-06-08).** To reingest ONE rewritten reference doc (e.g. after correcting `infrastructure-summary.md`), do NOT reach for `knowledge.py auto-index`. Despite the "index changed files since last run" framing, in practice it re-chunked ALL 21 reference docs in one pass and ballooned the store 143 → 348 rows (~205 duplicate chunks: 41 exact-text, 100 by body_hash). The correct tool for one doc is `python3 ~/.hermes/scripts/knowledge.py contextualize-file references/<doc>.md` — it touches only that file.
  **Recovery if you over-ingest (this is the reusable rollback):** the bad batch shares one ingest timestamp window. Find it via `Counter(str(x)[:16] for x in df['stored_at'])`, identify the boundary (the over-ingest rows all have `stored_at >= <this-session-start-epoch>`), then `tbl.delete('stored_at >= <epoch>')` to peel the entire batch back to the pre-call count in one op — far cleaner than trying to dedup-untangle. Then redo the intended stores surgically. The full-row JSON backup (below) is what makes this a non-event: ALWAYS export every row to `references/_archive/lancedb-full-<ts>.json` BEFORE any batch mutation, so any delete is one re-insert from undo.
  General rule: before any cold-store batch op, (1) full-row JSON backup, (2) note the row count, (3) verify the count delta matches expectation on the very next call — catching a 143→348 surprise immediately is what kept it a 2-minute fix instead of a corrupted store.

**GIL cleanup crash is benign:** Iterating LanceDB rows with `tbl.search().limit(N).to_list()` inside a Python `-c` one-liner can produce `Fatal Python error: PyGILState_Release` on interpreter exit. This is a cleanup artifact from PyArrow/numpy GIL teardown — the data was read successfully and the exit code may still be 0. If you see this, extract what you need before the loop ends, or use `tbl.to_pandas()` (processes all rows at once, less teardown contention). The script-file form (`python3 script.py`) is cleaner for large iterations.

**Data quality audit (2026-06-06, post-cleanup: 155 rows):** Three known issues in the live `knowledge` table:
1. **43 rows with empty source** — writes that didn't pass a `source` tag (as of pre-cleanup count; may be lower now). Data intact and searchable, just unattributed. The `knowledge.py store` command uses the `source` kwarg; always pass it.
2. **83 rows with `NaN` body_hash** — pre-v2.0 entries written before dedup was implemented. These can't be dedup-checked but don't cause errors.
3. **9 actual duplicate rows** — all have empty body_hash string (distinct from `NaN`). Appear to be a bug in the capture script when `source` is also empty. Low impact at current scale (169 rows) but will compound over time.
To audit: `python3 -c "import os; import lancedb,pandas; db=lancedb.connect(os.path.expanduser('~/.hermes/knowledge_db')); df=db.open_table('knowledge').to_pandas(); print(len(df[df['source'].str.strip()=='']),'empty source;', len(df[df['body_hash']=='NaN']),'NaN hash;', len(df[df['body_hash']!='NaN'][df[df['body_hash']!='NaN'].duplicated(subset=['body_hash'], keep=False)]),'dups')" 2>/dev/null`

**Dedup scanning must strip the contextual prefix before scoring (proven 2026-06-08).** The v2.0 situating prefix (`"This passage is from {doc}: {heading}."`) is IDENTICAL across every chunk of one source doc, so a naive cosine dedup scan on the stored `text` inflates similarity between otherwise-distinct chunks — producing a flood of phantom "duplicates" that are really just adjacent sections of one note. Acting on that report (deleting "the less-informative entry") punches holes in a multi-chunk doc's retrieval coverage. The corrected `~/.hermes/scripts/dedup_scan.py` (report-only, never mutates the DB):
  1. **Re-embed prefix-stripped text IN MEMORY for scoring only** — stored vectors are never read or altered (prefix stays in the DB where it helps retrieval). Strip the leading `"This passage is from …"` line, re-encode with the same model, score on real content. In one run this cut 23 phantom pairs → 10 honest ones.
  2. **Tier the output** — HIGH (≥0.95, review for deletion) vs REVIEW (0.85–0.95, usually fine). Don't present a flat list that implies every pair is a dup.
  3. **Annotate `[same-source]` vs `[cross-doc]`** — same-source pairs are almost always normal chunk overlap (keep both); a same-source HIGH can mean a whole doc was ingested twice (real dup); a cross-doc HIGH is a genuinely redundant note worth collapsing. This makes the one interesting signal visible instead of buried.
  General rule for any same-document chunk comparison: a shared boilerplate prefix is noise — score on the body, not the stored text.

**ROOT CAUSE of the duplicates: the overlap chunker manufactures headerless twins that exact-hash dedup can't catch (root-caused + fixed 2026-06-08).** The dedup scan above treats the SYMPTOM; this is the source. Mechanism: `chunk_overlap()` overlaps consecutive chunks by 1 paragraph (`i = max(i+1, j-1)`) for retrieval continuity. Markdown headings are standalone paragraphs (split on `\n\n`). On a SHORT section (heading + one table/list, then the section ends), the overlap produces two chunks that are identical except one leads with the `## Header` line and the other doesn't:
  - Chunk N = `"## Manifest\n\n<table>"`  ·  Chunk N+1 = `"<table>"` (the orphaned content paragraph)
  - `body_hash` is `sha256(chunk_body)` — an EXACT hash. The two strings differ by the heading line → different hashes → `find_by_hash` returns nothing → BOTH stored. The dedup only catches byte-identical re-ingestion (same file re-run unchanged), NOT near-identical overlap twins. Tell-tale signature in the data: paired rows where A has `## Heading`, B doesn't, bodies otherwise identical, `stored_at` ~0.7s apart (same ingest pass).
  **Fix (Option A1 — make the existing dedup work, don't add a second one):** normalize every overlap chunk to lead with its leaf heading, so twins become byte-identical and `body_hash` collapses them at ingest. Two helpers in `knowledge.py`: `_leaf_heading_line(heading_stack)` rebuilds `"## Manifest"` from the deepest `(level, heading)`; `_normalize_chunk_heading(body, heading_stack)` prepends it iff the body doesn't already start with that line. Apply it right where `body = '\n\n'.join(chunk_paras)` is built (covers both the normal and internal-sentence-split paths). No cron-schema change, no second dedup pass — it makes the `body_hash` mechanism you already have actually fire.
  **Verify on a THROWAWAY harness before touching the live DB** — the fix is preventive (future ingests), so the live table needs no re-ingest. Load old + new `knowledge.py` as separate modules via `importlib` (copy the `.bak` to a temp `.py` first — importlib can't load a `.bak-*` filename, it needs a `.py` extension), run BOTH `chunk_overlap()` on the REAL source docs that produced the dupes (`~/.hermes/references/infrastructure-summary.md` etc.), and assert: (a) new headerless-near-twin count → 0, (b) zero content lost (every old chunk's core still present in the new corpus). One run: 5 near-twins across 3 docs → 0, no content loss. Only after that passes is the chunker change safe.

**Matching a query against the SKILL corpus must read `tags:` and split hyphens — name+description alone mis-ranks (proven 2026-06-09).** When semantic/keyword-matching a task against on-disk `SKILL.md` frontmatter (e.g. a skill-review/auto-suggest matcher), two defects silently surface the WRONG skill:
  1. **Reading only `name:` + `description:` ignores `tags:`** — where the load-bearing domain terms live. `knowledge-store`'s description says "LanceDB-backed" but its discriminating signals (`lancedb`, `semantic-search`, `vector-db`) are ONLY in `tags:` (under `metadata.hermes.tags`, several lines past `description:`). A "build a lancedb prototype" query lost to `gateway-platform-setup` because the matcher never read the tags. Fix: parse `tags:` too — and scan ~40 frontmatter lines, not ~25, since tags sit in the metadata block well below description.
  2. **Treating `-` as a word char glues compound tags into unmatchable tokens** — `lancedb-backed` / `semantic-search` tokenize as ONE token, so a query word `lancedb` never matches. Fix: hyphens are SEPARATORS in the tokenizer; `semantic-search` → `{semantic, search}`.
  3. **Weight tags+name above description.** A curated tag or the skill name is a far stronger domain signal than an incidental description word — weight them ~3× (require ≥1 tag/name hit, OR ≥3 description hits, to qualify). Flat overlap counting lets a skill with a few coincidental description words outrank the true domain match.
  Lock the fix with a calibration fixture set (the motivating query must surface the right skill) that runs BEFORE any future edit to the matcher — a threshold/weight tweak silently regresses ranking otherwise.

**Heading context stability:** The overlap chunker anchors heading context to the FIRST paragraph in each chunk, not the last. Using the last paragraph is wrong — a high-level heading like "## Cron Jobs" resets the breadcrumb from "Infrastructure > Routing > DB Topology" to "Infrastructure > Cron Jobs", losing the earlier structure. First-paragraph anchor is the most stable descriptor of where the chunk begins. Do not "fix" this to track the deepest or last heading — it's intentionally first.

**Retrieval pipeline (v3.0 — implemented):** `~/.hermes/references/retrieval-pipeline-techniques.md` documented 5 post-retrieval scoring techniques cherry-picked from the memory-lancedb-pro OpenClaw plugin. Items 1-4 (LRU embedding cache, priority/recency scoring, BM25 hybrid fusion, MMR diversity) are now LIVE in `search()` as of v3.0. The knowledge-graph layer (from the GBrain cherry-pick, `~/.hermes/references/gbrain-cherry-pick.md`) is also live via `build-graph`/`graph-query`. Item 5 (cross-encoder rerank, ~80MB model download) remains the only un-implemented future option — gate it behind a flag if ambiguous-query quality ever needs it.

**⚠️ B-full auto-RAG was REMOVED from `gateway/run.py` on 2026-06-26 and is NOT currently live (verified 2026-06-30). The pipeline is BROKEN — offloaded facts no longer auto-inject.** This is the load-bearing correction: for two weeks the sections below described B-full as LIVE, but it was stripped out. `grep -c '_bfull_retrieve' /usr/local/lib/hermes-agent/gateway/run.py` → **0** on the live file. The reconstructed history from session + backup-file forensics:
  - **2026-06-09/10:** B-full deployed live (per-turn cold-store injection, healed by patch_guard — retired 2026-10-16).
  - **2026-06-25:** `GRAPH_TRAVERSE=true` shipped as the knowledge-store production default. This poisoned B-full: graph-neighbor hits carry FIXED arbitrary scores (0.60/0.65/0.55, `match_source='graph_traverse'`) NOT comparable to vector/FTS scores, and B-full's flat 0.10 floor admitted them all → every query injected irrelevant graph neighbors (e.g. querying "B-full deployment" injected `systemctl restart hermes-dashboard` at 0.60), burying the real hits.
  - **2026-06-26 ~00:27:** the poisoning was FIXED — a `GRAPH_FLOOR=0.70` was added to `_bfull_retrieve` so graph_traverse hits (capped at 0.65) never inject; only real vector/FTS hits clear the bar. Backup: `run.py.bak-recall-fixes-20260626-002706`. **The fix was correct and sufficient.**
  - **2026-06-26 ~03:08:** B-full was REMOVED ENTIRELY (backup `run.py.bak-20260626-030817-rm-bfull`) rather than kept with the graph-floor fix. **The removal reason is NOT recorded anywhere** — no commit message, no knowledge-store fact, no session transcript explains it (the only sessions in that window are DeepSeek-delegation subagents doing unrelated fact-classification). It may have been an autonomous agent decision with no human in the loop.
  - **2026-06-26 ~12:19:** the `blite-retrieval-maintenance` skill that documented B-full's build/caching/patch_guard registration was DELETED in the curator skill audit, taking the operational knowledge with it.
  **Net effect (the thing that makes recall feel broken NOW):** facts get offloaded MEMORY.md→Supabase and replaced with `knowledge.py search "X"` pointer cues (by design — `memory_offload.py` line 191, "a pointerless trim is a silent deletion — forbidden"). B-full was the mechanism that made those pointers unnecessary by auto-injecting the offloaded fact every turn. With B-full dead, the pointers are inert (the agent doesn't follow them reflexively) AND the auto-injection that obsoleted them is gone — so offloaded facts are effectively forgotten until explicitly searched. **Restoring B-full WITH the `GRAPH_FLOOR=0.70` fix (already solved on 2026-06-26, just not kept) is the correct fix — it's a gated `gateway/run.py` change.** Before re-deriving the implementation, the seam + caching-gotcha + patch_guard recipe survives in the curator backup at `skills/.curator_backups/skill-audit-deleted-20260626T121948/productivity/blite-retrieval-maintenance/`. The sections immediately below describe B-full's design and consequences and were TRUE while it was live — read them as the spec for restoration, not as current state. Verify live before relying on ANY of it: `grep -c '_bfull_retrieve' /usr/local/lib/hermes-agent/gateway/run.py`.

  **GENERALIZABLE LESSON — a destructive removal with no recorded rationale is itself the bug.** When an agent removes a working feature (B-full) instead of keeping the smaller fix (graph-floor) that already solved the problem, and writes no durable record of WHY, the next session inherits a silently-degraded system with no way to know if it was intentional. Any state-changing removal of a deployed capability MUST leave a `knowledge.py store(...)` fact stating what was removed and why — a backup filename like `rm-bfull` records WHAT, never WHY. The skill below claimed "LIVE, verify with grep" but nobody ran the grep for two weeks; "verify live before relying on it" is only protective if someone actually verifies.

Consequences (TRUE while B-full was live — the restoration spec):

**⚠️ B-full runs ONLY in the gateway/Telegram path, NOT in the WebUI (proven 2026-06-18).** `_bfull_retrieve` is wired into `gateway/run.py` — the Telegram/CLI message handler. The Hermes WebUI uses a SEPARATE codepath (`hermes-webui/api/streaming.py`) that has **zero** b-full integration: `grep -c bfull api/streaming.py` → 0. Hard evidence: querying the session DB for the injection marker — `SELECT COUNT(*) FROM messages m JOIN sessions s ON m.session_id=s.id WHERE m.content LIKE '%Cold-store auto-retrieval%' GROUP BY s.source` — returns 7 for `telegram`, **0 for `webui`**. This is the dominant reason "the WebUI me feels dumber": on Telegram every message gets relevant Supabase facts auto-injected before the model sees it; on WebUI the model gets only the frozen MEMORY.md/USER.md snapshot + whatever it explicitly tool-calls. **The same hot/cold memory files load fine in both** (verified: `MemoryStore.load_from_disk()` reads the same `~/.hermes/memories/*.md` regardless of surface) — the asymmetry is purely the per-turn RAG injection. **Diagnostic when "WebUI seems less aware than Telegram":** it's almost never a memory-loading bug — check (1) is the b-full marker present in webui sessions (it won't be), (2) is the KB daemon socket alive (orphaned-socket pitfall above), (3) did the offload cron strip behavioral entries from MEMORY.md that the WebUI snapshot then froze. **Fix for the asymmetry:** mirror the `run.py` injection into `streaming.py` — inject `_bfull_retrieve(message_text)` into the context prompt before each WebUI turn (same helper, imported from the agent package). Gate it as a core/webui patch; until then, WebUI sessions must lean harder on explicit `knowledge.py search` tool calls.

Consequences:
  - The old hard rule "an offloaded fact with no hot pointer is effectively forgotten" is now SOFT. The binding constraint is **retrievability at the 0.80 floor**, not pointer coverage. A fact that probes ≥0.80 on realistic query phrasings can be fully trimmed from MEMORY.md with no pointer.
  - **Probe-then-trim protocol (the aggressive-offload procedure):** per hot fact: (1) write it into the topical reference doc under a real heading and `contextualize-file` it; (2) probe with 2–3 realistic query phrasings via `python3 ~/.hermes/scripts/offload_probe.py` or manual `knowledge.py search`; (3) ALL phrasings pass → delete the hot line entirely; ANY marginal → keep a one-line cue. Never trim blind. See `memory-discipline` skill for the probe's auto-query + verdict logic (hardened 2026-06-24).
    `offload_probe.py` usage (built 2026-06-11, probe+verdict hardened 2026-06-24): `probe --fact "..." [--queries q1 q2 q3]` (omitting --queries auto-generates 3 deterministic phrasings — **Q1:** 6 highest-information tokens, **Q2:** 5 most distinctive content tokens by length (was: literal identifiers — failed for behavioral facts; fixed 2026-06-24), **Q3:** first 8 content words) · `scan` (parses MEMORY.md on `§` separators, skips pointer-style entries containing "knowledge.py", reports TRIM-SAFE/POINTER/KEEP-HOT per entry + projected % after trim, reads the live cap from config.yaml) · `--json`. READ-ONLY against both stores. Verdict uses both cosine score AND `fact_found` (token overlap ≥60% at ≥0.60) — see `memory-discipline` skill for the full design rationale.
    **API fact that bit during the build:** `knowledge.search()` result dicts carry the relevance score in the **`score`** field (plus `id`, `text`, `tags`, `priority`, `source`, `stored_at`, `context_prefix`) — NOT `_relevance_score`. Anything post-processing search results must read `score`.
  - **Store query-shaped:** include literal identifiers (config keys, ports, hostnames) AND aliases in the stored text — v3.0's adaptive BM25 weighting rewards exact tokens. Set `KNOWLEDGE_PRIORITY=high` on offloaded hot facts; run `build-graph` after each doc pass.
  - **B-lite remains the FALLBACK doctrine** whenever B-full is absent (fresh install, or after a `hermes update` reverted the core patch): on infra/config/topology-topical turns, run `knowledge.py search` before answering, trust only hits ≥0.80. The pointer model below still applies in that state.
  For the original investigation (why a clean hook can't do per-turn injection without a core patch, latency/relevance findings, B-lite vs B-full decision), see `references/auto-retrieval-architecture.md` + `scripts/auto_retrieve_proto.py`.

**B-lite — the FALLBACK TRIGGER (applies when B-full is absent — see live-check above).** When BOTH hold:
  1. the current turn is clearly **infra / memory / config / device / topology-topical**, AND
  2. the answer could depend on a **stored fact** (a host spec, port, model string, past decision, tool quirk, device map),
then **run `knowledge.py search "<topic>"` BEFORE answering** — don't answer from assumption first. Reading the result is the cue-driven retrieval the pointer model relies on; a topical turn IS a cue, even with no hand-written pointer.

- **The floor is judgment, NOT a flag.** `knowledge.py search` has no `--floor` arg — it prints top-K hits with their scores (`[0.86] …`). Apply the floor yourself: **trust only hits scoring ≥ 0.80** as authoritative cold facts. Treat 0.80 as the precision line proven by the proto — below it the embedding space is dense noise (junk queries score ~0.67), not signal.
- **Silent when nothing clears 0.80.** If no hit reaches the floor, drop retrieval and answer normally. Do not surface or lean on sub-floor hits — that's the noise B-full would inject and B-lite deliberately filters at the reasoning step.
- **Why this and not B-full:** ~90% of auto-RAG's value (cold facts surface without depending on a perfect breadcrumb) at none of the cost — no core patch, no per-turn latency tax, no context noise, no per-turn token tax. Paid once as tool output on the minority of topical turns, read-and-filtered, never re-injected.
- **Reliability honesty:** B-lite's one failure mode is *silent omission* — the agent answers without firing the search on a soft-topical turn. Three reinforcing layers cover that gap: (1) hot-tier POINTERS cue the lookup, (2) the **skill-review checkpoint** nudges loading this skill on complex sessions, (3) this trigger doctrine. Any one firing catches the fact. If Andrew ever says "what happened to X?" or "I told you this," a topical turn slipped the trigger — fix the reflex, and log it as a crossover signal (see below).
- **Crossover watch (when B-full's gated core patch finally earns its fragility):** NOT row count — it's *pointer-coverage decay*. Watch three signals: rising **reteach rate** ("what happened to X?"), climbing **orphan ratio** (cold facts with no hot pointer — instrumented in `infra_watchdog`), and a dirtying **noise floor** (re-run `auto_retrieve_proto.py`; junk queries clearing 0.80). B-full is justified only at ~800–1,500 rows AND with the cross-encoder reranker (Item 5) already in place AND recurring reteach events — until then, the cheaper fix is always Stage 2 (pointer-on-every-offload, in `memory-discipline`).

**Orphan-ratio instrument (built 2026-06-09; `orphan_ratio.py` DELETED 2026-10-16 — LanceDB-era, no caller).** The crossover signal above is now measurable, not guessed. `~/.hermes/scripts/orphan_ratio.py` counts cold rows with NO hot-tier cue (orphan facts): `orphan_ratio.py` (human report) · `--json` · `--quiet` (just the float). Key design points future agents must know:
  - **Reads LanceDB DIRECTLY (`lancedb.connect`), not via `knowledge.py`** — so it does NOT trigger the ~2s embedding-model load. This is what makes it cheap enough to run from the 15-min `infra_watchdog` (§8). Importing `knowledge.py` for a count-only job is the trap; the orphan check needs the table, not embeddings.
  - **A row is "cued" (not orphan) if ANY of:** its 8-char id is in MEMORY.md, its `source` basename is named in MEMORY.md, or ≥2 of its salient terms appear in MEMORY.md (a plain hot mention or a `knowledge.py search "<q>"` pointer query both count).
  - **Excludes frozen-history rows by `source` pattern** (`state`, `snapshot`, `changelog`, `user-model`, `peer-card`, `session`, `-archive`, `backup`) — those are never meant to be cue-retrieved, so scoring them as orphans inflates the number meaninglessly. The baseline is intentionally noisy (reference-doc chunks still count) — do NOT over-tune the filter chasing a prettier number; a stable-but-noisy baseline still detects the TREND, which is all the watchdog needs.
  - **Baseline lives in `~/.hermes/references/orphan-ratio-baseline.json`** — ALWAYS read the file, never trust a number written here (initial 2026-06-09 recording was 50.3% (76/151); re-baselined same day to 25.0% (12/48) after a prune+pointer pass).

**Correcting a stale row = DELETE + RE-STORE, never in-place text edit.** Editing only the `text` column leaves the embedding vector pointing at the OLD content — retrieval keeps matching the dead premise. Procedure (proven 2026-06-09): (1) read the old row, note its priority/tags and anything still true to carry over; (2) `tbl.delete('id IN ("<id>")')`; (3) re-store the corrected text via `scripts/knowledge.py store` (env `KNOWLEDGE_TAGS`, `KNOWLEDGE_PRIORITY`) so it re-embeds; (4) verify with a retrieval probe — corrected fact should be the top hit ≥0.80; (5) re-run the dead-term scan to confirm zero stale current-state claims remain. For 1.000-similarity same-source duplicates: read BOTH rows first, delete the chunk from the OLDER doc-version ingest, keep the newer. `infra_watchdog` §8 alerts only on **≥15pt RISE above baseline** (a trend-delta alarm, see general pattern below), never an absolute threshold — and is wrapped so a probe failure can't break the watchdog chain.
