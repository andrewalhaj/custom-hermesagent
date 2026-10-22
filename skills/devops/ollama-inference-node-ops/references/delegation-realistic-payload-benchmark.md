# Delegation Benchmark — Realistic 13k-Token Agent Payload (2026-06-18)

Node: Mac Studio M2 Max, `qwen2.5-32b-64k` (num_ctx 65536), P=4, MAX_LOADED=1, 56GB cap.
Payload: ~12,675-token system prefix (mimics AGENTS.md + tool schemas) + short task suffix —
i.e. what a real `delegate_task` child actually sends, NOT a toy prompt.

## Single task (3 warm runs — CACHE-INFLATED, see note)
| run | total | prompt ingest | gen |
|---|---|---|---|
| 1 | 11.9s | 8151 tok @ 92,416 t/s (0.1s) | 139 tok @ 12.0 t/s |
| 2 | 12.3s | 8151 tok @ 93,722 t/s (0.1s) | 143 tok @ 11.9 t/s |
| 3 | 12.7s | 8151 tok @ 93,426 t/s (0.1s) | 148 tok @ 12.0 t/s |

⚠️ The 0.1s ingest = warm-slot cache hit. These are NOT cold singles. A true cold single
(measured via real delegate_task) is ~115–140s. Do not quote the 12s figure as "single task speed."

## Concurrency scaling (uncached — the real signal)
| concurrent | wall | aggregate t/s | per-task |
|---|---|---|---|
| 1 | 12.5s | 661 (cached) | 12.5s |
| 2 | 103.3s | 160 | ~103s |
| 4 | 310.3s | 107 | ~310s |

**Aggregate throughput DECLINES monotonically with parallelism on this box.** Each added slot
adds a fresh ~12k cold ingest that saturates the ~400 GB/s memory bus at once. Decode is
bandwidth-bound on weight bytes/token; ingest is bandwidth-bound on the same bus. More slots =
more simultaneous cold ingests = collapse.

## Real delegate_task sequential calls (cache-survival test)
| call | duration | model | log evidence |
|---|---|---|---|
| 1 (cold) | 139.7s | qwen2.5-32b-65k | `new prompt, task.n_tokens = 13374` |
| 2 (same toolset, diff task) | 109.2s | qwen2.5-32b-65k | `new prompt, task.n_tokens = 13374` — FULL re-ingest |

Only 22% improvement, not the ~190× the raw-curl prefix test showed. Slot logs prove no prefix
reuse: both calls full-ingest. Cause = unique Session ID in prompt + P=4 slot scatter/overwrite.
See SKILL.md "Prefix caching does NOT help real delegation" section.

## Raw-curl prefix-cache test (engine-level, WORKS — but doesn't transfer)
Fixed ~12.8k prefix + varying suffix, sequential curls to /api/chat:
- call 2: prompt_eval 12,792 tok in **0.54s** (vs ~100s cold) ⚡
- call 3: prompt_eval 12,792 tok in **0.53s** ⚡
Proves the ENGINE caches a static prefix. Does NOT prove delegate_task benefits — it doesn't,
because the agent prompt isn't byte-stable (Session ID) and P>1 scatters slots.

## Bottom-line decision data
- Local 4-task batch (P=4): ~310s.  Local sequential P=2 w/ partial cache: ~150s est.
- The M2 Max ingests ~13k agent tokens at ~96 t/s cold (~100s to first token). That ingest cost,
  paid per child unless caching works, is THE bottleneck — not the 12 t/s generation.
- Untested but highest-leverage local lever: P=1 + sequential dispatch to make prefix cache real.
