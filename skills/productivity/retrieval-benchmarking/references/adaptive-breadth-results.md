# Adaptive Retrieval Breadth — Benchmark Results

**Date:** 2026-06-25
**Script:** `benchmark_adaptive_breadth.py` (deleted 2026-10-16; fixed-k sweep is `scripts/kbench.py sweep`)
**Key insight:** Adaptive `top_k` killed by classifier — fixed `top_k=8` is simpler and better.

## Classification Results

| Query | Tier | Adaptive k | Correct? |
|---|---|---|---|
| dispatcher webhooks port 8787 | EXACT | 3 | ✅ |
| wall dashboard VPS-A nginx | EXACT | 3 | ✅ |
| LH02 embedding nomic | EXACT | 3 | ✅ |
| neo4j aura graph database | SPECIFIC | 5 | ✅ |
| knowledge store supabase pgvector | SPECIFIC | 5 | ✅ |
| fleet repos hermes-dispatcher | SPECIFIC | 5 | ✅ |
| Linear integration webhook | SPECIFIC | 5 | ✅ |
| Sentry integration SDK setup | SPECIFIC | 5 | ✅ |
| kanban dispatch task worker | SPECIFIC | 5 | ✅ |
| Home Assistant dashboard | SPECIFIC | 5 | ✅ |
| **Hermes WebUI react frontend** | **SPECIFIC (k=5)** | **5** | **❌ needs k≥8** |
| Neo4j MCP server configuration | SPECIFIC | 5 | ✅ |
| Tailscale network hosts | BROAD | 13 | ✅ |
| Hermes config yaml model provider | BROAD | 13 | Can't help (isolated) |
| coder fleet workers | BROAD | 13 | ✅ |

## P@5 by top_k

| top_k | P@5 | Latency | Notes |
|---|---|---|---|
| 3 | 86.7% | 1.06s | Same as 5 — no loss |
| **5** | **86.7%** | 0.89s | Baseline |
| **8** | **93.3%** | 0.91s | Recovers Hermes WebUI |
| 13 | 93.3% | 0.94s | No gain over 8 |
| 21 | 93.3% | 0.96s | No gain over 8 |
| Adaptive | 86.7% | 0.92s | Misclassification penalty |

## The Two Permanent Misses

- **Hermes config** — Isolated fact, zero Neo4j edges, always scores 0.00. No `top_k` or classifier can fix this.
- **Hermes WebUI** — Isolated fact, appears at rank 6-8, recovered by `top_k=8`.

## Why Adaptive Fails

The classifier uses keyword rules: "react", "frontend", "webui" → SPECIFIC (k=5). But Hermes WebUI is an isolated fact that needs a wider pool to surface. The classifier can't know this without running the query. Query complexity ≠ retrieval difficulty at 152 facts.

## Bottom Line

Fixed `top_k=8` shipped as the default. It's simpler, faster to implement, and doesn't need a classifier that would need to be wrong on exactly the queries it's supposed to help.
