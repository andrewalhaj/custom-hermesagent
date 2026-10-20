# Layered vs Flat Retrieval Architecture

## Experiment (2026-06-25)

Built a 4-layer sequential pipeline:

```
Layer 1: Sector filter (SQL WHERE sector_path->>0 = query_domain)
Layer 2: Vector search (pgvector + FTS RRF on filtered pool)
Layer 3: Graph traversal (1-hop or 2-hop from top hits)
Layer 4: Cross-encoder rerank
```

vs the flat pipeline where all modes run on the same candidate pool.

## Results

| Architecture | P@5 | Failure mode |
|---|---|---|
| **Flat + SECTOR_BOOST** | **100%** | Soft multiplier, zero risk |
| **Flat + SECTOR_FILTER** | 87% | Classification errors exclude facts at RPC level |
| **Layered (filter→vector→graph→rerank)** | **80%** | Amplifies classification errors — wrong-sector facts never enter the pool |

## Specific Failures

| Query | Query sector | Fact sector | Root cause |
|---|---|---|---|
| "Hermes WebUI react frontend" | development→webui | infrastructure→deploy | Fact mentions "dispatcher", misclassified by keyword rule |
| "Linear integration webhook" | monitoring→linear | — | Expected substring in wrong-sector fact |
| "Hermes config yaml" | infrastructure | infrastructure | Always 0.00 (isolated, no edges) |

## Verdict

The 4-layer pipeline is architecturally correct. It fails at 152-fact scale because:

1. **Classification is keyword-based**, not semantic. ~13% of facts are in the wrong sector because keywords like "dispatcher" or "deploy" appear in passing.
2. **Hard filtering amplifies classification errors** — a soft boost (SECTOR_BOOST) gives 100% P@5; a hard filter (SECTOR_FILTER) drops to 87%; adding vector search on top (LAYERED) drops further to 80%.
3. **Precise classification requires LLM** (qwen-32b or similar), not keyword rules. Until classification is ≥95% accurate, soft boosting beats hard filtering.

**Recommendation:** Keep `_search_layered()` gated behind `LAYERED_RETRIEVAL=true` for future use. Ship flat pipeline with SECTOR_BOOST. When LLM-based classification reaches production quality, revisit layered.

## "spa" Substring Bug

Keyword-based query detection has substring false positives. Example:

```python
any(w in q for w in ['spa'])  # 'spa' is a substring of 'dispatch'!
# "dispatch" → d-i-s-p-a-t-c-h substrings include "spa" at positions 2-4
```

This caused "kanban dispatch task worker" to be detected as development→webui→build because "spa" matched "dispatch". Fixed by removing "spa" from the webui detection rule. Lesson: single-word substring matching on short tokens (<4 chars) produces phantom hits. Use multi-word patterns or longer tokens.
