# Provenance Pipeline — LEARNED_IN Edges

## Architecture (2026-06-25)

Every Honcho session_summary now creates a provenance chain in Neo4j:

```
Honcho webhook → dispatcher hooks.py → Supabase knowledge store → Neo4j mirror
                                          └→ USER.md append
                                          └→ LEARNED_IN edge
```

Edge types in play:
- `(:Fact)-[:LEARNED_IN]->(:Session)` — session provenance
- `(:Fact)-[:RELATED_TO]->(:Fact)` — pgvector similarity
- `(:Fact)-[:SUPERSEDES]->(:Fact)` — CORRECTION tag (wired, no real instances yet)

## Wiring

Two Honcho handlers pass `session_id` to `_write_to_neo4j()`:

| Handler | Event type | Supabase | USER.md | Neo4j | LEARNED_IN |
|---|---|---|---|---|---|
| `_handle_honcho_session` | session_summary | ✅ store | — | ✅ mirror | ✅ |
| `_handle_honcho_conclusion` | conclusion | ✅ store | ✅ append | ✅ mirror | ✅ |
| `_handle_honcho_observation` | observation | ❌ | MEMORY.md ptr | ❌ | ❌ |

Conclusions tagged `honcho,conclusion,behavioral` — scores 0.08-0.09, nearly invisible in vector search. Value is in USER.md (push-based) and Neo4j (provenance), not vector retrieval.

## End-to-End Benchmark

Simulated Honcho webhook → full pipeline:

| Stage | Time | Detail |
|---|---|---|
| Webhook → response | 1.6s | HMAC verify + Supabase store + Neo4j write |
| Knowledge store | ✅ | Row inserted to Supabase |
| Neo4j Fact | ✅ | Node created with text |
| LEARNED_IN edge | ✅ | Fact → Session |
| RELATED_TO edges | 3 | Auto by pgvector similarity |
| **Full pipeline** | **~3.1s** | End-to-end verified |

## Provenance Queries (20 sessions × 4 facts benchmark)

| Query | Time | Purpose |
|---|---|---|
| Session timeline | 0.10s | "What did we learn per session?" |
| Rich sessions | 0.07s | "Which sessions produced the most?" |
| Graph expansion | 0.08s | "What's connected to session X?" |
| Cross-session recurrence | 0.12s | "Which facts recur across sessions?" |
| Session density | 0.11s | "How connected is a session's knowledge?" |

All under 0.12s at 20 sessions/80 facts scale.

## Pre-existing Bug Fixed

Honcho session handler referenced `~/.hermes/.venv/bin/python3` which never existed. Knowledge store subprocess silently failed since deployment — only Neo4j mirror worked. Fixed to use `/usr/local/lib/hermes-agent/venv/bin/python3 ~/.hermes/scripts/knowledge.py`.

## Dispatcher Restart Pattern

The dispatcher runs as bare uvicorn (not systemd). To pick up hooks.py changes:

```bash
# Kill all on port 8787, clear bytecode cache, restart with .env
kill $(lsof -ti:8787)
find ~/hermes-dispatcher -name '__pycache__' -type d -exec rm -rf {} +
cd ~/hermes-dispatcher && set -a && source .env && set +a && \
  .venv/bin/uvicorn server:app --host 0.0.0.0 --port 8787 --log-level warning &
```

Pitfall: `.pyc` bytecode cache can mask file changes. Always clear `__pycache__` after editing hooks.py.

## Contradiction Detection

Wired as logging-only in `_write_to_neo4j()`. When a new fact has a pgvector similar fact at score > 0.85 AND the texts are not substrings of each other, logs:

```
logger.warning("potential contradiction: new=%s (score=%.3f) vs existing=%s", ...)
```

No edges created yet — observation phase.
