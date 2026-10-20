# Supabase Knowledge Store Hygiene

## Audit Query

```sql
-- Identify garbage and organizational debt
SELECT
  COUNT(*) as total,
  COUNT(*) FILTER (WHERE text IS NULL OR text = '') as empty_text,
  COUNT(*) FILTER (WHERE sector_path IS NULL OR sector_path = '[]') as no_sector,
  COUNT(*) FILTER (WHERE source = '' OR source IS NULL) as no_source,
  COUNT(*) FILTER (WHERE text LIKE '%--text%' OR text LIKE '%--tags%') as cli_garbage
FROM knowledge;
```

## Common Garbage Patterns

| Pattern | Root Cause | Fix |
|---|---|---|
| `--text Sentry alert in : (error) --tags sentry,alert...` | CLI arg string stored as literal text; broken `subprocess.run` quoting in webhook | Remove `knowledge.py store` from webhook handlers; delete rows |
| Empty `source` | Facts stored without source attribution | Always pass `--source` to `knowledge.py store` |
| Empty `sector_path` | Facts stored before classification existed | Batch classify via `_detect_query_sector()` |

## Garbage Deletion

```sql
-- CLI garbage (raw cli args in text column)
DELETE FROM knowledge WHERE text LIKE '%--text%' OR text LIKE '%--tags%';

-- Source-specific garbage
DELETE FROM knowledge WHERE source IN ('sentry-webhook', 'linear-webhook');
```

## Webhook Hygiene Rule

Webhooks should NOT write to Supabase directly. The knowledge store is for durable institutional knowledge, not transient alerts. Sentry alerts → Linear → Kanban. Linear issues → Kanban. Neither writes to Supabase.

If a webhook handler calls `subprocess.run([..., "knowledge.py", "store", ...])` — remove it.

## Batch Classification

When facts exist with empty `sector_path`, batch-classify them using the same keyword rules that `search()` uses:

```python
# ~/scripts/classify_facts.py — imports _detect_query_sector from knowledge.py
sys.path.insert(0, os.path.expanduser('~/.hermes/scripts'))
from knowledge import _detect_query_sector, get_supabase

SUPABASE_URL, SUPABASE_SERVICE_KEY = get_supabase()

# Fetch facts, classify each, PATCH sector_path via Supabase REST
for row in supabase_query("knowledge?select=id,text&order=stored_at.desc&limit=300"):
    sector = _detect_query_sector(row["text"])
    if sector:
        supabase_query(f"knowledge?id=eq.{row['id']}", method="PATCH", data={"sector_path": sector})
```

Run via the hermes-agent venv so imports resolve: `/usr/local/lib/hermes-agent/venv/bin/python3 /tmp/classify_facts.py`

The classifier (`_detect_query_sector`) uses keyword rules — infrastructure (deploy/config/networking/monitor), development (coder-fleet/webui/knowledge/graph), monitoring (sentry/linear/webhooks), home-automation, user preferences. Facts that match no keywords remain `[]` (unclassified, neutral 1.0× Fibonacci weight). This is expected — behavioral rules, test data, and edge-case facts don't fit the keyword taxonomy.

## Idempotency for Kanban Dispatch

Webhook handlers that create Kanban cards via raw SQL must check for duplicates:

```python
idempotency_key = f"linear-{issue_id}"

# Check before INSERT
existing = conn.execute(
    "SELECT id FROM tasks WHERE idempotency_key = ? AND status NOT IN ('done','archived')",
    (idempotency_key,)
).fetchone()

if existing:
    return {"status": "duplicate", "existing_id": existing[0]}

# Insert with idempotency_key
conn.execute(
    "INSERT INTO tasks (..., idempotency_key) VALUES (..., ?)",
    (..., idempotency_key)
)
```
