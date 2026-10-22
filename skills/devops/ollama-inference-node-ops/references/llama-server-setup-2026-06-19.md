# Native llama-server on Mac Studio — setup record (2026-06-19)

Stood up llama.cpp's native `llama-server` on the Studio alongside Ollama, to get controls
Ollama hides (speculative decoding, explicit `--ctx-size`/`--parallel`/`--cache-type-k`,
deterministic prefix cache). Runs on `:8080`; Ollama untouched on `:11434`.

## Why bother (the motivation)
Two arXiv papers drove this: speculative decoding (2211.17192 — small draft model proposes,
big model verifies in one parallel pass, 2-3× with identical output) and multi-token prediction
(2404.19737 — extra heads predict N tokens, up to 3× faster). BOTH are in llama.cpp and BOTH are
hidden by Ollama's wrapper. `llama-server --model-draft` exposes speculative decoding directly.
Note: MTP needs models *trained* with MTP heads (DeepSeek-V3/R1 only, 671B — won't fit); Qwen2.5
can't do MTP, but CAN do speculative decoding with a Qwen2.5-7B draft.

## Install (prebuilt binary — do NOT build from source)
- Xcode CLT is NOT installed on the Studio and `xcode-select --install` needs a GUI dialog you
  can't clear over SSH. So source builds are out. Use prebuilt macOS arm64 releases.
- **THE TRAP: the latest release targets a newer macOS SDK than the node runs.** Studio is on
  macOS **14.4.1 Sonoma**. The newest llama.cpp arm64 build (b9716) is compiled against macOS
  26.0 and dies at load:
  ```
  dyld[...]: Symbol not found: _OBJC_CLASS_$_MTLResidencySetDescriptor
    Referenced from: .../libggml-metal.dylib (built for macOS 26.0 which is newer than running OS)
  ```
  FIX: grab an OLDER tag. The Apr-2026 `-kleidiai` builds run on Sonoma. Used **b8891**:
  ```
  curl -L "https://github.com/ggml-org/llama.cpp/releases/download/b8891/llama-b8891-bin-macos-arm64-kleidiai.tar.gz" -o /tmp/llama.tar.gz
  mkdir -p ~/llama.cpp/b8891 && tar -xzf /tmp/llama.tar.gz -C ~/llama.cpp/b8891
  ~/llama.cpp/b8891/llama-server --version   # must print Metal device lines, not a dyld error
  ```
  Good output includes `GPU family: MTLGPUFamilyApple8`, `has unified memory = true`,
  `has bfloat = true`. (`has tensor = false` is expected — M2 Max is pre-M5, no tensor API.)
- To find a compatible tag: list releases via the GitHub API, page back to before the SDK bump,
  prefer `-kleidiai` arm64 assets:
  ```
  curl -s "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=100&page=6" \
    | python3 -c "import json,sys; [print(r['tag_name'],r['published_at'][:10],a['browser_download_url']) for r in json.load(sys.stdin) for a in r['assets'] if 'macos-arm64' in a['name']]"
  ```

## Model: reuse the Ollama blob (no re-download)
```
ollama show --modelfile qwen2.5-32b-64k   # FROM line → blob path
# → /Users/localadmin/.ollama/models/blobs/sha256-c18a9ba8...
```
Pass that path straight to `--model`. GGUF is GGUF.

## launchd plist (com.llama.server.plist)
Path: `/Users/localadmin/Library/LaunchAgents/com.llama.server.plist`. Key args:
```
--model /Users/localadmin/.ollama/models/blobs/sha256-<32b-hash>
--ctx-size 16384          # right-sized to ~13k subagent payload; NOT 64k (halves slots for nothing)
--n-gpu-layers 999        # full Metal offload
--flash-attn on           # NOTE: needs explicit 'on' in b8891 — bare flag eats the next arg & errors
--cache-type-k q8_0       # quantized KV → ~2GB/slot instead of ~4
--parallel 8              # P=8: throughput pick for batch fan-out (see benchmark)
--host 0.0.0.0 --port 8080
--metrics                 # exposes /metrics (prompt/predict t/s, busy slots)
--alias qwen2.5-32b       # the model id /v1/models + responses report → match Hermes config
```
`KeepAlive=true`, `RunAtLoad=true`. Logs to `~/llama.cpp/logs/llama-server.log`.

Restart = SAME bootout+bootstrap as Ollama (a plain kill respawns the stale plist):
```
UID_N=$(id -u)   # 501
PLIST="/Users/localadmin/Library/LaunchAgents/com.llama.server.plist"
launchctl bootout   gui/$UID_N/com.llama.server 2>&1 || true
sleep 4
launchctl bootstrap gui/$UID_N "$PLIST"
sleep 90   # 18GB 32B into Metal takes ~75-90s; /health is 'ok' only after
curl -s http://localhost:8080/health   # {"status":"ok"}
```
Always `tail ~/llama.cpp/logs/llama-server.log` after a plist edit — argparse errors print there
and KeepAlive respawns straight back into the same error (looks like "won't start").

## Validation gate (the only proof that matters)
1. Tool-call shape — must be STRUCTURED, not plain text:
   ```
   curl -s http://<studio-host>:8080/v1/chat/completions -H 'Content-Type: application/json' \
     -d '{"model":"qwen2.5-32b","messages":[{"role":"user","content":"add 5 and 7 using the add tool"}],
          "tools":[{"type":"function","function":{"name":"add","parameters":{"type":"object",
          "properties":{"a":{"type":"number"},"b":{"type":"number"}}}}}],"max_tokens":80}' \
     | grep -o '"tool_calls":\[.*\]'
   ```
   qwen2.5-32b returns a proper `tool_calls` array (the general Qwen models tool-call correctly;
   coder variants do NOT — they emit plain text).
2. Reachable from the mini: `curl -s http://<studio-host>:8080/health` → `{"status":"ok"}`.

## Benchmark (32B, ctx 16384, q8_0 KV, 120 tok each; run from a bash SCRIPT not inline — `&` loops trip the foreground guard)
| Config | concurrent | wall time | aggregate t/s |
|---|---|---|---|
| P=4 | 4 | 35s | ~13.7 |
| P=8 | 4 | 33s | ~14.5 |
| P=8 | 8 | 55s | ~17.5 |
VRAM: weights ~19GB + ~2GB/slot(q8_0@16k) → P=8 = 35GB of 56GB cap, 21GB spare. P=12 ≈ 43GB also fits.
**P=8 is the batch-fan-out pick.** Latency-min for a single task is still LOW P (per-slot t/s drops
as slots fill) — match P to workload shape.

## Hermes wiring (what was set this session)
- `custom_providers`: added `mac-studio-llama` → `base_url: http://<studio-host>:8080/v1`,
  `api_mode: chat_completions`, `models.qwen2.5-32b.context_length: 65536`. (The 65536 declaration
  clears the 64k init gate; the `_ollama_num_ctx` clamp gate does NOT apply to llama-server.)
- Aux roles moved to Studio llama-server: `compression`, `web_extract`, `title_generation`,
  `triage_specifier`, `kanban_decomposer` (lightweight summarization/classification — 32B handles
  fine, frees Anthropic rate limit). `curator` stays on Ollama `:11434`.
- `executor` profile → `model.default: qwen2.5-32b` + `provider: custom:mac-studio-llama`.
- **Swarm workers a/b/c** → same (was deepseek-v4-flash). Local + free + P=8 concurrent. Worker
  profile edits are live on next kanban dispatch — no gateway restart. Default-profile + aux
  changes DO need a gateway restart.
- `max_spawn_depth: 1→2` on default AND on the three worker profiles — lets workers fan out their
  own independent subtasks across the P=8 slots. Cascade is hard-bounded at depth 2 globally.

## The delegation decision (why delegation STAYED on Sonnet)
`patches/anthropic_billing_bypass.py` auto-upgrades Sonnet→Opus-4.8 on complex requests (2+
signals, or 1 signal + >2000 chars). Gates on `"sonnet" in model`. So `delegate_task` on Sonnet
gets free Opus on complex subtasks. Flipping delegation to the Studio routes around the Anthropic
adapter → loses the upgrade. Resolution: delegation stays Sonnet (free Opus escalation + no local
ceiling); the SWARM WORKERS carry the local-Studio fan-out (shallow parallel read/analysis, don't
go through the bypass anyway). Clean split — keeps Opus quality where it matters, free local
throughput where it doesn't.

## Still open
- `studio-watchdog.sh` only health-checks Ollama `:11434`; llama-server `:8080` is unmonitored.
  Add a parallel `/health` check with a llama-server-specific recovery (bootout+bootstrap of
  com.llama.server, not the Ollama kill-respawn).
- 72B is on disk, unused. Candidate for a second llama-server instance (`:8081`) for direct
  high-quality single inference, or the speculative-decoding target with a 7B draft.
