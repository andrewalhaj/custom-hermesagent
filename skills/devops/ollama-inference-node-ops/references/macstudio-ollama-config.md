# Mac Studio Ollama — verified config snapshot

Verified live 2026-06-17 during a parallelism/multi-model tuning pass.

## Hardware / disk
- Apple M2 Max, 64GB unified, macOS arm64.
- Data volume: 926Gi total, 864Gi free at time of check.
- `iogpu.wired_limit_mb = 57344` (56GB) — VRAM cap already raised from the 48GB default.

## How Ollama runs here
- Spawned by `Ollama.app` GUI, NOT by launchd (see SKILL.md pitfall).
- Process: `/Users/localadmin/OllamaApp/Ollama.app/Contents/Resources/ollama serve`
- llama-server child shows the live serving flags, e.g.
  `--port 63568 --host 127.0.0.1 ... -c 32768 -np 1 --flash-attn auto -b 1024 -ub 1024`
- `launchctl list | grep ollama` → `-  1  com.ollama.server` (PID `-` confirms launchd isn't the parent).

## Plist (~/Library/LaunchAgents/com.ollama.server.plist)
RunAtLoad=true, KeepAlive=true, logs to /tmp/ollama.log + /tmp/ollama-error.log.
`EnvironmentVariables` after this session's tuning:
```
OLLAMA_HOST=0.0.0.0:11434
OLLAMA_NUM_PARALLEL=2
OLLAMA_MAX_LOADED_MODELS=2
OLLAMA_KEEP_ALIVE=30m
```
Before: only `OLLAMA_HOST` was set (single model, single thread, default keep-alive).

## Models
- Standing models: `qwen2.5-32b-64k:latest` (Q4_K_M, 32.8B, ~28GB resident, 32768 ctx),
  `qwen2.5vl:7b` (vision, ~6GB, loads cold on demand pre-tuning).
- The previously-noted `qwen2.5 72B` had been **removed from disk** (only ~24GB of model
  data present = just the 32b + vl:7b). Re-pulled `qwen2.5:72b` (~47GB) this session.
  → When restoring a "missing" heavy model, first confirm it's actually gone vs evicted:
    `du -sh ~/.ollama/models/` and the tags API tell the truth; memory/topology lag.

## Pull performance observed
`qwen2.5:72b` pulled at ~79 MB/s, 47GB total, ~10min wall time. Scheduled a one-shot
`15m` cron (toolset `terminal`) to report completion instead of blocking the turn.
