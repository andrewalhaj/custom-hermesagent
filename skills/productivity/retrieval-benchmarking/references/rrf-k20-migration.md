# RRF k=20 Migration (2026-06-25)

## Problem

`match_knowledge_v2` used RRF with k=60. At 152-fact scale:
- Rank 1 score = 1/(60+1) = 0.01639
- Rank 50 score = 1/(60+50) = 0.00909
- Ratio = 1.8× — effectively zero discrimination

All results looked identical. Generic high-priority facts always won because there was no score separation.

## Migration

### Supabase RPC

```sql
-- Key change: 60.0 → 20.0 in the merged CTE
merged AS (
    SELECT ..., (1.0 / (20.0 + _rn::double precision)) _rrf_score, ... FROM vector_arm
    UNION ALL
    SELECT ..., (1.0 / (20.0 + _rn::double precision)) _rrf_score, ... FROM fts_arm
)
```

Full migration: `match_knowledge_v3_rrf_k20` → creates `match_knowledge_v3` function. Later superseded by `match_knowledge_v4` (adds `sector_filter` parameter).

### Knowledge.py Companion Change

`_rrf_merge()` client-side RRF merge: `1.0 / (60 + rank)` → `1.0 / (20 + rank)`.

### Schema Cache

After EVERY RPC function change:
```sql
NOTIFY pgrst, 'reload schema';
```

Without this, PostgREST returns 400/404 because it uses a stale cached function signature.

## Results

| Metric | k=60 | k=20 |
|---|---|---|
| Rank 1 score | 0.0164 | 0.0476 |
| Rank 50 score | 0.0091 | 0.0143 |
| Ratio | 1.8× | 3.3× |
| P@5 | 100% | 100% (no regression) |

## Why k=20 not k=10?

k=10 would give 5.4× discrimination but risks FTS arm dominating. A mediocre FTS match at rank 3 (0.077) would beat a good vector match at rank 10 (0.050). k=20 balances both arms.
