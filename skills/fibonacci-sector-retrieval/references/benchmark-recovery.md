# Benchmark Recovery — 2026-06-25

## What happened

1. Bulk-classified 334 facts via `_detect_query_sector()` — keyword classifier tuned for queries
2. Overwrote correct webhook-set sector_paths with keyword-inferred noise
3. P@5 dropped: **100% → 60%** (Mistake 1)
4. Cleared sectors, re-classified: **73% → 67%** (Mistake 2 — same error)
5. Cleared sectors permanently, installed 200-char guard on `_detect_query_sector()`

## Current state (2026-06-25 18:00 EDT)

| Metric | Value |
|---|---|
| P@5 | 73.3% (flat, no sectors) |
| Total facts | 334 |
| Sector paths | All `[]` (neutral 1.0×) |
| SECTOR_BOOST | Enabled but nooping |
| GRAPH_TRAVERSE | Enabled |
| Cron | Running (built-in ticker; memory offload removed 2026-07-01) |
| Memory Offload (removed) | — |

## Recovery path

1. **Do nothing to sectors.** Webhooks accumulate correct `sector_path` at store time.
2. `prefetch()` automatically handles cold-store injection — webhooks will classify them.
3. Benchmark weekly with `scripts/knowledge/eval.py` — expect:
   - Week 1: ~80% P@5 (some facts classified)
   - Week 2: ~90% P@5 (most classified)
   - Week 3+: ~100% P@5 (full recovery)

## Guard installed

```python
def _detect_query_sector(query):
    if len(query) > 200:
        raise ValueError(
            "Facts get sectors from webhooks, not keyword inference. "
            "Bulk-classifying fact text destroyed retrieval accuracy."
        )
```

Bulk classification now crashes instead of silently polluting. Query classification (5-80 chars) is unaffected.
