# LanceDB → Supabase Migration (v3.0, 2026-06)

## What happened
The knowledge store backend was migrated from local LanceDB (`~/.hermes/knowledge_db/`) to Supabase pgvector. The migration happened in the `knowledge.py` code but the `knowledge-store` SKILL.md and `memory-discipline` SKILL.md were not fully updated — they retained ~50+ LanceDB references in operational instructions (query LanceDB, store to LanceDB, assumes LanceDB is trustworthy).

## Current state (2026-06-25)
- **Live backend:** Supabase pgvector — `knowledge.py` uses Supabase exclusively
- **LanceDB directory:** `~/.hermes/knowledge_db/` no longer exists
- **Skills:** Patched to remove operational LanceDB references. Historical "proven" sections still reference LanceDB (these are records of past sessions, not current instructions) — the migration banner at the top of the skill body explains this.
- **Warm daemon:** `hermes-kb-daemon` holds the embedding model warm (not LanceDB connections). The Supabase backend means the daemon caches the model, not the DB connection.
- **Code:** `knowledge.py` imports `lancedb` 3 times — all in comments/docs explaining the migration, not in active code paths.

## Verification
```bash
# Confirm live backend is Supabase
grep -c 'SUPABASE' ~/.hermes/scripts/knowledge.py  # → 20
grep -c 'lancedb' ~/.hermes/scripts/knowledge.py    # → 3 (all in comments)

# Confirm LanceDB directory is gone
ls ~/.hermes/knowledge_db/  # → No such file or directory

# Supabase project
python3 -c "import os; import sys; sys.path.insert(0,os.path.expanduser('~/.hermes/scripts')); from knowledge import get_supabase; u,k = get_supabase(); print(u)"
# → https://lmxjngzjpfcvakpqonxs.supabase.co
```

## Why the skill was stale
The v3.0 code migration ran, the backend switched, but no systematic skill-audit followed. The `knowledge-store` SKILL.md was ~16,000 words of accumulated session notes — many referencing the old architecture. The lesson: after any backend migration, audit ALL skills that reference the old backend, not just the code.
