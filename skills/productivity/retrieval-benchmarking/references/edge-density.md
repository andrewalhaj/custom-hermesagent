# Edge Density — Creating RELATED_TO Edges for Isolated Facts (2026-06-25)

## Problem

62.9% of Neo4j nodes (122/194) had zero RELATED_TO edges. No graph traversal algorithm can bridge facts with no edges. Multi-hop end recall ceiling: 46.7% (7/15 chains).

## Procedure

1. Query Neo4j for isolated infrastructure facts (skip session transcripts, `--text` prefixed webhook artifacts, Sentry alert reports)
2. For each isolated fact, find top-3 pgvector-similar neighbors via Supabase `match_knowledge_v3`
3. Match neighbor texts to Neo4j nodes via `f.text STARTS WITH $text`
4. MERGE `[:RELATED_TO]` edges: `MATCH (a:Fact {id: $a}), (b:Fact {id: $b}) MERGE (a)-[:RELATED_TO]->(b)`

## Target Facts Connected

| Isolated Fact | Connected To |
|---|---|
| Claude Code binary path | Hermes WebUI |
| Coder fleet coder-b | Coder fleet trimmed to 4 |
| Dispatcher dashboard | hermes-dispatcher service, Hermes WebUI, Wall Dashboard |
| Dashboard design parity | Wall Dashboard, Hermes WebUI, hermes-dispatcher |
| All agents run on Mac Mini | hermes-dispatcher service |
| Knowledge store architecture | Knowledge store pgvector, Memory system, Neo4j Aura |
| Linear configuration | Hermes Kanban, Fleet repos, Hermes config |

## Results

| Metric | Before | After |
|---|---|---|
| Edges | 128 | 145 (+17) |
| Isolated | 122 (62.9%) | 113 (58.2%) |
| Connected facts | 72 | 81 |

## Side Effect: Chain Rotation

Adding edges changes the `find_multi_hop_chains` output. Old chains (hermes-dispatcher→Tailscale→Mac Mini) were replaced by new chains (Knowledge store→Neo4j→Supabase). Multi-hop both-recall on new chains: 0% baseline → 33.3% with chain traversal. The decline from 46.7% to 33.3% is because new chains are harder (different community pairs), not because edge creation hurt quality.

## Pitfall: `--text` Prefixed Facts

Chain discovery picks up webhook-artifact facts prefixed with `--text`. These produce poor query keywords. The benchmark should filter: `WHERE NOT f.text STARTS WITH '--text' AND NOT f.text STARTS WITH 'Session:'`.
