# Tiered Model Benchmark + Aux-Routing Decision Filter
Session: 2026-06-17. Goal stated by user: **throughput/performance over efficiency**, on a
flat $200 Claude Max plan (OAuth bypass = rate-limit cost, not metered $). Local models had
already dropped his metered token usage significantly.

## Head-to-head: qwen2.5-coder:14b vs qwen2.5-32b-32k
Both built/pinned at num_ctx 32768, warmed before recording, P=4, identical prompt
(400-token code-gen + complexity explanation). Native context of coder:14b is already 32768
(no RoPE stretch). 14B is Q4_K_M, 14.8B params, 9GB on disk.

| Metric | 14B-coder | 32B | 14B advantage |
|---|---|---|---|
| Single-task eval | 29.0 t/s | 13.5 t/s | 2.15× |
| Single wall (400 tok) | 13.8s | 29.6s | 2.15× |
| Prompt ingestion | 1595 t/s | 378 t/s | 4.2× |
| Aggregate @ 4-wide | 41.3 t/s | 19.3 t/s | 2.14× |
| 4 tasks wall-clock | 38.6s | 83.0s | 2.15× |
| VRAM resident | 34.9GB | 54.0GB | 19GB lighter |

Concurrency curves (aggregate t/s): 14B N=1/2/4 = 28.6 / 32.6 / 41.3; 32B = 13.4 / 15.2 / 19.3.
Same ~1.5× parallel-efficiency shape both models — bandwidth-bound regardless of size.

## Architecture constraint (the key finding)
`delegate_task` exposes NO model param. `tools/delegate_tool.py::delegate_task(goal, context,
toolsets, tasks, max_iterations, acp_command, acp_args, role, background, parent_agent)`.
Child model resolves from ONE config block via `_resolve_delegation_creds` →
`configured_model = cfg.get("model")`, and `effective_model = model or parent_agent.model`.
=> All subagents share `delegation.model`. Tiering is binary (what the single config points
at), not per-task — unless agent code is patched to thread a model through each task dict.

## Aux-role routing decision filter (local Studio vs Sonnet)
The question is NOT "what can move to Sonnet" — most aux roles (`provider: auto`) ALREADY
resolve to the main model (Sonnet) via `agent/auxiliary_client.py` auto-detection. Only 4
roles ran on the Studio. The right question: "what's LOCAL that shouldn't be?" Filter:

| Role | Keep local? | Reason |
|---|---|---|
| delegation | **local** (user insists) | The whole point of the node; user is actively tuning it. |
| web_extract | → Sonnet | In INTERACTIVE path — user waits on page reads. Faster + better extraction. |
| compression | → Sonnet (if tokens spare) | Better context compression = session quality. First to move BACK local if hitting Max 5-hr caps. |
| curator | **local** | Pure background cron, zero perceived latency. Let the node earn its keep. |
| vision | → Sonnet (done earlier) | Trivial volume (5 calls/14d), quality up, freed VRAM. |

Rule of thumb: **interactive-path + latency-sensitive → Sonnet; high-volume background +
latency-tolerant → keep local** (don't make background jobs compete with live turns for the
rate-limit window). On a flat Max plan with headroom, bias toward Sonnet for anything the
user perceives latency on; keep crons local.

## How aux 'auto' resolves
`auxiliary.<role>.provider: auto` + empty model → auto-detection chain → main provider
(Anthropic/Sonnet) for text tasks unless a cheaper backend is detected. So leaving a role at
`auto` is already "on Sonnet" in this setup. To force local, set explicit
`provider: custom:mac-studio` + `model:` + `base_url: http://<studio-host>:11434/v1`.
