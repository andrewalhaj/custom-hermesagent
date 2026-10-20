# Supabase RPC Function Modification — Pitfalls

**Date:** 2026-06-25
**Context:** Modifying `match_knowledge_v2` to add `metadata_filter jsonb` parameter

## The Cycle

Four errors across ~15 attempts before the function worked:

### 1. Function Overloads (404: Not Found)

`CREATE OR REPLACE` with different parameter TYPES creates a NEW overload — it does NOT replace the old one. The old function survives as a separate overload with the same name. The REST API can't resolve which one to call → 404.

Check: `SELECT proname, pronargs, proargtypes::regtype[] FROM pg_proc WHERE proname = 'match_knowledge_v2';`

Fix: `DROP FUNCTION IF EXISTS match_knowledge_v2(vector, text, integer, text[], text);` — match the EXACT old signature, then `CREATE OR REPLACE`.

### 2. Parameter Name Mismatch (404: Not Found)

Supabase REST API resolves function parameters by NAME, not position. If the function expects `"query_embedding"` but you send `{"q_emb": ...}`, the parameter won't bind → 404.

The knowledge.py `_supa_post` sends: `query_embedding`, `query_text`, `match_count`, `filter_tags`, `min_priority`, `metadata_filter`.

Fix: Use QUOTED parameter names in the function definition that exactly match what the REST client sends:
```sql
CREATE OR REPLACE FUNCTION match_knowledge_v2(
  "query_embedding" vector(768), "query_text" text DEFAULT '',
  "match_count" int DEFAULT 10, "filter_tags" jsonb DEFAULT NULL,
  "min_priority" text DEFAULT NULL, "metadata_filter" jsonb DEFAULT NULL
)
```

### 3. Column Ambiguity (400: Bad Request — "column reference is ambiguous")

Postgres can't resolve `id` when both a function parameter and a table column share the name. Even WITHIN a function body, return-column names can conflict with local variables.

Fix: Use ALIAS for parameters, distinct names for all internal variables, and explicit aliases in RETURN QUERY:
```sql
DECLARE
  qv ALIAS FOR "query_embedding";
  rec record; vr int;
BEGIN
  ...
  RETURN QUERY SELECT r.rid AS "id", r.ft AS "text", ...
END;
```

### 4. Type Mismatch: jsonb vs text[] (400: "operator does not exist: jsonb && text[]")

Supabase stores array columns (`tags`, `metadata`) as `jsonb`, not `text[]`. The `&&` operator doesn't exist for `jsonb`.

Fix: Use `@>` (containment) instead of `&&` (overlap):
```sql
-- Wrong: k.tags && filter_tags   (not valid for jsonb)
-- Right: k.tags @> filter_tags   (jsonb containment)
```

ALL filter/array parameters should be `jsonb` type, not `text[]`.

### 5. Legacy Content Filtering (400: "ANY syntax error")

Postgres doesn't support `= ANY(...)` or `CASE ... WHEN ... THEN ANY(...)` syntax — it's only valid in WHERE clauses.

Fix: Use explicit `IN` in WHERE clause directly:
```sql
AND (mp IS NULL OR k.priority = mp
     OR (mp='high' AND k.priority IN ('critical','high'))
     OR (mp='normal' AND k.priority IN ('critical','high','normal')))
```

## Debugging Checklist

When modifying a Supabase RPC function:

1. **List overloads first:** `SELECT proname, pronargs, proargtypes::regtype[] FROM pg_proc WHERE proname = '<name>';`
2. **Drop exact old signatures** before CREATE OR REPLACE with new types
3. **Match parameter names** to what the REST client sends (check the Python payload dict)
4. **Use quoted identifiers** for parameter names: `"query_embedding"`
5. **Use ALIAS for short internal names** to avoid ambiguity
6. **Test with a direct curl/urllib call** from Python before testing through knowledge.py
7. **Check column types in Supabase** — `information_schema.columns` — before writing operators (jsonb vs text[])
8. **Reload PostgREST schema cache after every function change:** `NOTIFY pgrst, 'reload schema'`. Without this, the REST API may return 400/404 because it's using a stale cached signature. This is the #1 cause of "it works in SQL but not via REST."

### 6. jsonb_array_elements_text Fails on Scalars (400: "cannot extract elements from a scalar")

`jsonb_array_elements_text(meta->'systems')` crashes if the value is a scalar string `"wall-dashboard"` instead of an array `["wall-dashboard"]`. The LLM backfill produces both formats depending on whether there's one value or multiple.

Fix: Wrap in a CASE that checks `jsonb_typeof()`:
```sql
CASE WHEN jsonb_typeof(meta->fkey) = 'array' 
     THEN meta->fkey 
     ELSE jsonb_build_array(meta->fkey) 
END
```

### 7. Outer Query Values Not Visible in Nested EXISTS (42703: "column f.val does not exist")

`jsonb_each_text(filter) AS f(fkey, fval)` makes `f.val` available in the outer query, but NOT in a nested `EXISTS (SELECT ... FROM ... WHERE ... f.val ...)`. PostgreSQL scoping limits prevent deeply nested references to lateral-join outputs.

Fix: Use `fval::jsonb` or extract values into a CTE before the nested EXISTS, or use `IN (SELECT ...)` with a correlated reference only one level deep.

### 8. Temp Table Column vs Variable Ambiguity (42702: "column reference fr is ambiguous")

A temp table with a column `fr` conflicts with a PL/pgSQL `DECLARE fr int;` variable in statements like `UPDATE _rp SET fr=fr`. PostgreSQL can't tell which `fr` is the column and which is the variable.

Fix A (preferred): Use CTEs instead of temp tables — no variable/column collision possible.
Fix B: Qualify with `_rp.fr = _fr` (aliased variable) — fragile under refactors.

### 9. ORDER BY References Unaliased Column

`SELECT row_number() OVER (ORDER BY ts_rank(...) DESC) AS _rn ... ORDER BY ts_rank DESC` — `ts_rank` is a function call in the OVER clause, not a selected column. The bare `ORDER BY ts_rank DESC` at the CTE level references nothing.

Fix: `ORDER BY _rn` (reference the aliased output column) or repeat the full expression.
