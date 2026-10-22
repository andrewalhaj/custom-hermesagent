# NUM_PARALLEL A/B/C Benchmark + Aux-Routing Decision Filter

Captured 2026-06-17 on mac-studio (M2 Max, 64GB, 56GB VRAM cap).
Model: qwen2.5-32b-32k (Q4_K_M, 19GB weights, native 32k ctx).
Prompt: "Count slowly 1→50, one per line", num_predict=160. Warm runs only.

## Concurrency scaling — full grid (wall-clock / aggregate t/s)

| Config | N=1 | N=2 | N=3 | N=4 | VRAM | vision co-resident |
|--------|-----|-----|-----|-----|------|--------------------|
| P=2 | 12.1s / 13.3 | 21.6s / 14.8 | — | — | 37.5GB | ✓ 18.5GB spare |
| P=3 | 11.7s / 13.6 | 20.9s / 15.3 | 30.9s / 15.5 | — | 45.8GB | ✓ barely |
| P=4 | 12.1s / 13.2 | 21.7s / 14.8 | 32.3s / 14.9 | 33.4s / 19.2 | 54.4GB | ✗ 1.6GB margin |

Per-slot eval t/s at full load: P=2→7.6, P=3→5.3, P=4→4.9. Single-stream baseline ~13.5 t/s.

## What the data proves (do not re-derive this — it's measured, not theoretical)
1. **Memory-bandwidth-bound, not compute-bound.** One stream already pulls ~256/400 GB/s.
   Parallel slots share weight reads (small win) then saturate the bus.
2. **~15-16 t/s aggregate ceiling for ≤3 concurrent.** P=2 and P=3 plateau identically.
3. **Only the 4th slot breaks the plateau** (15.5→19.2 t/s), i.e. P=4 is worth it ONLY if
   you genuinely run 4 concurrent decodes. P=3 is wasted middle ground for aggregate.
4. **Aggregate gain P=2→P=4 is +32%** (14.8→19.2), parallel efficiency ~37%. Never "Nx".
5. **Per-slot latency degrades monotonically** with P. A saturated P=4 node makes any one
   subagent ~2.7× slower than solo. Throughput-max ≠ latency-min.

## Config-shape recommendation
- **Batch fan-out delegation (many children at once):** P=4. Vision MUST move off-Studio
  (to Sonnet) to free the VRAM — at P=4 the 32B alone is 54.4GB.
- **Latency-sensitive single delegations:** P=2 or P=3, keeps per-task speed up + leaves
  VRAM for vision co-residency.
- Final state this session: P=4, MAX_LOADED=1, vision→Sonnet, max_concurrent_children=12.

## Studio aux-role routing — the decision filter (local vs Sonnet/Anthropic)
`auxiliary.<role>.provider: auto` resolves to the MAIN model (Anthropic/Sonnet) via the
auto-detect chain (`agent/auxiliary_client.py::_normalize_aux_provider`, "main"→main provider).
So most aux roles are ALREADY on Sonnet by default. Only roles explicitly set to
`custom:mac-studio` run local. As of this session those were: compression, curator, web_extract.

**The filter is NOT "what can move to Sonnet" — it's "what's local that SHOULDN'T be":**
- Keep LOCAL: high-volume + latency-tolerant + background work. Moving it to Sonnet makes
  background jobs compete with interactive turns for the same Anthropic rate limit. The Studio
  exists precisely to keep this OFF the metered path. → compression (fires on every long
  session, verbose, quality barely matters), curator (scheduled cron, runs while you sleep).
- Move to SONNET: roles in the INTERACTIVE latency path where local inference makes the USER
  wait, AND volume is low enough that rate-limit contention is negligible. → vision (5 calls/
  14d, quality upgrade, freed the VRAM co-residency constraint), web_extract (you wait on it
  during research; 13.5 t/s is slow for a long page; Sonnet is faster + better extraction).

Anti-pattern to avoid: treating "shift toward Sonnet" as a default direction. It's a
case-by-case filter (interactive-path + low-volume), not a migration.
