# Knowledge Store & Retrieval — Master Architecture
>
> **Date:** 2026-06-25  
> **Audience:** Fresh Hermes agent session. Read before touching anything.

---

## Stack

| Layer | Technology | Location |
|---|---|---|
| Vector store | Supabase pgvector | `lmxjngzjpfcvakpqonxs` (us-east-2) |
| Graph mirror | Neo4j Aura | `fce34ad7` at `c78e1cb1.databases.neo4j.io` |
| Full-text | PostgreSQL tsvector | Same Supabase DB (`public.knowledge.tsv`) |
| Retrieval engine | `scripts/knowledge/` package | `~/.hermes/scripts/knowledge/` |
| Classification | `_detect_query_sector()` | Keyword-based, QUERY-tuned, 200-char guard |

## Table: `public.knowledge`

| Column | Type | Purpose |
|---|---|---|
| `id` | text (PK) | 8-char hex UUID |
| `text` | text | Fact body |
| `vector` | vector(2560) | Qwen3-Embedding-4B |
| `tags` | jsonb | e.g. `["infrastructure", "offload"]` |
| `priority` | text | `critical` / `high` / `normal` / `low` |
| `source` | text | Origin: `github-webhook`, `memory-compaction`, etc |
| `stored_at` | timestamptz | Insert time |
| `sector_path` | jsonb | `["domain","capability","process"]` or `[]` |
| `metadata` | jsonb | Extracted by qwen-32b on Mac Studio |
| `tsv` | tsvector | PostgreSQL full-text search index |

## Retrieval Architecture

### Search pipeline (per `search()` in knowledge.py)

```
1. Embed query → 2560-dim vector
2. pgvector cosine search (pool_size=100 or sector-filtered subset)
3. PostgreSQL FTS (websearch_to_tsquery) — parallel
4. RRF merge (k=20): weighted reciprocal rank fusion of vector + text results
5. IF SECTOR_BOOST=true: Fibonacci same-sector multiplier (×3.70)
6. IF GRAPH_TRAVERSE=true: Neo4j confirmatory boost
7. Confidence scoring + metadata enrichment
8. Return top K results
```

### Feature flags (set in systemd drop-ins)

| Flag | Effect | Benchmark | Status |
|---|---|---|---|
| `SECTOR_BOOST` | Same-sector ×3.70 multiplier | 100% P@5 | **ON** |
| `GRAPH_TRAVERSE` | Neo4j confirmatory boost | +stability | **ON** |
| `SECTOR_FILTER` | Exclude non-matching sectors BEFORE search | 87% P@5 | **OFF** |
| `LAYERED_RETRIEVAL` | Filter then expand | 80% P@5 | **OFF** |
| `GRAPH_EXPAND` | Neo4j graph expansion | Pushes correct to rank 11+ | **OFF** |
| `RERANK` | Cross-encoder rerank | Untested | **OFF** |

## Fibonacci Sector System

### How it works

1. Query arrives → `_detect_query_sector(query)` returns e.g. `["infrastructure", "hosting", "deploy"]`
2. Each fact in results has `sector_path` (set by webhooks at store time)
3. Same-sector facts: score × (8+5+3)*0.15 = **3.70× boost**
4. Unclassified facts (sector_path=[]): **1.0× neutral**
5. Different-sector facts: **1.0× neutral**

### Sector taxonomy (14 sectors)

See `fibonacci-sector-retrieval` skill for full keyword table.

### Temporal decay

| Sector | TTL | Rationale |
|---|---|---|
| `monitoring/sentry/alert` | 3 days | Alerts stale fast |
| `monitoring/linear/issue` | 7 days | Issues age weekly |
| `infrastructure/hosting/deploy` | 30 days | Infra changes slowly |
| Default (unclassified) | 14 days | Neutral |

### CRITICAL RULE: How sectors get set

- **QUERIES:** `_detect_query_sector(query)` at search time. Correct. Works. Do not touch.
- **FACTS:** Webhooks set `sector_path` at store time. Do NOT run `_detect_query_sector()` on fact text.
- **Guard:** `_detect_query_sector()` rejects input >200 chars with ValueError. Queries are 5-80 chars. Facts are 100-500. The guard crashes bulk-classification attempts.

### Benchmark history

| Date | State | P@5 | Cause |
|---|---|---|---|
| 2026-06-24 | Webhook sectors + SECTOR_BOOST | **100%** | Original working state |
| 2026-06-25 | Flat (sectors cleared) | **73.3%** | After clearing corrupted keyword-inferred sectors |
| 2026-06-25 | Keyword bulk-classified | **60%** | Mistake 1: bulk over-wrote correct sectors |
| 2026-06-25 | Keyword bulk-classified (retry) | **67%** | Mistake 2: same error, same result |
| Future target | Organic re-accumulation | **→100%** | Webhooks set correct sectors over days/weeks |

## Webhook Pipeline

### Sentry → Kanban (as of 2026-06-25)

```
Sentry webhook (POST /api/sentry)
  → HMAC verify (SENTRY_WEBHOOK_SECRET)
  → sentry_messages.json (dashboard channel)
  → Linear API (issueCreate, only for action="created")
    → Linear webhook (POST /api/linear)
      → HMAC verify (LINEAR_WEBHOOK_SECRET)
      → Telegram notify
      → Kanban card (SQL INSERT, idempotency_key="linear-{issue_id}")
      → NO Supabase write (removed 2026-06-25)
```

### GitHub / Figma / Honcho → Kanban

Each webhook stores to `knowledge.py` (tags webhook-specific), creates Kanban card for actionable events. Supabase writes are intentional for these — they're durable reference facts.

### Webhook files

- `~/hermes-dispatcher/routes/hooks.py` — all webhook handlers
- `~/hermes-dispatcher/routes/sentry.py` — Sentry messages API endpoint
- `~/hermes-dispatcher/data/sentry_messages.json` — dashboard data source

## Cron System

### Configuration

| Key | Value | Location |
|---|---|---|
| `approvals.cron_mode` | `allow` | config.yaml line 1407 |
| `cron.provider` | `custom:mac-studio-llama` | config.yaml line 1473 |
| `cron.chronos.portal_url` | `https://portal.nousresearch.com` | config.yaml |

### Scheduler

The cron scheduler runs as part of `hermes-gateway.service`. It ticks every 60 seconds and dispatches jobs whose schedule matches. 32 jobs configured in `~/.hermes/cron/jobs.json`.

### Key jobs

| ID | Name | Schedule | Type | Priority |
|---|---|---|---|---|

### Memory offload flow

Agent-driven, no script (since 2026-10-16, step 7.3): at ≥80% the agent `memory_add`s entries verbatim to Supabase, verifies each with `memory_search`, then trims the store to ≤75%. The in-band memory checkpoint hook is gone; the only out-of-band alarm is `infra_watchdog.py` (≥92%).

## Dispatcher Service

The dispatcher is NOT a separate service. It runs as an ExecStart override on `hermes-dashboard.service`:

```
/etc/systemd/system/hermes-dashboard.service.d/dispatcher-override.conf
  → ~/hermes-dispatcher/.venv/bin/uvicorn server:app --port 8787
```

- **Code:** `~/hermes-dispatcher/routes/hooks.py`
- **Env:** `~/hermes-dispatcher/.env`
- **Restart:** `systemctl restart hermes-dashboard`
- **No** `hermes-dispatcher` unit exists — never has

## Supabase Operations

### How to query

```sql
-- Classification coverage
SELECT CASE WHEN sector_path IS NULL OR sector_path = '[]' THEN 'unclassified' ELSE 'classified' END, COUNT(*) FROM knowledge GROUP BY 1;

-- CLI garbage check
SELECT COUNT(*) FROM knowledge WHERE text LIKE '%--text%' OR text LIKE '%--tags%';

-- Source distribution
SELECT source, COUNT(*) FROM knowledge GROUP BY source ORDER BY cnt DESC;
```

### Disaster recovery

If sector_paths are corrupted by bulk classification:
1. `UPDATE knowledge SET sector_path = '[]'::jsonb` — clear everything
2. `kbench.py eval` — confirm flat baseline ≥70% P@5
3. Wait — webhooks accumulate correct sectors over days/weeks
4. Re-benchmark — target return to 100% P@5

### Common pitfalls

- **Source-based DELETE misses garbage:** CLI garbage has empty `source` field because CLI args leak into `text` column, not `source`.
- **Column is `stored_at`, not `created_at`:** The Supabase ORM uses `stored_at` for timestamps.
- **Table is `knowledge`, not `documents`:** Earlier migrations used different names.

## Files Reference

| File | Purpose |
|---|---|
| `~/.hermes/scripts/knowledge/` | Retrieval engine package (search.py, storage.py, eval.py) |
| `~/.hermes/cron/jobs.json` | All 32 cron job configurations |
| `~/hermes-dispatcher/routes/hooks.py` | All 6 webhook handlers |
| `~/hermes-dispatcher/.env` | Webhook secrets, API keys |
| `~/.hermes/config.yaml` | Full Hermes configuration |
| `~/.hermes/memories/MEMORY.md` | Agent memory (3,000 char cap) |
| `~/.hermes/memories/USER.md` | User profile (2,250 char cap) |

## Skills

| Skill | Content |
|---|---|
| `fibonacci-sector-retrieval` | Sector system, benchmarks, pitfalls, recovery |
| `knowledge-store` | Supabase operations, store/search API |
| `memory-discipline` | Compaction, offload, memory hygiene |
