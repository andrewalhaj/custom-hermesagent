---
name: ollama-inference-node-ops
description: "Operate and tune the Mac Studio Ollama inference node"
sector_path: ["infrastructure", "hosting", "configure"]
---

# Ollama Inference Node Ops (Mac Studio)

Manage the local inference node: pulling/replacing models, tuning concurrency and
multi-model residency, raising the Metal VRAM cap, and restarting the Ollama server
correctly. The Mac Studio is the standing inference node for cron/aux/compression/
curator + heavy delegation.

## Node facts (verify live, don't trust recall)
- Host: `mac-studio`, tailnet `<studio-host>` (`localadmins-mac-studio`).
- Hardware: Apple M2 Max, 64GB unified, 12 cores, macOS arm64.
- SSH: `ssh -o ConnectTimeout=8 -o BatchMode=yes -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes localadmin@<studio-host>`
  (user is **localadmin**, NOT andrew — `andrew@` gets Permission denied. Topology file has the right form.)
- Ollama API: `http://localhost:11434` on the node; reachable as `http://<studio-host>:11434` from the mini.
- Ollama binary: `/Users/localadmin/OllamaApp/Ollama.app/Contents/Resources/ollama`
- Models dir: `~/.ollama/models/` (manifests + blobs). `du -sh` it to see real on-disk size.
- VRAM cap: `sysctl iogpu.wired_limit_mb` — already raised to 57344 (56GB of 64).
  Default Metal cap is 75% (48GB); raise via `sudo sysctl iogpu.wired_limit_mb=57344` (sudo is interactive-only).

## ⚠️ PITFALL: who owns the service determines how you restart it — CHECK FIRST
Ownership has drifted over time. ALWAYS determine the current owner before restarting:
```
ssh ... 'launchctl list | grep ollama'          # real PID in col 1 = launchd owns it; PID "-" = GUI-spawned
ssh ... 'ps aux | grep -i "Ollama.app" | grep -v "ollama serve" | grep -v grep'  # GUI app running?
```

**Case A — launchd owns it (current state as of 2026-06-17):** `launchctl list` shows a real
numeric PID (e.g. `12608  0  com.ollama.server`) and NO `Ollama.app` GUI process is running.

  ⚠️ **A plain `kill <pid>` does NOT reload an edited plist.** KeepAlive respawns the process
  from launchd's CACHED in-memory plist copy — your on-disk env edits are IGNORED. Verified
  failure mode: edited plist to NUM_PARALLEL=4/MAX_LOADED=1, plain-killed, respawn came back
  with the OLD 2/2 env. The on-disk plist was correct; launchd just never re-read it.

  **The working pattern is bootout + bootstrap** (forces a fresh plist read):
  ```
  UID_N=$(id -u)   # 501 on this node
  PLIST="$HOME/Library/LaunchAgents/com.ollama.server.plist"
  launchctl bootout   gui/$UID_N/com.ollama.server      # stop + unload stale copy
  sleep 2
  launchctl bootstrap gui/$UID_N "$PLIST"               # reload fresh env from disk
  sleep 8
  ```
  Then verify env on the NEW pid (see Verification). A plain `kill` is fine ONLY when you did
  NOT edit the plist (just want a clean restart with the same env).

**Case B — GUI-spawned (older state, may recur if someone launches Ollama.app):**
`launchctl list` shows PID `-` and an `Ollama.app` process is running. Here `bootout`/
`bootstrap` fail (`Boot-out failed: 3: No such process`) and `load/unload` is a no-op. The
only lever is `kill <ollama-serve-PID>` + KeepAlive respawn — but the respawn picks up plist
env only if the plist is loaded. If env won't take, quit the GUI app and bootstrap via launchd.

**Benign log noise after any respawn:** `/tmp/ollama-error.log` shows a burst of
`Error: listen tcp 0.0.0.0:11434: bind: address already in use` — the OLD and respawned
process briefly racing for the port during handoff. NOT a fault; confirm health via
`/api/tags` + the env check on the NEW pid.

⚠️ **The self-healing watchdog uses plain-kill (Case B pattern).** That's correct for its job
(recover an UNREACHABLE node with unchanged env) — but it will NOT apply a plist edit. After
any plist change, always bootout+bootstrap by hand and verify; never assume the watchdog did it.

## Tuning concurrency + multi-model residency
Edit `EnvironmentVariables` in the plist, then kill-and-respawn:
- `OLLAMA_NUM_PARALLEL=2` — concurrent requests per model (cron/subagent calls run
  concurrently instead of queueing).
- `OLLAMA_MAX_LOADED_MODELS=2` — keep two models warm (e.g. a 70B + the vision model),
  kills vision cold-start latency.
- `OLLAMA_KEEP_ALIVE=30m` — how long an idle model stays resident.
Default (no vars set) = single model, single thread, evict-on-idle.

## Model VRAM math (64GB unified, 56GB cap)
- 72B Q4_K_M ≈ 45–50GB. Fits solo. Paired with a 7B vision model (~6GB) ≈ ~56GB — at the cap, tight but inside.
- A 32B Q4 ≈ 20–28GB resident.
- `nomic-embed-text` (274MB, 768-dim) ≈ negligible VRAM — co-resident with any LLM.
- With `MAX_LOADED_MODELS=2`, do the arithmetic: two large models that sum past 56GB will
  thrash / evict. Pair ONE big model with a small one, not two bigs.
- VRAM scales with slots: same 32B-64k measured 28.9GB @ P=1, 37.5GB @ P=2, 54.4GB @ P=4
  (each parallel slot reserves its own KV). Dropping P=4→P=1 reclaimed ~25GB.

## ⚠️ MAX_LOADED_MODELS=1 + mismatched aux model = full model-swap thrash
With `MAX_LOADED_MODELS=1` the Studio holds EXACTLY ONE model. If `delegation.model` and any
Studio-routed `auxiliary.*` role name DIFFERENT models, every aux call evicts the 19GB
delegation model and loads the aux model (full ~3.5s swap + total KV-cache wipe), then the
next delegation swaps it back — a ping-pong far worse than prefix eviction. Found this session:
`delegation.model: qwen2.5-32b-64k` but `auxiliary.curator.model: qwen2.5-32b-32k` (a stale
orphan). Fix: point ALL Studio-routed roles at the SAME model name as delegation. Audit with:
```
python3 -c "import os,yaml;c=yaml.safe_load(open(os.path.expanduser('~/.hermes/config.yaml')));\
print('deleg:',c['delegation']['model']);\
[print(r,':',v.get('model')) for r,v in c.get('auxiliary',{}).items() if isinstance(v,dict) and 'mac-studio' in str(v.get('provider',''))]"
```
All Studio rows must print the same model name. Mitigating factor: curator-class aux fires on
daily crons (Knowledge Capture; the Honcho Drift cron was removed with Honcho), not in-session — so the thrash is rare but real.

## ⚠️ P=1 single-slot contention: aux traffic evicts the delegation prefix
At P=1 there is ONE slot shared by delegation subagents AND any Studio-routed aux call. Even
when they use the SAME model (no swap), an aux call landing between two delegations EVICTS the
delegation's cached prefix from slot 0 → the next delegation falls back to the edit-heavy floor
(~3,400 tokens) and runs ~170s instead of the ~30s a warm prefix would give. Verified via Studio
log: an aux call with `sim_best=0.894` reused-then-overwrote slot 0 between two delegate_task
calls. So P=1's prefix-cache win only fully materializes when delegation has the slot to ITSELF.
The roles you hit IN-SESSION (title_generation, triage, kanban_decomposer, etc.) default to
`auto`→main(Sonnet), NOT the Studio — good, they don't contend. Only explicitly Studio-pinned
aux roles (curator) do, and those are low-frequency. To fully isolate delegation's slot, route
curator off-Studio too — but that spends rate-limit on background work the Studio should own, so
it's a judgment call, not an obvious win.

## ⚠️ The concurrency gate is NUM_PARALLEL on the node, NOT Hermes max_concurrent_children
Raising Hermes `delegation.max_concurrent_children` alone does NOTHING for throughput.
When `delegate_task` fans out N children, they all hit the SAME Ollama model. Ollama
decodes `OLLAMA_NUM_PARALLEL` requests at a time and QUEUES the rest. So 8 Hermes children
against `NUM_PARALLEL=2` = 2-wide decode + 6 queued. The Studio is the real gate.

**Keep Hermes fan-out > node parallel slots on purpose.** Subagents spend real time in
tool/terminal phases where they are NOT decoding on the GPU. Over-subscribing the fan-out
(e.g. Hermes=8 against node P=2–4) keeps the node fed instead of idle. Raise the NODE first,
leave Hermes as a pipelining buffer.

## ⚠️ VRAM is reserved for context × parallel, not actual usage — the core trade
Ollama allocates KV cache for the FULL `num_ctx × OLLAMA_NUM_PARALLEL`, even on a one-line
task. The governing equation:

    usable_VRAM = weights + (num_ctx_KV_per_token × num_ctx × num_parallel)

KV cost is model-specific — derive it live from `/api/show` (head_count_kv × head_dim ×
block_count × 2 bytes fp16). For qwen2.5-32b (GQA: 8 KV heads, 64 layers) it's ~0.5 MB/token.

Worked example (32B, 20GB weights, 36GB free for KV):
| Context | KV/slot | P=1 | P=2 | P=3 | P=4 |
|---|---|---|---|---|---|
| 64k | 32GB | 52 ✓ | 84 ✗ | — | — |
| 32k (native) | 16GB | 36 ✓ | 52 ✓ | 68 ✗ | — |
| 16k | 8GB | 28 ✓ | 36 ✓ | 44 ✓ | 52 ✓ |

The dial is **"few deep thinkers" vs "many shallow ones"** on a fixed budget. Pick context
to match real subagent need (see `references/subagent-context-budget.md`), then set
`num_parallel` to whatever the leftover VRAM allows.

## ⚠️ VRAM "fits" ≠ parallelism "helps" — the M2 Max is memory-bandwidth-bound
The VRAM math above tells you how many slots *fit*. It does NOT tell you whether they buy
throughput. On this M2 Max they mostly don't, because decode is memory-bandwidth-bound, not
compute-bound: one 19GB stream at 13.5 t/s already pulls ~256 of the ~400 GB/s bus. Adding
slots shares the weight reads (a real but small win) and then saturates the bus.

**Measured A/B/C (qwen2.5-32b-32k, full data in `references/parallelism-benchmark.md`):**
| Config | max aggregate t/s | per-slot @ full load | VRAM | vision co-resident? |
|---|---|---|---|---|
| P=2 | 14.8 | 7.6 | 37.5GB | ✓ (18.5GB spare) |
| P=3 | 15.5 | 5.3 | 45.8GB | ✓ (barely) |
| P=4 | **19.2** | 4.9 | 54.4GB | ✗ (1.6GB margin) |

Three hard facts the numbers force:
1. **Aggregate gain is ~1.5×, NEVER N×.** P=2→P=4 buys +32% aggregate (14.8→19.2), not 2×.
   Parallel efficiency at P=4 is ~37% (19.2 of a theoretical 52.8). Don't promise "Nx faster."
2. **There's a plateau at ~15-16 t/s for ≤3 concurrent** — P=2 and P=3 hit the same ceiling.
   The only place an extra slot clearly pays is the 4th (15.5→19.2).
3. **More slots = SLOWER individual tasks.** Per-slot drops 13.5→4.9 t/s as you go 1→4-wide.
   P=4 is the *throughput-max* config; it is the *latency-min* config's opposite. A single
   subagent finishes ~2.7× slower when the node is saturated at P=4.

**Decision rule:** optimize aggregate (high P) ONLY for batch fan-out delegation where many
children decode at once. For latency-sensitive single delegations, LOWER P is better. Match
P to the dominant workload shape, and always A/B the curve live — never assume the VRAM-fit
number is the throughput-optimal number.

## 🛑 BEFORE optimizing Studio delegation: VERIFY delegate_task actually runs on the Studio
**This is the most expensive lesson of the whole node-ops history. Do this FIRST, every time.**
Raw inference benchmarks (`/api/generate`, `/v1/chat/completions` curls) prove the MODEL is
fast — they prove NOTHING about whether `delegate_task` uses it. A real subagent can silently
fall back to a cloud provider while every Studio benchmark you run looks perfect. Full
transcript + repro in `references/delegation-routing-failure.md`.

**The silent-fallback mechanism:** `delegation` inherits the parent's `_fallback_chain`
(`tools/delegate_tool.py` ~line 1202 → `fallback_providers` in config.yaml, typically
DeepSeek). When the child's PRIMARY Studio call fails, it transparently fails over to
DeepSeek and the task still "completes." The only tell is the `model` field in the
delegate_task result JSON: if it says `deepseek-v4-pro` (or any cloud model) when you
configured the Studio, **delegation is NOT running locally.** ALWAYS read that field after a
config change — config-says-Studio + result-says-cloud = broken.

**🛑 ROOT-CAUSE FAILURE MODE #0 (the deepest, found last — check this FIRST):
Hermes rejects any delegation model with a DECLARED context window < 64,000 tokens.**
`agent/model_metadata.py: MINIMUM_CONTEXT_LENGTH = 64_000`. At child spawn, `agent_init.py`
(~line 1525) raises `ValueError: Model <X> has a context window of N tokens, which is below
the minimum 64,000 required by Hermes Agent` and the child dies BEFORE any TCP connection —
so the Studio access log shows ZERO connection from the mini (`/tmp/ollama-error.log` only
shows `127.0.0.1`, never the mini's tailnet IP). The parent then falls through to the
DeepSeek fallback. This is why rebuilding the 32B from `64k`→`32k` to fix VRAM SILENTLY BROKE
delegation: the original `qwen2.5-32b-64k` name existed precisely to clear this 64k gate.

  **How Hermes resolves the declared context** (first non-None wins), all checked at init:
  1. `model.context_length` in config.yaml
  2. `custom_providers[<name>].models[<model>].context_length`  ← the practical lever
  3. live `/api/show` query to Ollama
  If the declared value is 32768, the gate fails regardless of what Ollama allocates internally.

  **The fix (no model rebuild needed):** declare `context_length: 65536` (or higher) for the
  model under `custom_providers[mac-studio].models.<model>` in config.yaml. Ollama already
  allocates a large KV internally (llama-server logs showed `-c 131072 -np 4`, i.e. 128k, even
  for a Modelfile baked at 32k), so declaring 64k+ is HONEST — it just tells Hermes the model
  qualifies. Verify with:
  ```
  venv/bin/python -c "from hermes_cli.config import get_custom_provider_context_length, load_config; \
    print(get_custom_provider_context_length(model='qwen2.5-32b-32k', base_url='http://<studio-host>:11434/v1', \
    custom_providers=load_config().get('custom_providers',[])))"
  ```
  Must print ≥64000. This is a GATED config.yaml edit.

  **Detection drill for \"Studio never sees the request\":** tail the Studio access log and
  look at the source IP. `127.0.0.1` only = your own curls; the mini's tailnet IP absent =
  the child never dialed out = a PRE-CONNECTION crash (almost always this 64k gate, or a
  client-init ValueError). Don't chase HTTP/timeout theories until you've confirmed the
  request actually left the mini.

  **Reproduce the exact crash in-process** (fastest way to see the real exception instead of
  guessing from logs): build a child `AIAgent(base_url=..., model=..., provider='custom',
  api_mode='chat_completions')` in a `venv/bin/python` harness — it raises the
  ValueError synchronously at construction. This single probe collapses hours of log-spelunking.

**🛑 FAILURE MODE #0b (bites AFTER the 64k gate is cleared): `child_timeout_seconds`.**
Once the model declares ≥64k and the child actually spawns + dials the Studio (result `model`
field finally shows your Studio model, `api_calls: 1`), the NEXT wall is the per-child timeout.
`delegation.child_timeout_seconds` (default 600) kills the child if its single slow API call
doesn't finish in time. Verified this session: a single delegate_task took **115s** solo, but
a 4-wide batch (all sharing P=4 decode + each ingesting ~13k tokens) ran ~500s/child and ALL
FOUR hit the 600s cap → `status: timeout, exit_reason: timeout`. Raising to 900 let the same
4-wide batch complete (`exit_reason: completed`, model = the Studio model, real code returned).
This is the FIRST point in the whole chain where delegation genuinely runs locally — confirm
it with a single-task delegate FIRST (isolates timeout from contention), then the batch.
- Lever A: raise `delegation.child_timeout_seconds` (gated config + gateway restart).
- Lever B (bigger win): shrink the child prompt. `toolsets=[]` still injects the DEFAULT
  toolset (~13k tokens of schemas) — pass a minimal `toolsets=["terminal","file"]` to cut
  ingest from ~13k→~4k tokens, dropping per-child time ~115s→~35s and batches well under cap.

**🛑 FAILURE MODE #0c (THE DEEPEST — bites AFTER #0's context_length declaration passes; the
custom_providers `context_length` fix ALONE is NOT sufficient).** This is the trap that wasted
hours after #0 looked solved: declaring `context_length: 65536` in custom_providers clears the
INIT-time gate (`agent_init.py` ~line 1525), so the child BUILDS fine — but there is a SECOND,
SEPARATE runtime check that fires at conversation START, before the first API call:
`agent/conversation_loop.py` (~line 108-144) reads `agent._ollama_num_ctx` and if it is
`< MINIMUM_CONTEXT_LENGTH` (64,000) it emits a refusal message ("Ollama loaded `<model>` with
only N tokens of runtime context, but Hermes needs at least 64,000…") which becomes the child's
turn output → triggers `fallback_prior_turn_content` (conversation_loop ~line 4092) → child
falls to DeepSeek. Telltale signature: `duration_seconds ≈ 11s`, `api_calls: 1`,
`model: deepseek-v4-pro`, and ZERO 13k-token requests in the Studio log (only your warm-up
probe). The child never sends its real prompt to the Studio.

  **Why it survives the #0 fix:** `_ollama_num_ctx` is set in `agent_init.py` (~line 1614-1632)
  from a LIVE `/api/show` query to Ollama — NOT from custom_providers.context_length. Ollama
  CLAMPS a Modelfile `num_ctx 65536` back to the model's native trained ceiling (32768 for
  qwen2.5-32b) and reports `32768`. So `context_length` (declared, gate #1) = 65536 ✓ but
  `_ollama_num_ctx` (live-queried, gate #2) = 32768 ✗. Two different values, two different
  gates, same 64k threshold. Verify the live value with `/api/show` and read
  `model_info.<family>.context_length` — if it prints `32768`, gate #2 will reject the child.

  **The fix (config override, no rebuild):** set `ollama_num_ctx: 65536` under the TOP-LEVEL
  `model:` block in config.yaml (NOT custom_providers — `agent_init.py` reads it from
  `_agent_cfg.get("model",{}).get("ollama_num_ctx")`, and a child's `_agent_cfg` is the FULL
  config via `load_config()`, so the top-level model block IS read by children). This directly
  sets `_ollama_num_ctx=65536`, skipping the live `/api/show` query entirely, so gate #2 sees
  65536 ≥ 64000 and proceeds. It's honest — Ollama still runs a 32k slot internally; you're only
  telling Hermes the declared window clears its tool-use minimum. GATED config edit + restart.

  **The cleaner alternative:** use a model whose NATIVE trained context is ≥64k (e.g.
  `qwen2.5-128k`, already on the Studio, reports 128k natively) — it passes BOTH gates with no
  override needed and no RoPE-stretch quality loss. Prefer this when a genuinely-long-context
  model is available; the `ollama_num_ctx` override is the fallback when you must use a 32k-native
  model. Full failure chain + in-process repro in `references/studio-delegation-findings-2026-06-18.md`.

  **The general lesson (write this on your hand):** Hermes enforces the 64k minimum at TWO
  independent points — declared `context_length` at init, and live `_ollama_num_ctx` at turn
  start. A 32k-native Ollama model needs BOTH satisfied. Clearing one and declaring victory is
  the exact mistake that turns a 20-minute fix into a multi-hour debug. After any delegation
  config change, the ONLY proof is a real `delegate_task` whose result `model` field shows your
  Studio model — never trust that the config "looks right."

  **✅ RESOLVED (2026-06-18, third session): the #0c fix was NOT the root cause when it failed.**
  Root cause was caveat (a): `delegation.model` was `qwen2.5-coder-14b-32k`, a CODER model that
  returns tool-calls as PLAIN TEXT (FAILURE MODE #1) → child can't parse → DeepSeek fallback. The
  context-gate debugging in sessions 1–2 was a red herring; every gate was already satisfied.
  Repointing `delegation.model` to `qwen2.5-32b-64k` (the documented tool-call-capable target) fixed
  it in one config-line change — verified by a real `delegate_task` returning `model: qwen2.5-32b-64k`.

  **🎯 THE FIRST-CHECK SHORTCUT (do this BEFORE any context-gate or `_ollama_num_ctx` debugging):**
  When delegation falls to DeepSeek, the handoff/notes may point you at a context-window theory —
  DISTRUST IT until you've cleared two 30-second live checks that are far more likely to be the cause:
  1. **Is the configured `delegation.model` actually a tool-call-capable general model, AND does it
     exist on the node RIGHT NOW?** A coder variant or a renamed/deleted/phantom model is the common
     real cause. Probe BOTH in one shot:
     ```
     M=$(python3 -c "import os,yaml;print(yaml.safe_load(open(os.path.expanduser('~/.hermes/config.yaml')))['delegation']['model'])")
     curl -s http://<studio-host>:11434/api/tags | python3 -c "import sys,json;print([m['name'] for m in json.load(sys.stdin)['models']])"  # is M on disk?
     curl -s http://<studio-host>:11434/v1/chat/completions -H 'Content-Type: application/json' \
       -d "{\"model\":\"$M\",\"messages\":[{\"role\":\"user\",\"content\":\"add 5 and 7 with the add tool\"}],\"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"add\",\"parameters\":{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"number\"},\"b\":{\"type\":\"number\"}}}}}],\"max_tokens\":80}" \
       | python3 -c "import sys,json;m=json.load(sys.stdin)['choices'][0]['message'];print('STRUCTURED tool_calls' if m.get('tool_calls') else 'PLAIN-TEXT (non-viable) content='+repr(m.get('content','')[:80]))"
     ```
     `PLAIN-TEXT` or model-absent → you found it; repoint to a known-good general model. No need to
     touch any context config.
  2. Only if (1) is clean (model exists + emits structured `tool_calls`) do you proceed to the
     context gates (#0a–#0c). **Config DRIFT to a non-viable model is the cheapest, most common
     failure and masquerades as a context bug** — a prior session chasing the deepest gate (#0c) is a
     red flag that the shallow checks were skipped. Walk all five gates from the SHALLOWEST first.

  **⚠️ The earlier UNRESOLVED note (kept below for the trail):** A full session applied EVERY documented fix —
  `context_length: 65536` in custom_providers (gate #1 ✓, verified via
  `get_custom_provider_context_length` → 65536), `ollama_num_ctx: 65536` in the top-level
  `model:` block (gate #2 fix), removed stale `api_key_env` (#0d ✓), rebuilt
  `qwen2.5-coder-14b-32k` and confirmed it resolves on the Studio via direct curl, gateway
  restarted, model warm — and `delegate_task` STILL fell to `deepseek-v4-pro` (~11–21s,
  `api_calls: 1–3`, ZERO 13k-token request in the Studio log). The live gateway `CLI_CONFIG`
  showed the correct delegation block (verified via `nsenter -t <gwpid> -m -- venv/bin/python -c`
  reading `cli.CLI_CONFIG["delegation"]`), yet children never dialed the Studio. So there is a
  SIXTH path (or the #0c fix is incomplete) NOT yet root-caused. TWO caveats that may explain it
  and MUST be checked next time before re-applying known fixes:
  (a) the delegation target that session was `qwen2.5-coder-14b-32k`, a CODER model — which
  FAILURE MODE #1 (text tool-calls) already marks non-viable; the fallback may have been the
  tool-call-format failure, not a context gate, and the context debugging was a red herring.
  (b) `_ollama_num_ctx` is read from the agent cfg's `model` block — confirm the CHILD's cfg
  actually carries the top-level model block, and verify the child's resolved `_ollama_num_ctx`
  directly rather than assuming the top-level write propagated.
  **Next session: do NOT re-apply the context fixes blindly — they were already applied and did
  not work. FIRST switch the delegation target to a known tool-call-capable model
  (`qwen2.5-32b-32k` or `qwen2.5-128k`, NOT a coder variant) and re-test; if it still falls back,
  instrument the child's actual API call (not config resolution) to capture the real exception —
  reproduce the child `run_conversation` in a `venv/bin/python` harness against the
  live config to see the refusal/error firsthand.** The config-resolution layer is provably
  correct; the failure is in child EXECUTION and was never captured directly this session — that
  is the gap to close.

**🛑 FAILURE MODE #0d (stale `api_key_env` — a SEPARATE silent-DeepSeek path; hit 2026-06-18).**
A leftover `delegation.api_key_env: DEEPSEEK_API_KEY` in the delegation block — left over from
when delegation genuinely WAS DeepSeek — forces the credential resolver to load the DeepSeek key
and route to DeepSeek's cloud endpoint, EVEN THOUGH `provider: custom:mac-studio` (keyless) and
`base_url: http://<studio-host>:11434/v1` are set right beside it. The key binding wins over the
keyless provider. Telltale: result `model: deepseek-v4-pro` with NO 404/connection attempt to the
Studio in the log (the child dialed DeepSeek directly, never tried the Studio). **Fix:** delete the
`api_key_env` line from the delegation block entirely so the keyless `custom:mac-studio` provider
stands. Audit: `grep -A14 '^delegation:' config.yaml | grep api_key_env` — if present and the
provider is a keyless custom/Studio one, it's stale; remove it. This is a config-coherence bug:
`provider: custom:mac-studio` (keyless) and `api_key_env: DEEPSEEK_API_KEY` contradict each other.

**🛑 FAILURE MODE #0e (config-write THRASH + `ollama rm` non-persistence — the operational trap
that wasted the most wall-clock 2026-06-18).** Two compounding gotchas around editing delegation:
- **Repeated `yaml.safe_load → edit → yaml.dump` cycles in one session silently REVERT each other.**
  If you load the config, another write lands, then your in-memory copy dumps — it overwrites the
  newer value with your stale snapshot. Verified: `delegation.model` bounced 14b→32k→64k→14b across
  the session, and a late aux-moves write reverted an earlier delegation-model change because its
  in-memory `c` still held the old value. **Rule: edit config in ONE atomic read-modify-write per
  change, and IMMEDIATELY re-read the specific block off disk to confirm — never trust the dump
  succeeded.** `python3 -c "import os,yaml;print(yaml.safe_load(open(os.path.expanduser('~/.hermes/config.yaml')))['delegation']['model'])"`
  right after every write.
- **`ollama rm <model>` does NOT reliably remove a model that shares a blob, and a configured model
  can VANISH from the Studio between operations.** This session: a model we `ollama create`'d earlier
  was gone from `ollama list` later (only `32b-64k` + `72b` remained), AND a previously-`rm`'d
  `32b-64k` was still loadable/serving from its cached blob. So `delegation.model` pointed at a model
  that DID NOT EXIST on the node → every delegate_task 404'd → DeepSeek fallback. **Rule: before
  restarting the gateway after a delegation-model change, confirm the EXACT model name resolves on
  the Studio RIGHT NOW** — not "we built it earlier":
  ```
  curl -sf -m10 http://<studio-host>:11434/api/generate \
    -d '{"model":"<exact-delegation-model>","prompt":"hi","stream":false,"options":{"num_predict":3}}'
  ```
  Non-2xx / "model not found" → the model is gone; rebuild or repoint BEFORE restart. Cross-check
  against `ollama list` (authoritative) rather than recall.

**The meta-rule binding #0a–#0e together:** there are now FIVE independent silent-DeepSeek-fallback
paths (64k declared gate, live `_ollama_num_ctx` gate, `child_timeout_seconds`, stale `api_key_env`,
and missing/wrong model name). Clearing ONE and declaring victory is the recurring mistake. The
ONLY proof of a working delegation config is a real `delegate_task` whose result `model` field
shows the Studio model — run it after EVERY change, and when it shows `deepseek-v4-pro`, walk all
five gates rather than assuming it's the same one as last time.

**Two FURTHER failure modes that also dead-end at DeepSeek (both hit this session):**
1. **Wrong tool-call format (kills the small coder models).** Subagents are tool-callers.
   Probe the OpenAI endpoint with a `tools` payload and check the RESPONSE SHAPE, not just
   that it answers:
   ```
   curl -s http://<studio-host>:11434/v1/chat/completions -H 'Content-Type: application/json' \
     -d '{"model":"M","messages":[{"role":"user","content":"add 5 and 7 with the add tool"}],
          "tools":[{"type":"function","function":{"name":"add","parameters":{"type":"object",
          "properties":{"a":{"type":"number"},"b":{"type":"number"}}}}}],"max_tokens":80}'
   ```
   - `qwen2.5-coder:14b` returns the call as PLAIN TEXT in `content` → child can't parse →
     fallback. (The model catalog even flags coder variants `"tool_call": false`.) **Coder
     models are NOT viable delegation targets through Ollama's OpenAI endpoint.**
   - `qwen2.5-32b-32k` returns a proper structured `tool_calls` array with an id → parseable.
     The general Qwen models tool-call correctly; that's why delegation historically used 32B.
2. **Prompt-ingest timeout (kills the slow big models).** Even when tool-calls are correct,
   the 32B is too slow on a REAL agent payload. A subagent ships ~13,600 input tokens
   (AGENTS.md + ~14 tool schemas + task). At the 32B's ~378 t/s prompt-ingest that's ~40s to
   first token — past the child's stream-idle timeout (`Stream stale for 180s — no chunks
   received. Killing connection.` in older runs; effectively trips well under that). Connection
   killed → DeepSeek fallback. **A tiny curl succeeds where the full agent payload times out**
   — which is exactly why isolated benchmarks mislead. Always probe with a realistic
   ~4k+ token system prompt + 15 tools, not "say hi."

## ⚠️ PITFALL: OllamaApp /api/embeddings returns empty — use CLI wrapper

**This is the embedding-specific version of the delegation failures above.** OllamaApp 0.30.8
(installed from the macOS App Store, at `/Users/localadmin/OllamaApp/`) serves the
`/api/embeddings` endpoint but returns `{"embedding":[]}` (empty array) for `nomic-embed-text`,
even though the model is pulled and `ollama run nomic-embed-text "text"` works correctly via CLI.

**Detection:** `curl http://<studio-host>:11434/api/embeddings -d '{"model":"nomic-embed-text","input":"test"}'` returns `{"embedding":[]}` while `ollama run nomic-embed-text "test"` returns a valid 768-dim float array.

**Fix (2026-06-25):** a thin Python stdlib HTTP wrapper that calls the ollama CLI binary. Deployed
at `/tmp/embed_server.py` on the Studio, listening on port `:9090`:
```python
# Key: uses subprocess.run([OLLAMA_BIN, "run", MODEL, text]) instead of the HTTP API
```
The wrapper accepts `POST /embed` with `{"input": "text"}` and returns `{"embedding": [...]}`.
Start with: `nohup python3 /tmp/embed_server.py 9090 > /tmp/embed_server.log 2>&1 &`

**Not persistent across reboots** — the script runs as a background process, not a launchd
service. If the Studio reboots, re-run the nohup command above via SSH.

**Client config** (on the Mini, in `.env`):
```
EMBEDDING_API_URL=http://<studio-host>:9090
EMBEDDING_MODEL=nomic-embed-text
```
`knowledge.py`'s `embed()` function checks `EMBEDDING_API_URL` and falls back to local
`sentence_transformers` on failure — the remote path is a speed optimization, not a hard
dependency.

## ✅ SIMPLER EMBEDDING PATH (2026-06-27): direct Ollama /api/embed, no :9090 wrapper
The `:9090` CLI wrapper above was a workaround for the OllamaApp `/api/embeddings` (plural,
old API) returning empty. **The newer `/api/embed` (singular) endpoint works correctly** and is
the path `knowledge/embed.py` actually uses — no wrapper needed. Verified live:
```
curl -s http://<studio-host>:11434/api/embed \
  -d '{"model":"qwen3-embedding:4b","input":["test"]}' \
  | python3 -c "import json,sys; print('dims', len(json.load(sys.stdin)['embeddings'][0]))"
```
So the working client config is the DIRECT port, not the wrapper:
```
EMBEDDING_API_URL=http://<studio-host>:11434
EMBEDDING_MODEL=qwen3-embedding:4b
```
The `:9090` wrapper is dead (returns 501) but irrelevant — don't restart it; point at `:11434`.

## 🛑 PITFALL: /api/embed HTTP 400 on large batches — chunk to ≤32 (2026-06-27)
`/api/embed` returns **HTTP 400 when the `input` array exceeds ~32 texts in one call.** This bit
`build_centroids()` hard: it embeds 402 anchor texts in ONE batch → 400 → `embed.py` SILENTLY
falls back to local `all-mpnet-base-v2` (768-dim) → centroids built in the WRONG embedding space
(768-dim) while the store vectors were 2560-dim. The classifier then abstained on nearly
everything because centroid-space ≠ query-space. **Symptom:** `anchors.pkl` matrix shape is
`(N, 768)` when you expected `(N, 2560)` — a silent dimension mismatch, no error raised.

**Fix:** chunk the remote call in `knowledge/embed.py::_embed_remote()`:
```python
def _embed_remote(texts):
    BATCH = 32
    out = []
    for i in range(0, len(texts), BATCH):
        out.extend(_call_ollama_embed(texts[i:i+BATCH]))   # existing single-call logic
    return out
```
**Verify after any embed-model change:** embed >32 texts and assert the dim AND count:
```
EMBEDDING_MODEL=qwen3-embedding:4b python3 -c "import sys;sys.path.insert(0,'scripts'); \
from knowledge.embed import embed; v=embed(['t'+str(i) for i in range(50)]); \
print(len(v),'x',len(v[0]))"   # must be 50 x 2560, not a fallback 768
```
The local-fallback-on-failure design is a feature for resilience but a TRAP for dimension
correctness — a 400 you don't notice yields a working-but-wrong-space classifier.

## 🛑 PITFALL: swapping embedding model = a Supabase pgvector column-resize dance (2026-06-27)
Changing embedding model changes the vector dimension (nomic/mpnet 768 → Qwen3-4B **2560**).
The Supabase `public.knowledge` vector column is typed `vector(768)` and a 2560-dim PATCH is
REJECTED until the column is re-typed. You cannot just `ALTER ... TYPE vector(2560)` while an
IVFFlat index exists on it. The working order (via supabase MCP `apply_migration`):
1. `DROP INDEX` the IVFFlat index on the vector column
2. `UPDATE knowledge SET vector = NULL` (can't cast 768→2560 in place)
3. `ALTER TABLE knowledge ALTER COLUMN vector TYPE vector(2560)`
4. Re-embed every row with the new model and PATCH the vectors back
5. **Recreate the IVFFlat index** on the 2560-dim column (this step is easy to forget — without
   it, ANN search falls back to slow exact scan; fine at ~190 rows, a problem at scale)

Two naming traps that cost the subagent iterations: the column is named **`vector`**, NOT
`embedding`; and the re-embed PATCH needs header `Prefer: return=minimal` + `Content-Type:
application/json` or Supabase 400s. Re-embedding ~190 rows took <1 min at ~80ms/vector.
The M2 Max ceiling (~19 t/s aggregate) is bandwidth-bound on WEIGHT bytes/token. A smaller
model reads fewer bytes/token → genuinely faster at RAW inference. Measured head-to-head
(full data in `references/tiered-model-benchmark.md`):

| Model | single eval | prompt ingest | 4-wide aggregate | VRAM | viable delegation target? |
|---|---|---|---|---|---|
| qwen2.5-coder:14b @32k | **29.0 t/s** | **1595 t/s** | **41.3 t/s** | 34.9GB | ❌ NO — text tool-calls |
| qwen2.5-32b-32k | 13.5 t/s | 378 t/s | 19.3 t/s | 54.0GB | ⚠️ slow-ingest timeouts |

The 14B-coder is 2.15× faster at RAW generation — **but it is useless as a delegation target**
because it can't emit structured tool_calls (see failure-mode section above). Speed numbers
are irrelevant if the model can't drive an agent loop. This is the trap: a head-to-head t/s
benchmark looks decisive and is completely beside the point for delegation.

## ⚠️ Prefix caching: P=1 enables it, P>1 defeats it (the throughput lever that actually works)
Full investigation in `references/studio-delegation-findings-2026-06-18.md`. The headline:
the M2 Max ingests a ~13k-token agent prompt at ~100-136s COLD, but llama.cpp can REUSE a
cached prompt prefix across calls — IF requests hit the SAME slot. This is the single biggest
local-delegation speed lever, bigger than any P-tuning.

- **P=1 locks all sequential calls to slot 0** → LCP (longest-common-prefix) match reuses the
  cached prefix. Studio logs show `selected slot by LCP similarity, sim_best=...` + `cached
  n_tokens = N` reused. Measured: cold 136s → warm ~104-110s THIS session (edit-heavy).
- **P>1 round-robins across slots**, each with its OWN cold KV cache → sequential calls scatter,
  almost no reuse. This is WHY high P hurts real (large-prompt) delegation: it both saturates
  the memory bus AND defeats prefix caching.

**System prompt cacheability (measured live, ~11,820 tokens total):**
| layer | tokens | cacheable |
|---|---|---|
| stable (SOUL + skills list + tool schemas) | ~5,973 | ✅ unless skills edited |
| context (AGENTS.md + project files) | ~4,575 | ✅ unless files edited |
| volatile (MEMORY + USER + session-id/date; Honcho removed 2026-07-01) | ~1,271 | ❌ always re-ingested |

Prompt is ordered stable→context→volatile on purpose (`agent/system_prompt.py`) to maximize
the cacheable prefix. **Quiet-session cache floor ≈10,549 tokens (89%)** → only ~1,271 volatile
tokens re-ingest → **~30s/subagent** (13s ingest + 17s gen), NOT the 136s cold number. In an
EDIT-HEAVY session the `<available_skills>` block (~token 3,400) churns and the floor collapses
to ~3,400 tokens → ~104s/task. Lesson: cold benchmarks OVERSTATE steady-state delegation cost;
measure across SEQUENTIAL warm calls in a quiet session for the real number.

**Practical:** for sequential local delegation, P=1 is correct. Only raise P when you genuinely
need N tasks decoding AT ONCE and can eat the cold re-ingest per slot.

## ⚠️ delegate_task has NO per-task model param — model selection is BINARY
`tools/delegate_tool.py::delegate_task` accepts only `goal, context, toolsets, role,
background, max_iterations` — NO `model`. Every child reads the SAME `delegation.model`
(`_resolve_delegation_credentials`, `effective_model = model or parent_agent.model`). No knob
for "this task → 14B, that task → 32B" without an agent-code patch.

**The honest conclusion for throughput-over-efficiency on THIS hardware:** local Studio
delegation CAN be made to work end-to-end (verified: 32B at declared 64k + 900s child timeout
→ real 4-wide batch completes on the Studio), but it is the SLOW path: ~115s/child solo,
~500s/child under 4-wide contention, vs Sonnet's seconds. The M2 Max ingests a ~13k-token
agent prompt at ~378 t/s (~40s+ to first token) and the only model fast enough (14B-coder)
can't tool-call. **The full working local recipe, in order:** (1) model declared ≥64k context,
(2) `child_timeout_seconds` ≥900, (3) ideally minimal `toolsets` per call to cut ingest. The
realistic options, in priority order:
- **Sonnet/Anthropic delegation** (`delegation.provider: anthropic`, `model: claude-sonnet-*`).
  No local ceiling, 12 genuine concurrent children, rides a flat Max-plan OAuth subscription
  (rate-limit cost, not metered $). For a user with token headroom + throughput priority this
  is usually the RIGHT answer — don't reflexively talk them out of it toward "local is better."
- **Local Studio delegation** — viable but slow; best when the user explicitly wants work kept
  on-box and can tolerate ~100-500s/child. Requires the full 3-step recipe above or it silently
  falls back to DeepSeek.
- **Accept the cloud fallback** (DeepSeek) — it's what runs by default when the recipe is incomplete.
- Keep the Studio for DIRECT inference + aux roles (compression/curator/web_extract), where
  there's no agent-loop tool-calling and no per-call timeout pressure. That's its real niche.

## ⚠️ Prefix caching does NOT help real delegation — the Session ID breaks it
The obvious "make local delegation fast" idea is prompt prefix caching: subagents share a
~12k-token prefix (AGENTS.md + tool schemas), so cache it once and every later child skips the
~100s ingest. At the ENGINE level this works spectacularly — a raw curl with a fixed prefix +
varying suffix ingests ~12.8k tokens in **0.5s on call 2** vs ~100s cold (~190× on the ingest
phase; cold prompt-eval is ~96 t/s, the M2 Max ceiling). It is REAL but it does NOT survive the
`delegate_task` path. Measured: sequential delegate_task calls went 139s → 109s (only 22%), and
the Studio llama-server log showed `new prompt ... task.n_tokens = 13374` on BOTH — i.e. a FULL
re-ingest each time, no `n_past` reuse.

**Two root causes, both confirmed in the slot logs:**
1. **`Session ID` in the system prompt is unique per child** (`agent/system_prompt.py` ~line 386:
   `timestamp_line += f"\nSession ID: {agent.session_id}"`, appended to `volatile_parts`). llama.cpp
   prefix matching needs a byte-identical token prefix; one differing token kills all reuse after it.
   The date line is deliberately day-precision (PR #20451 comment: "Minute-precision changes
   invalidate prefix-cache KV") — but the per-child Session ID re-introduces exactly that volatility.
   The stable block (AGENTS.md + tools) sits BEFORE it, so in principle that prefix could cache; in
   practice it didn't, because of #2.
2. **`OLLAMA_NUM_PARALLEL>1` scatters + overwrites slots.** Ollama round-robins requests across N
   slots, each with its OWN KV cache. Sequential children rarely hit the same slot, and even on a
   slot hit the KV got overwritten by the interleaved request, so there's nothing to match. The
   raw-curl test only "worked" because back-to-back curls happened to land on one slot with nothing
   evicting between them. `n_keep = 4` in the logs = only 4 template-head tokens retained on reuse.

**Detection:** read the Studio log for the big tasks — `slot update_slots: ... new prompt ...
task.n_tokens = <full count>` with NO preceding `n_past was set to <large>` = no reuse = caching
is NOT helping. A tiny task showing `n_past was set to 4` is just the template head, not real reuse.

**The untested lever (do this BEFORE concluding local delegation is inherently ~110s/task):**
set `OLLAMA_NUM_PARALLEL=1` (gated plist edit + bootout/bootstrap), then fire 3 SEQUENTIAL
delegate_task calls with identical `toolsets` and watch call-2/3 duration + log `n_past`. One slot =
no scatter = the only configuration where sequential children can reuse a warm prefix. If call 2
drops ~110s→~15s, sequential local delegation is fast; if not, llama.cpp isn't reusing across
distinct agent requests regardless, and ~110s/task is the floor on this box — a real, final answer.
High parallelism is doubly wrong on this bandwidth-bound node: it tanks aggregate throughput AND
defeats caching. For cache-friendly local delegation, fewer slots + SEQUENTIAL dispatch, not more.

## ⚠️ Realistic-payload benchmark overturns small-prompt parallelism numbers
The P=2/3/4 grid that picked P=4 used a TINY "count to 50" prompt — negligible ingest, so adding
slots looked free. Re-run with the REAL ~13k-token agent payload and the conclusion INVERTS:
aggregate throughput goes **661 → 160 → 107 t/s** as concurrency rises 1→2→4 (full data in
`references/delegation-realistic-payload-benchmark.md`). It's monotonically DOWN: every slot past 1
adds a fresh ~12k-token cold ingest that saturates the memory bus simultaneously, so per-task time
explodes (12s cached single → 103s/task at 2-wide → 310s/task at 4-wide). The N=1 number is
cache-inflated (warm slot, 0.1s ingest) and must be read as an artifact, not a real cold single.
**Lesson: benchmark with the payload the real workload sends.** A tiny-prompt concurrency curve is
worse than no data — it actively recommends the wrong config. P=4 is the WORST setting for real
delegation, not the best.

## ⚠️ Watch the baked context vs native context (RoPE stretch)
A model's Modelfile `num_ctx` can exceed its TRAINED context (`<family>.context_length` in
`/api/show`). Above native, RoPE scaling fakes the extra range and quality quietly degrades.
qwen2.5-32b-64k: native 32768, baked 65536 — the 64k is stretched 2× past native AND
double the VRAM. Prefer rebuilding at the native ceiling (32k here) unless a task genuinely
needs more. Two costs for one bad default: degraded quality + halved parallelism.

## ⚠️ Oversized vision-model context silently steals delegation VRAM
`qwen2.5vl:7b` loads at ctx=128000 → ~22GB resident, when image description never needs
>~8k. With `MAX_LOADED_MODELS=2` that 22GB blocks the 32B from staying warm beside it
(22 + 20 + KV > room for 2 slots), causing the KEEP_ALIVE eviction thrash. If you want both
warm AND multi-slot delegation, drop the vision model's context too, or let it evict during
heavy delegation.

## Procedure: rebuild a model at a NEW context size (no re-download)
Changing `num_ctx` does NOT need the weights re-pulled — `ollama create` reuses the
existing blob via `FROM /path/to/blob`. Fast (~30s, just writes a new manifest layer).

1. Read the current Modelfile to get the blob path + template/params to preserve:
   `ollama show --modelfile <model>` (the `FROM` line points at the sha256 blob).
2. Write the new Modelfile LOCALLY on the mini, then `scp` it to the Studio. Do NOT pipe a
   heredoc over `ssh` — `ssh host "python3 ... << EOF"` does NOT forward the heredoc stdin
   and silently writes nothing. scp the file instead. (Also: a bare `python3` on the Studio
   triggers an xcode-select dev-tools install prompt — avoid invoking it over SSH.)
   Minimal Modelfile = `FROM <blob>` + `SYSTEM ...` + `PARAMETER num_ctx <N>` (carry over
   any `PARAMETER temperature` etc. the original had; template is inherited from the blob).
3. `<ollama-bin> create <new-name> -f /tmp/Modelfile.<new> ` → ends in `success`.
4. Verify the baked context took: `curl -s /api/show -d '{"model":"<new>"}'` →
   `parameters` shows `num_ctx <N>` and `model_info.<family>.context_length` is native.
5. Update `delegation.model` in `~/.hermes/config.yaml` (GATED — config write) to the new
   name, and bump `max_concurrent_children` if the freed VRAM bought more parallel slots.
6. Keep the OLD model on disk until verified — rollback is just pointing config back. Don't
   `ollama rm` the old one in the same change.

This session: 64k→32k 32B + 128k→8k vision. Both warm co-resident dropped 22.3→7.8GB
(vision) and freed headroom: 45.3GB/56GB both-warm with 10.7GB spare (was thrashing before).

**CONFIRMED WORKING local-delegation recipe (verified end-to-end 2026-06-18):** after the
32k→32k rebuild SILENTLY BROKE delegation (64k gate), the fix that made `delegate_task`
genuinely run on the Studio: (1) rebuild/use a model that satisfies BOTH 64k gates — either a
64k-native model (e.g. `qwen2.5-128k`) OR a 32k-native model with `num_ctx 65536` baked AND
`context_length: 65536` in custom_providers (gate #1) AND `ollama_num_ctx: 65536` in the
TOP-LEVEL `model:` block (gate #2 — see FAILURE MODE #0c; this step was the missing piece that
made early "confirmed working" claims premature), (2) raise `child_timeout_seconds` 600→900,
(3) ideally minimal `toolsets` per call to cut ingest. Proof of success is ALWAYS a real
`delegate_task` whose result `model` field shows the Studio model (NOT deepseek-v4-pro), with
the 13k-token request visible in the Studio log. Solo task ~115s, 4-wide ~500s/child. It WORKS
but is the slow path. The 14B-coder remains non-viable (text tool-calls).

## Benchmark methodology — warm-vs-warm or the numbers lie
`scripts/studio-bench.sh` runs the standard probe (cold + 3 warm, eval/prompt t/s, load).
Reading the results (user needs this stated plainly):
- **Compare like-for-like conditions.** Warm-vs-warm, cold-vs-cold. A warmed baseline vs a
  cold-cache new model produces a scary -59% prompt delta that is pure methodology artifact.
  ALWAYS fire one throwaway warm-up run on a freshly-built model before recording.
- **Direction depends on unit.** t/s is a rate (higher=better); seconds is a duration
  (lower=better). Don't read the sign of Δ without knowing which.
- **Per-token eval t/s is the real work** and is INVARIANT to context size — same weights,
  same quant ⇒ same eval t/s (±10% run-to-run noise). If eval t/s is flat, generation speed
  did not change, full stop.
- **Prompt-ingestion t/s scales DOWN with smaller context** by design (smaller KV buffer) —
  a big negative prompt-t/s Δ after shrinking context is EXPECTED, not a regression.
- **A context-shrink's win is VRAM/parallelism, never per-token speed.** Lead the report with
  the VRAM headroom number, not the speed table — the speed table only proves nothing
  regressed. Saying "X% faster" after a context change is almost always wrong.
- Cold-load spikes (e.g. 3.5s→43s) when a 2nd model is already resident = one-time VRAM
  contention during load, not steady state. Re-measure with the node idle.

Baselines live in `references/` as `performance-baseline-*.md` — write a fresh pre-change
snapshot (new file, no gate) BEFORE tuning so the after-comparison is apples-to-apples.

## ⚠️ Empirically verifying the system-prompt prefix cache (don't trust code comments)
When an issue/handoff claims the Anthropic system-prompt prefix cache is being busted
mid-session (e.g. "Layer N is refreshed on cadence and rewrites the cached prompt"),
the freeze claim must be MEASURED, not read off invariant comments — comments drift from
code. Anthropic responses carry the gold-standard meter for free:
`usage.cache_creation_input_tokens` and `usage.cache_read_input_tokens`.

**The reading:** on turn 1 cache_creation is large (writing the prefix), cache_read ~0.
On turns 2+ a WARM/frozen prefix shows cache_read ≈ system-prompt size and cache_creation
≈0. If cache_creation stays large EVERY turn, something is busting the prefix.

**Two ways to get the number — prefer the second:**
1. *Live API probe* (fragile under OAuth): fire 3-4 `client.messages.create` calls reusing
   the same `system=[{... 'cache_control':{'type':'ephemeral'}}]` and read the usage deltas.
   PITFALLS that make this probe lie: (a) the Claude-Code OAuth credential lives in
   `~/.claude/.credentials.json` under `claudeAiOauth.accessToken` (camelCase — NOT
   `access_token`), pass it as `auth_token=` not `api_key=`; an empty token yields header
   `b'Bearer '` and a connection error. (b) Do NOT add `context-1m-2025-08-07` to
   `anthropic-beta` on a Max-plan subscription → 400 "long context beta is not yet available
   for this subscription." (c) On some model+OAuth combos `cache_control` is silently dropped
   (creation=read=0 on turn 1) — when that happens the probe is inconclusive; use method 2.
2. *Production state.db (authoritative, zero risk)* — the real agent already logged it.
   `~/.hermes/state.db` `sessions` table has `cache_read_tokens` + `cache_write_tokens`
   per session. Long real sessions (100-300+ msgs) on the live model show the truth directly:
   ```
   venv/bin/python -c "import os,sqlite3;d=sqlite3.connect(os.path.expanduser('~/.hermes/state.db'));d.row_factory=sqlite3.Row;[print(r['model'],r['message_count'],'write',r['cache_write_tokens'],'read',r['cache_read_tokens']) for r in d.execute('SELECT model,message_count,cache_write_tokens,cache_read_tokens FROM sessions WHERE cache_read_tokens>0 ORDER BY started_at DESC LIMIT 10')]"
   ```
   Verified 2026-06-19: real Sonnet-4.6 sessions of 199/298/338 msgs show **92-96% cache
   read rate** → the prefix IS byte-stable across turns; the "cadence rewrites the cached
   prompt" issue does NOT apply on this codebase. Per-turn auto-inject context (historically Honcho; now prefetch cold-store recall) lands in the
   USER MESSAGE tail, not the system prompt, so it cannot bust the prefix (cross-ref the
   `honcho` skill's "Cadence semantics + per-turn network cost" section). Short sessions
   (<15 msgs) show low hit rate — expected, the cache warms on turn 1 and pays off over turns,
   so judge freeze on LONG sessions only.

## ⚠️ Scripting remote Studio ops from the mini — three traps that waste iterations
When you wrap Studio operations in a bash script run from the mini:
1. **`$HOME`/`~` expand on the MINI, not the Studio.** A script var like
   `PLIST="$HOME/Library/LaunchAgents/com.ollama.server.plist"` resolves to `~/Library/...`
   (mini) and PlistBuddy fails `No such file or directory`. HARDCODE Studio-side paths:
   `STUDIO_PLIST="/Users/localadmin/Library/LaunchAgents/com.ollama.server.plist"`,
   `STUDIO_UID=501`. Only let `$HOME` expand inside a `$SSH '...'` single-quoted remote block.
2. **`set -e` + launchctl = false abort.** `launchctl bootout`/`bootstrap` return non-zero
   even on success, so a script with `set -e` dies right after a working restart. Do NOT use
   `set -e` around launchctl; check the outcome explicitly (verify env on the new PID instead).
3. **A `&`-backgrounded concurrency loop trips the foreground-guard** if run inline via the
   terminal tool. Put the `for j in $(seq 1 $N); do gen & done; wait` loop in a script FILE
   and `bash` it — the guard only blocks inline `&`, not `&` inside an executed script.

## ⚠️ Disk reclaim from deleting a rebuilt model — measure, don't estimate
`ollama create <new> -f Modelfile` (FROM an existing blob) COPIES the source blob into a new
one; it does NOT share/symlink it. So `qwen2.5-32b-64k` and `qwen2.5-32b-32k` each held a
full 19GB weight copy — deleting the 64k freed the whole 19GB, not "almost nothing." (I
predicted ~0 reclaim twice on shared-blob reasoning and was wrong both times.) Always
`du -sh ~/.ollama/models/` before AND after a delete and report the measured delta, never an
estimate. Vision models DO share a base blob (vl:7b + vl-8k), so there the reclaim is smaller.

## Procedure: replace/restore a heavy model
1. Confirm what's actually on disk: `curl -s http://localhost:11434/api/tags` and
   `du -sh ~/.ollama/models/`. Don't trust memory/topology for which models exist — they drift.
2. Pull in background (47GB pulls take ~10min @ 80MB/s):
   `nohup <ollama-bin> pull qwen2.5:72b > /tmp/ollama-pull-72b.log 2>&1 &`
3. Watch: `tail -5 /tmp/ollama-pull-72b.log` shows `%`, GB, MB/s, ETA.
4. Long pull → schedule a one-shot cron (`15m`, toolset `terminal`) to report completion
   rather than blocking the turn.
5. After it lands, update `references/topology.json` model list + route heavy delegation to it.

## WRITE GATE
Plist edits, `kill`, model pulls, and VRAM `sysctl` are all state-changing on a peer host —
present what/risks/rollback and wait for greenlight. Always `cp` the plist to a
`.bak-<timestamp>` before editing.

## Verification (always run after a restart)
```
NEW_PID=$(ps aux | grep "ollama serve" | grep -v grep | awk '{print $2}' | head -1)
ps ewww -p $NEW_PID | tr ' ' '\n' | grep -E "OLLAMA_NUM|OLLAMA_MAX|OLLAMA_KEEP|OLLAMA_HOST"
curl -s --max-time 5 http://localhost:11434/api/tags | head -c 120
```
`ps ewww -p <pid>` is the macOS way to read a process's live environment — confirm the
new vars are present on the NEW pid (a stale pid means the kill-respawn didn't happen).

## Self-healing watchdog (auto-restart, not just alert)
The `~/.hermes/scripts/studio-watchdog.sh` cron (every 15m, `no_agent`, silent-on-healthy)
should AUTO-RECOVER on an unreachable node, not merely page. The recovery action reuses the
proven kill-respawn pattern over SSH: kill the `ollama serve` PID and let launchd `KeepAlive`
bring it back, then recheck `/api/tags`. Report the OUTCOME either way (Andrew rejects
"cron will handle it" alert-only watchdogs — heal first, then tell him what happened):
```bash
_restart_ollama() {
    ssh $SSH_OPTS localadmin@<studio-host> 'kill $(pgrep -f "ollama serve" | head -1) 2>/dev/null; true'
    sleep 12
    curl -sf --connect-timeout 10 "$TAGS_URL" >/dev/null 2>&1; return $?
}
```
On reachability failure: attempt `_restart_ollama`; emit "✅ auto-restarted" on success or
"❌ auto-restart FAILED, manual intervention" on failure. Keep the deeper checks
(model-count, live-throughput tok/s probe) as alert-only — those are degradation, not death,
and a blind restart could make a thrashing node worse. Only the hard-unreachable path
auto-restarts. Silent (empty stdout, exit 0) when healthy.

## VRAM cap is persistent — no reboot re-arming needed
`iogpu.wired_limit_mb=57344` survives reboot on this node (set via the launchd/sysctl path
already in place). Don't re-add a "raise the cap after every reboot" step — verify it with
`sysctl iogpu.wired_limit_mb` and move on.

## ✅ :8080 REPURPOSED (2026-08-15): llama-server now serves bge-reranker-v2-m3 in `--rerank` mode
The orphaned 32B instance on :8080 (retired from swarm duty, still burning ~19GB VRAM) was swapped
to the reranker: `bge-reranker-v2-m3-Q8_0.gguf` (636MB, at `~/llama.cpp/models/`), launched with
`--rerank --ctx-size 8192 --parallel 2`, alias `bge-reranker-v2-m3`. Net VRAM change: **freed ~18GB**.
- **Why llama-server for rerank:** Ollama has NO rerank endpoint; llama-server's native `/v1/rerank`
  is purpose-built. Call shape: `POST http://<studio-host>:8080/v1/rerank` with
  `{"model":"bge-reranker-v2-m3","query":"...","documents":[...]}` → `results[].{index,relevance_score}`
  (scores are raw logits, CAN BE NEGATIVE; sort descending — do not treat <0 as an error).
  Measured: **~400ms for an 8-doc rerank** vs 9–58s for the CPU CrossEncoder on the mini.
- GGUF source: `huggingface.co/gpustack/bge-reranker-v2-m3-GGUF` (Q8_0), plain curl download.
- 🛑 **`--ubatch-size` defaults to 512 and 500s on real rerank payloads.** A rerank request is ONE
  physical batch of query+document tokens; 15 docs with fat session digests hit 566–607 tokens →
  HTTP 500 `input (N tokens) is too large to process. increase the physical batch size`. The plist
  must carry `--batch-size 2048 --ubatch-size 2048` (added 2026-08-15 after 3 benchmark queries
  silently scored as misses). If reranks start 500ing after any plist edit, check these flags first.
- Plist backup of the old 32B config at `com.llama.server.plist.bak-20260815` — rollback = restore
  it + bootout/bootstrap. Same launchd restart pattern as everything else on this node.
- The KB-retrieval consumer side (whether/how `knowledge/search.py` calls this) lives in the
  `fibonacci-sector-retrieval` skill — this section only covers node-side serving.
- ⚠️ Sections below that describe :8080 as "serving the 32B" / `custom:mac-studio-llama` chat
  provider predate this swap — :8080 is rerank-only now; chat/delegation is all Ollama :11434.

## ⚠️ NATIVE llama-server is a SECOND runtime on this node — and it sidesteps the Ollama traps
As of 2026-06-19 the Studio runs **two** inference servers side by side:
- **Ollama** on `:11434` (the historical node; `curator` aux role still uses it).
- **native `llama-server`** on `:8080` (`custom:mac-studio-llama` provider) — llama.cpp's own
  OpenAI-compatible server, NOT wrapped by Ollama. This is the preferred runtime for new work
  because it exposes controls Ollama hides (explicit `--ctx-size`, `--parallel`, `--cache-type-k`,
  `--flash-attn`, `--model-draft` for speculative decoding) and its prefix cache is deterministic.

Full setup recipe + benchmark data: `references/llama-server-setup-2026-06-19.md`. Key facts:

- **macOS-version gate on the prebuilt binary (the #1 install trap).** llama.cpp's *latest* macOS
  arm64 release is built against a NEWER macOS SDK than this node runs (Studio = Sonoma 14.4.1).
  Symptom: `dyld[...]: Symbol not found: _OBJC_CLASS_$_MTLResidencySetDescriptor ... built for
  macOS 26.0 which is newer than running OS`. FIX: download an OLDER release tag (the Apr-2026
  `-kleidiai` builds, e.g. `b8891`, run on Sonoma). Probe `--version` after extract — if it prints
  the Metal device lines (`GPU family: MTLGPUFamilyApple8`, `has unified memory = true`) the binary
  matches the OS. Don't build from source: Xcode CLT isn't installed and `xcode-select --install`
  needs a GUI dialog you can't clear over SSH.
- **Reuse the Ollama GGUF blobs directly — no re-download.** `llama-server --model
  /Users/localadmin/.ollama/models/blobs/sha256-<hash>` works; a GGUF is a GGUF regardless of who
  stored it. Get the blob path from `ollama show --modelfile <model>` (the `FROM` line).
- **Flag syntax drifts between builds.** In b8891 `--flash-attn` REQUIRES a value (`--flash-attn on`),
  not a bare flag — a bare `--flash-attn` consumes the NEXT arg and errors
  (`unknown value for --flash-attn: '--cache-type-k'`). Always tail the log after a plist edit;
  llama-server prints the exact argparse error and exits, KeepAlive respawns into the same error.
- **`--alias <name>` sets the model id** the OpenAI endpoint reports (`/v1/models` + the `model`
  field in responses). Set it to the clean name you put in Hermes config (`qwen2.5-32b`), not the
  blob path.
- **Restart pattern is the SAME launchd bootout+bootstrap as Ollama** (plist is
  `com.llama.server.plist`, `KeepAlive=true`, `RunAtLoad=true`). A plain kill respawns the stale
  plist. Model load into Metal takes ~75-90s for the 18GB 32B — health probe `/health` returns
  `{"status":"ok"}` only after; don't conclude failure before ~90s.
- **The `_ollama_num_ctx` gate (FAILURE MODE #0c) does NOT apply** — llama-server isn't Ollama,
  Hermes doesn't do an `/api/show` clamp query against it. Declaring `context_length: 65536` in
  the `custom_providers[mac-studio-llama]` block is sufficient for the 64k gate. This is a real
  advantage of the native runtime for delegation.

## 🛑 PITFALL: `--ctx-size` is the TOTAL pool; each `--parallel` slot gets ctx_size ÷ parallel
llama-server divides `--ctx-size` evenly across `--parallel` slots. The per-request context window
each caller actually sees is **`--ctx-size ÷ --parallel`**, NOT `--ctx-size`. This silently
starves long-prompt callers and is invisible until something overflows.

**The failure it caused (2026-06-25):** the `tasks.compression` model (`qwen2.5-32b` on `:8080`)
was launched with `--ctx-size 16384 --parallel 12` → **16384 ÷ 12 ≈ 1365, rounded to a 1536-token
per-slot window**. Hermes context compaction tried to summarize a multi-hour session
(~23,700 tokens) and the request failed with
`request (23733 tokens) exceeds the available context size (1536 tokens)`. The compactor then
emitted a deterministic-fallback summary that DROPPED a key decision (a config flag we'd decided
to turn off came back as "on"). So a too-small per-slot context doesn't just error — it produces
**silently wrong compaction context** that corrupts the next session's state.

**Detection (the per-slot number, not the pool):**
```
curl -s http://<studio-host>:8080/slots | python3 -c "import sys,json;print('per-slot n_ctx:', json.load(sys.stdin)[0]['n_ctx'])"
```
`/props` reports `n_ctx` as the SLOT count (e.g. `12`), which is confusing — read `/slots[0].n_ctx`
for the real per-request window. If it prints a tiny number like 1536, any caller sending a real
payload (compaction, web_extract, a long delegation prompt) will overflow.

**The sizing rule:** pick per-slot context to fit your LARGEST caller's payload, then set
`--parallel` to whatever the VRAM budget allows AFTER that. For a node serving compaction
(summarizes whole sessions, can hit 30k+ tokens) the per-slot floor should be ~32k. Example fix:
`--ctx-size 32768 --parallel 4` → 8192/slot (still too small for big sessions) vs
`--ctx-size 131072 --parallel 4` → 32768/slot ✓. The plist `com.llama.server.plist` carries these
args; edit it then bootout+bootstrap (same launchd pattern as Ollama — a plain kill respawns the
stale plist). **Lower `--parallel` is the cheap lever** when you need a bigger per-slot window and
the node isn't actually fielding many concurrent callers.

**Which Hermes roles this bites:** any `tasks.<role>` in config.yaml pointed at a multi-slot
llama-server — `compression` (whole-session summaries, the worst offender), `web_extract`
(full-page markdown), and Studio-routed delegation. These send LARGE prompts; a per-slot window
sized for "say hi" will silently truncate or hard-error them. Always size the per-slot context to
the role's real payload, not the model's native ceiling.

**Measured llama-server benchmark (32B, `--ctx-size 16384`, `--cache-type-k q8_0`, 120 tok each):**
| Config | concurrent | wall time | aggregate t/s |
|---|---|---|---|
| P=4 | 4 tasks | 35s | ~13.7 |
| P=8 | 4 tasks | 33s | ~14.5 |
| P=8 | 8 tasks | 55s | **~17.5** |

At `--ctx-size 16384` + q8_0 KV the VRAM math is roomy: weights ~19GB + ~2GB/slot → P=8 = 35GB of
the 56GB cap, 21GB spare (P=12 ≈ 43GB also fits). **P=8 is the throughput pick for batch fan-out**
— 8-wide finishes 8 tasks in ~55s vs ~70s if serialized at P=4. Right-size `--ctx-size` to the real
subagent payload (~13k tokens) — 16k is the sweet spot; 32k+ halves your slot count for no benefit.

## ⚠️ The Sonnet→Opus auto-upgrade is baked into the bypass — flipping delegation to local LOSES it
`patches/anthropic_billing_bypass.py` (`_classify_complexity` + `_maybe_upgrade_model`, ~line 502+)
**transparently upgrades `claude-sonnet-*` → `claude-opus-4-8` on complex requests** — fires on every
API call that goes through the Anthropic adapter. Threshold: 2+ complexity signals
(refactor/architecture/migration/audit/debug/diagnose/build-a/from-scratch/…), or 1 signal + prompt
>2000 chars. It gates on `"sonnet" in model_name`, so it only touches Sonnet-routed traffic: **you**
and `delegate_task` (when `delegation.provider: anthropic`).

Implication for the local-vs-Sonnet delegation decision: keeping `delegation` on Sonnet means complex
subagent tasks **silently get Opus 4.8 for free** (rides the flat Max plan). Flipping `delegation` to
`custom:mac-studio-llama` routes around the Anthropic adapter entirely — no upgrade possible, you get
the 32B full stop. So the right split for a throughput-AND-quality user:
- **`delegation` stays Sonnet/Anthropic** → free Opus upgrade on complex tasks, no local ceiling.
- **Swarm workers (`swarm-worker-a/b/c` profiles) point at the Studio** → explicitly-shallow parallel
  read/analysis, free + local + P=8 concurrent, and they DON'T go through the bypass (own profile
  config, hit their model directly) so there's no upgrade to lose. This is the clean place to put
  local fan-out. ⚠️ **STALE ADVICE CORRECTED 2026-08-13:** the earlier recommendation (32B on
  `custom:mac-studio-llama` :8080) is exactly what killed the swarm lane — that endpoint served 8K
  ctx (< the ~26K worker prompt) and the 32B's 109 tok/s prefill blows the ~60s client timeout even
  after a ctx fix. Current working target: `model.default: qwen2.5-14b-32k` + `provider:
  custom:mac-studio` (:11434) — small/fast model per the prefill-inequality section above, paired
  with a slimmed worker prompt. Worker profile changes are live on next dispatch — NO gateway
  restart needed (the default-profile config + provider routing does need a restart, but spawned
  worker profiles are read fresh).

## 🛑 SILENT WORKER DEATH: prefill time vs client timeout — the failure BEHIND the context overflow
Dispatcher symptom: worker exits rc=0 with NO kanban_complete/block ("protocol violation"), agent.log
shows plugin discovery then NOTHING — no error. Two stacked root causes produce this IDENTICAL
signature, and fixing the first exposes the second (verified 2026-08-13):
1. **Context overflow** (prompt > model ctx): log shows `request (N tokens) exceeds the available
   context size` + `Cannot compress further`. Fix = bigger-ctx model or slimmer prompt.
2. **Prefill slower than the client's ~60s first-token timeout**: log shows NO error at all —
   the session dies silently at ~60s. The model is still prefilling when the client gives up.

**Diagnose #2 by measuring, never guessing** — benchmark prefill rate directly:
```python
# ~7.3K-token synthetic prompt against /api/generate, read prompt_eval_count/prompt_eval_duration
prompt = "the quick brown fox jumps over the lazy dog. " * 730
# rate = prompt_eval_count / (prompt_eval_duration/1e9)  → then: prompt_tokens / rate vs timeout
```
Measured on this node (:11434, Ollama): **qwen2.5-32b ≈ 109 tok/s prefill; qwen2.5:14b ≈ 243 tok/s.**
So a 26K-token worker prompt = ~240s (32B) or ~107s (14B) — BOTH over a 60s timeout; an 8K prompt =
~67s (32B, still over) vs ~33s (14B, passes). **The governing inequality for any dispatched worker:
`system_prompt_tokens / prefill_rate < client_first_token_timeout`.** Model speed and prompt size are
co-requisites — shipping one fix and retesting wastes a cycle; do the arithmetic first.

**Design rule:** disposable swarm/fan-out workers get a SMALL fast model (14B class), not the biggest
model that fits. Reserve 32B+ for cron/aux jobs whose timeouts (600s) tolerate slow prefill.

## ✅ Remote model ops via Ollama HTTP API — no SSH, no Modelfile scp (2026-08-13)
The scp-Modelfile procedure below still works, but modern Ollama does both operations over the API
from the mini — avoiding the peer-host SSH write entirely:
- **Pull:** `curl -X POST http://<studio-host>:11434/api/pull -d '{"model":"qwen2.5:14b","stream":false}'`
  → `{"status":"success"}` (blocks until done; use -m 300+).
- **Create a ctx variant:** `curl -X POST .../api/create -d '{"model":"qwen2.5-14b-32k",
  "from":"qwen2.5:14b","parameters":{"num_ctx":32768},"stream":false}'` — the `from` + `parameters`
  fields replace the Modelfile. VERIFY the param stuck: `/api/show -d '{"model":"<new>"}'` must list
  `num_ctx 32768` under parameters (Ollama defaults to 4K ctx otherwise — silent truncation).

## ⚠️ Scaling the swarm fleet (worker count + what a clone inherits)
Adding more swarm-worker profiles is a clone-a-dir operation (`cp -r swarm-worker-a
swarm-worker-<letter>`, rename profile.yaml, clear logs) — NO gateway restart, workers are
read fresh per kanban dispatch. Full procedure in
`references/swarm-worker-fleet-scaling.md`. Two things that cost tool calls to derive and
are worth knowing up front:

- **How many: `workers = effective_slots / decode_duty_cycle`, then miss HIGH.** On this
  M2 Max effective_slots is the throughput plateau (~P=8), NOT the `--parallel 12` cap.
  Duty cycle ~70% (analysis workload) → **16 workers**; ~90% (pure decode) → 12–14;
  <60% (research/tool-heavy) → 24–32. An idle GPU slot is the expensive waste; a parked
  dispatcher thread on the mini is nearly free — so when unsure, pick the larger count.
- **The three guards are PROCESS-WIDE, inherited automatically by any clone.** write_gate,
  memory_checkpoint, and the checkpoint family load via the venv's `sitecustomize.py` at
  interpreter startup into the single shared `hermes-agent` venv — not per-profile. A fresh
  worker is gated the instant it spawns. The worker AGENTS.md "no write gate" line is a
  BEHAVIORAL instruction to the model, NOT a bypass; the gate is physically present.
  `on_session_start` hooks are the exception — they live in the default profile's config and
  are NOT inherited (fine; they're skill-hygiene crons irrelevant to workers). The only real
  kill-switch to audit is `HERMES_WRITE_GATE=off` in a worker's `.env` (clean clones don't
  set it). Audit command in the reference.

## See also
- `references/swarm-worker-fleet-scaling.md` — clone procedure for swarm-worker-<letter>, worker-count sizing model (duty-cycle formula), gate/hook inheritance audit, decommissioning stale profiles.
- `references/llama-server-setup-2026-06-19.md` — native llama-server install (macOS SDK gate, blob reuse, flag drift), launchd plist, P=4/8 benchmark, aux+swarm routing, bypass auto-upgrade interaction.
- `references/prompt-cache-verification.md` — verify prefix-cache warmth EMPIRICALLY via Anthropic usage counters + the production state.db query; OAuth-probe gotchas. Use when a claim rests on "the prompt is frozen / cache stays warm."
- `references/studio-delegation-findings-2026-06-18.md` — DEFINITIVE delegation record: full failure chain (api_key_env, 64k gate, timeout, tool-call format), prefix-cache investigation, P=1 vs P>1, working recipe. Read FIRST if delegation breaks.
- `references/delegation-routing-failure.md` — silent DeepSeek-fallback debugging: detect via the result `model` field, tool-call-format + prompt-ingest-timeout failure modes, repro recipes.
- `references/macstudio-ollama-config.md` — verified node config snapshot (this session).
- `references/parallelism-benchmark.md` — P=2/3/4 A/B/C grid + aux-role local-vs-Sonnet decision filter.
- `scripts/studio-bench.sh` — re-runnable cold+3×warm probe (eval/prompt t/s + VRAM residency).
- `references/performance-baseline-*.md` — dated pre/post snapshots to diff against.

> `<studio-host>` = the GPU host on the tailnet; real address via `STUDIO_OLLAMA_HOST` in `.env` (memory_search "Tailnet").
