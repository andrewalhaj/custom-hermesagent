# Retrieval Latency Diagnosis — 2026-08-13 run

Full record of the pass that killed the "23s/query is embed+graph" myth. Reusable method for any future "why is search slow" question.

## Outcome (headline)

| State | Per-query latency |
|---|---|
| RERANK=true (dotenv contamination) | 9–58s |
| RERANK=false, all mandated flags | **0.5–1.2s** |
| GRAPH_TRAVERSE share | ~0.5s (6%) — identical top-5 on/off, keep ON |
| embed round-trip (Ollama on the Mini, model warm) | ~0.2s |

Root cause: `RERANK=true` on line 1 of `~/.hermes/.env`. Cross-encoder (`mxbai-rerank-base-v1`, `_cosine_rerank` in search.py) runs CPU-bound `predict()` over (query, hit-text) pairs; cost scales with candidate text length → large per-query variance (9s vs 58s) that LOOKS like a data-dependent retrieval problem but isn't.

## Why live turns never showed it

The gateway does not load `.env` into os.environ (long-standing quirk, also documented in the embedding-dim pitfall). Only processes sourcing the dotenv paid: persistent shells, cron scripts, benchmarks, heartbeat jobs. **A latency complaint that reproduces in scripts but not in live turns (or vice versa) = env divergence between the two surfaces. Check flags first, profile second.**

## Method (two phases, cheapest first)

### Phase 1 — A/B flag toggle (suspect elimination)
Run the same 5 queries with a suspect flag on/off; compare wall time AND top-5 overlap. Probe script: `scripts/graph_latency_probe.py` (adjust the env var under test). This exonerated GRAPH_TRAVERSE in one run: 6% share, 5/5 overlap everywhere.

### Phase 2 — stage-level wrapper profiling (mechanism naming)
Monkeypatch-time every internal of `knowledge/search.py` on a fast query and a slow query. Script: `scripts/search_stage_profile.py`. Key traps it already handles:

1. **Module shadowing:** `knowledge/__init__.py` re-exports `search`/`embed` functions over their modules. Get the real module via `sys.modules['knowledge.search']` after import — `import knowledge.search as ks` gives you the FUNCTION.
2. **Symbol rebinding:** if search.py did `from knowledge.embed import embed`, patching `knowledge.embed.embed` does nothing — rebind the wrapped fn on the search module too.
3. Wrap the REAL function list (grep `^def ` in search.py first), don't guess names.

Output shape that names the culprit instantly: one stage owning >90% of wall time on the slow query (`_cosine_rerank 58.77s of 59.50s`).

## Verification discipline

- Print effective flags from `knowledge.flags` (`_rerank_enabled()` etc.) **in the same process** as the measurement — shell env, dotenv, and systemd drop-ins can all disagree.
- 5/5 top-5 overlap on/off = the flag contributes latency but not ranking → safe to judge on latency alone.
- Prior art in-tree: `scripts/knowledge/profile_rerank2.py` (someone hit this before; check for existing instrumentation before writing new).

## Standing corrections this produced

- 2026-07-09 "23s/query, embed+graph dominate" finding: **falsified** — do not re-cite.
- 50-query benchmark true runtime: ~2-5 min clean, not 20-40 min.
- Gated proposal pending as of this run: flip `.env` RERANK to false + this correction (Andrew greenlight requested).
