# Metadata Filtering Architecture (v3.4)

**Date:** 2026-06-25
**Status:** In progress — column and RPC deployed, backfill pending

## Overview

Additive retrieval layer on top of Supabase pgvector. Structured metadata is extracted from facts at store time and from queries at search time. The `match_knowledge_v2` RPC filters facts by metadata BEFORE the vector search, reducing the candidate pool and improving precision.

## Schema

```sql
ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS metadata jsonb;
CREATE INDEX IF NOT EXISTS idx_knowledge_metadata ON knowledge USING gin (metadata);
```

Example metadata:
```json
{
  "hosts": ["VPS-A", "<VPS-A-ip>"],
  "ports": [5051],
  "services": ["nginx"],
  "file_paths": ["~/wall-dash/"],
  "systems": ["wall-dashboard"]
}
```

## RPC Integration

The `match_knowledge_v2` function accepts an optional `metadata_filter jsonb` parameter. 

**⚠️ HARD SQL FILTER HURTS RECALL (proven 2026-06-25).** Using `@>` containment as a WHERE clause filters rows BEFORE the vector search, which at current KB scale (~150 facts) silently drops relevant results. The metadata filter should be a SCORING BOOST, not a hard filter. Currently disabled in `knowledge.py search()` — the `_extract_query_metadata()` and `_meta_matches_any()` infrastructure remain deployed and ready to re-enable as a boost when the KB reaches >500 facts.

**Helper function: `_meta_matches_any(meta jsonb, filter jsonb)`** — OR semantics with case-insensitive, hyphen-normalized LIKE matching. Handles scalars and arrays uniformly:

```sql
CREATE OR REPLACE FUNCTION _meta_matches_any(meta jsonb, filter jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT meta IS NOT NULL AND filter IS NOT NULL AND EXISTS (
    SELECT 1 FROM jsonb_each_text(filter) AS f(fkey, fval)
    WHERE meta ? fkey
    AND EXISTS (
      SELECT 1 FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(meta->fkey) = 'array' 
             THEN meta->fkey 
             ELSE jsonb_build_array(meta->fkey) 
        END
      ) mv
      WHERE EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(
          CASE WHEN jsonb_typeof(fval::jsonb) = 'array'
               THEN fval::jsonb
               ELSE jsonb_build_array(fval::jsonb)
          END
        ) jv
        WHERE replace(lower(mv), '-', ' ') LIKE '%' || replace(lower(jv), '-', ' ') || '%'
      )
    )
  )
$$;
```

**Key design decisions in the helper:**
- **LIKE, not IN**: `@>` requires exact array-element match; `IN` requires exact string match. `replace(lower(...), '-', ' ') LIKE '%value%'` handles substring matches across hyphen/space variations (e.g. "lh02" matches "localadmins-LH02.local").
- **OR, not AND**: `jsonb @> filter` requires ALL filter keys to match. `_meta_matches_any` matches if ANY key has an overlapping value — one matching system is enough to include a fact.
- **Scalar-safe**: `jsonb_array_elements_text()` crashes on scalar values. The `CASE jsonb_typeof() = 'array'` guard wraps scalars in `jsonb_build_array()`.
- **Case-insensitive + hyphen-normalized**: `replace(lower(mv), '-', ' ')` normalizes both stored metadata values and filter values before comparison. Handles "Dispatcher" vs "dispatcher" and "wall-dashboard" vs "wall dashboard".

## Query-Side Extraction

`_extract_query_metadata()` in `knowledge.py` uses fast regex patterns to extract metadata from user queries. No LLM needed — sub-millisecond.

**Normalization:** `val = m.group(1).lower(); val = '-'.join(val.split())` — lowercases, then collapses whitespace into single hyphens. Example: "LH02" → "lh02", "wall dashboard" → "wall-dashboard".

Regex patterns cover:
- Hostnames: `VPS-A`, `VPS-B`, `LH01`, `LH02`, IP addresses, space-tolerant variants (`wall\s+dashboard`) (real hostname list: env var KB_HOST_NAME_PATTERN)
- Ports: `port 5051`, `:8787`
- Services: `nginx`, `docker`, `uvicorn`, `home-assistant`, `ollama`, `neo4j`, `supabase`, etc.
- Systems: `wall-dashboard`, `ha-dashboard`, `kanban`, `dispatcher`, `linear`, `sentry`, etc. (compound terms only — bare `dashboard` removed: too greedy)

## Store-Side Extraction

qwen2.5-32b on LH02 (llama.cpp `:8080`) with a 3-line system prompt. Produces valid JSON in ~2 seconds per fact.

System prompt:
```
Extract structured metadata from facts about infrastructure. Return ONLY valid JSON, 
no other text. Fields: hosts (array of hostnames/IPs), ports (array of integers), 
services (array), file_paths (array), systems (array of system/domain names this fact 
is ABOUT: e.g. wall-dashboard not VPS-A, kanban not LH01). Use null for missing.
```

Example curl:
```bash
curl -s http://<LH02-tailnet-ip>:8080/v1/chat/completions \  # (real address: memory_search "Tailnet")
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5-32b",
    "messages": [
      {"role": "system", "content": "Extract structured metadata..."},
      {"role": "user", "content": "Wall dashboard at VPS-A..."}
    ],
    "temperature": 0,
    "max_tokens": 200
  }'
```

## Enrichment Architecture

Three layers, zero manual intervention:

1. **New facts — kanban card**
   `knowledge.py store()` creates a kanban card → dispatcher picks it up → worker calls Studio → updates Supabase metadata column. ~60 seconds from store to enriched.

2. **Delta — hourly cron**
   Scans for facts where `metadata_updated_at < text_updated_at`. Enriches changed facts. ~6 seconds when idle. Catches direct Supabase edits and failed kanban runs.

3. **Full sweep — daily 2 AM cron**
   Re-extracts metadata from all facts. ~4 minutes for 135 facts. Catches prompt drift, model changes, schema evolution.

## Ranking Weight Fix

The importance and recency weights in `_single_search()` were overpowering cosine similarity:

Before:
```python
score *= (0.7 + 0.3 * _IMPORTANCE.get(priority, 0.5))  # multiplier
score += math.exp(-age_days / 14.0) * 0.10              # up to +0.10
```

After:
```python
score += (0.04 - order.get(priority, 2) * 0.01)         # +0.01 to +0.04
score += math.exp(-age_days / 14.0) * 0.02              # up to +0.02
```

This prevents stale high-priority Sentry alerts from dominating every search.

## KB Cleanup (2026-06-25)

Before the metadata layer was added, a cleanup pass removed:
- 222 duplicate rows (63% of KB) — some facts duplicated 41x–54x
- CLI flag leakage from 43 facts (`--text`, `--priority`, `--tags` in stored text)
- Stale counts: "161 rows" → "353 rows", "6 rows" → "353 rows"

Result: 353 → 135 clean facts, 3/5 → 5/5 retrieval relevance.

## LH02 Role

LH02 runs two services over Tailscale:

| Service | Port | Model | Use |
|---|---|---|---|
| Ollama | `:11434` | `nomic-embed-text` (0.3GB) | Embedding for `knowledge.py` (via `EMBEDDING_API_URL`) |
| llama.cpp | `:8080` | `qwen2.5-32b-64k` (19.9GB) | Metadata extraction + LLM inference (`custom:lh02-llama` provider) |
| llama.cpp | `:8080` | `qwen2.5:72b` (47.4GB) | Available but unused in enrichment pipeline |
