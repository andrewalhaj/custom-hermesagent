# Delegation → Studio silent-fallback-to-cloud failure (2026-06-18)

## TL;DR
Configured `delegation.model` to the Studio. Every `delegate_task` silently ran on
`deepseek-v4-pro` instead. Took ~20 tool calls to root-cause because every Studio component
verified fine in isolation. The lesson: **raw inference benchmarks do not prove delegation
works. Read the `model` field in the delegate_task result.**

## How to detect it instantly
After ANY delegation config change, fire one trivial `delegate_task` and inspect the result:
```
"model": "deepseek-v4-pro"   ← BROKEN: fell back to cloud, NOT running on the Studio
"model": "qwen2.5-32b-32k"   ← GOOD: actually ran on the Studio
```
The task "completes" either way — the fallback is transparent. The model field is the only tell.

## The fallback mechanism
`tools/delegate_tool.py` `_build_child_agent` (~line 1202):
`parent_fallback = getattr(parent_agent, "_fallback_chain", None)` → passed as the child's
`fallback_model`. That chain comes from top-level `fallback_providers` in config.yaml:
```yaml
fallback_providers:
- provider: deepseek
  model: deepseek-v4-pro
  base_url: https://api.deepseek.com/v1
  api_key_env: DEEPSEEK_API_KEY
```
Child's PRIMARY (Studio) call fails → child transparently retries down the fallback chain →
DeepSeek answers → task reports success with `model: deepseek-v4-pro`.

## Credential resolution is NOT the bug — verified clean
Reproduced `_load_config()` + `_resolve_delegation_credentials()` in a fresh venv python:
```
creds.model=qwen2.5-32b-32k  creds.provider=custom  creds.base_url=http://<studio-host>:11434/v1
effective_model=qwen2.5-32b-32k  effective_provider=custom
```
Resolution is flawless. The child is BUILT pointing at the Studio. It fails at RUNTIME, during
the API call, then falls back. Don't waste time auditing config/resolution — it's the runtime call.

## Two runtime failure modes (both end at DeepSeek)

### Mode 1 — wrong tool-call format (small coder models)
`qwen2.5-coder:14b` asked to call a tool returns it as PLAIN TEXT in `content`:
```
content: { "name": "add", "arguments": { "a": 5, "b": 7 } }   ← string, not a tool_call
```
The agent framework needs a structured `tool_calls` array. Text → unparseable → child errors →
fallback. `qwen2.5-32b-32k` on the same probe returns it correctly:
```
tool_calls: [{"id":"...","type":"function","function":{"name":"add","arguments":"{\"a\":5,\"b\":7}"}}]
```
Probe recipe (check RESPONSE SHAPE, not just that it answers):
```bash
curl -s http://<studio-host>:11434/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"MODEL","messages":[{"role":"user","content":"add 5 and 7 with the add tool"}],
       "tools":[{"type":"function","function":{"name":"add","description":"Add",
       "parameters":{"type":"object","properties":{"a":{"type":"number"},"b":{"type":"number"}},
       "required":["a","b"]}}}],"max_tokens":80}'
```

### Mode 2 — prompt-ingest timeout (slow big models)
Even with correct tool-calls, the 32B is too slow on a REAL agent payload. A subagent ships
~13,600 input tokens (AGENTS.md + ~14 tool schemas + task). At 32B prompt-ingest ~378 t/s →
~40s to first token. Older logs: `Stream stale for 180s — no chunks received. Killing
connection.` → broken pipe → DeepSeek fallback.

**Why isolated benchmarks lie:** a tiny "say hi" curl returns in <1s and looks perfect. The
failure only appears with a realistic large payload. Probe with a ~4k-token system prompt +
15 tools to reproduce the real latency:
```python
big_system = "You are a subagent. " + ("Follow instructions carefully. " * 800)  # ~5k tok
tools = [{"type":"function","function":{"name":f"t{i}","parameters":{"type":"object",
         "properties":{"x":{"type":"string"}}}}} for i in range(15)]
# POST to /v1/chat/completions, time it. 40s = will time out as a real subagent.
```

### Mode 0 — DECLARED context < 64k → child rejected at spawn (THE ACTUAL ROOT CAUSE, found last)
`agent/model_metadata.py: MINIMUM_CONTEXT_LENGTH = 64_000`. `agent_init.py` (~line 1525)
raises `ValueError: Model qwen2.5-32b-32k has a context window of 32,768 tokens, which is
below the minimum 64,000 required by Hermes Agent` — synchronously, at child CONSTRUCTION,
before any network call. The child never dials the Studio; parent falls to DeepSeek.

This is why rebuilding 32B `64k`→`32k` (to fix VRAM) silently broke delegation: the original
`qwen2.5-32b-64k` name existed to clear this gate. The `32k` rebuild looked great on every
raw benchmark and was un-spawnable as a delegation child.

**Smoking gun that points straight here:** Studio access log (`/tmp/ollama-error.log`) shows
ONLY `127.0.0.1` (your curls), never the mini's tailnet IP. Request never left the mini =
pre-connection crash. Confirm by reproducing the child build in-process:
```python
# /usr/local/lib/hermes-agent/venv/bin/python
from run_agent import AIAgent
AIAgent(base_url="http://<studio-host>:11434/v1", api_key="no-key-required",
        model="qwen2.5-32b-32k", provider="custom", api_mode="chat_completions",
        max_iterations=1, quiet_mode=True)   # raises the ValueError immediately
```

**Declared-context resolution order** (first non-None wins, all at init):
1. `model.context_length` (config.yaml)
2. `custom_providers[<name>].models[<model>].context_length`  ← the lever
3. live `/api/show`
Verify what Hermes will use:
```
venv/bin/python -c "from hermes_cli.config import get_custom_provider_context_length, load_config; \
  print(get_custom_provider_context_length(model='qwen2.5-32b-32k', \
  base_url='http://<studio-host>:11434/v1', custom_providers=load_config().get('custom_providers',[])))"
```

## Resolution
Two viable paths, in order:

1. **FIX local delegation (no rebuild):** declare `context_length: 65536` for the model under
   `custom_providers[mac-studio].models.<model>` in config.yaml (GATED edit). This clears the
   64k gate so children spawn. It's honest — llama-server already runs `-c 131072` (128k KV)
   regardless of the Modelfile's baked 32k, so the model genuinely has the window. NOTE: this
   only fixes Mode 0. Mode 2 (prompt-ingest timeout on the slow 32B) can still bite under real
   ~13k-token agent payloads, and Mode 1 still rules out coder models. Test with a realistic
   payload after clearing the gate before declaring victory.
2. **Sonnet/Anthropic delegation** — no local ceiling, no gate, flat Max-plan OAuth (rate-limit
   cost not metered $). For throughput-priority + token headroom this is often the right call.

Keep the Studio for direct inference + aux roles (compression/curator/web_extract) where
there's no agent-loop tool-calling and no per-call timeout regardless of which path is chosen.

## Mode 0b — child_timeout_seconds (the LAST wall, after the 64k gate clears)
Once the model declares ≥64k, children FINALLY spawn and dial the Studio — the delegate_task
result `model` field shows the Studio model (`qwen2.5-32b-65k`) and `api_calls: 1`. But the
first batch still failed: all four came back `status: timeout, exit_reason: timeout` at
exactly 600.0s. `delegation.child_timeout_seconds` (default 600) was the cap.
- Single delegate_task solo: **115s** (13,395 input tokens, completed, model=Studio). ✓
- 4-wide batch at 600s cap: ALL timed out (~500-516s each, killed at 600). ✗
- 4-wide batch after raising `child_timeout_seconds` → 900: ALL completed, real code returned,
  model = Studio on every result. ✓✓ — first genuine end-to-end local delegation this session.
Always test a SINGLE delegate first (isolates timeout from contention), then the batch.

## VERIFIED WORKING RECIPE (full local-delegation chain, 2026-06-18)
All three are required; missing any one → silent DeepSeek fallback or timeout:
1. Model rebuilt/declared at ≥64k: `qwen2.5-32b-64k` with `PARAMETER num_ctx 65536`, AND
   `custom_providers[mac-studio].models.qwen2.5-32b-64k.context_length: 65536`. (Clears Mode 0.)
2. `delegation.child_timeout_seconds: 900` (≥). (Clears Mode 0b for 4-wide batches.)
3. `delegation.model: qwen2.5-32b-64k`, `provider: custom:mac-studio`, and NO stale
   `delegation.api_key_env` (a leftover `DEEPSEEK_API_KEY` there forces cloud routing — remove it).
Performance reality once working: ~115s/child solo, ~500s/child at 4-wide (P=4 decode
contention + 13k-token ingest). Slow but real. Cut it by passing minimal `toolsets` per call
(toolsets=[] still injects the DEFAULT ~13k-token schema set; `["terminal","file"]` ≈ 4k).

## Note on the api_key_env trap (Mode -1, the FIRST thing that misrouted this session)
Before the 64k gate even mattered, delegation routed to DeepSeek because the `delegation`
block still carried `api_key_env: DEEPSEEK_API_KEY` from a prior DeepSeek-era config. The
keyless `custom:mac-studio` provider + a DeepSeek key binding contradict; the resolver
followed the key. Removing `api_key_env` was necessary but NOT sufficient (the 64k gate was
still behind it). When auditing delegation routing, grep the delegation block for any stale
`api_key_env`/`api_key` that contradicts `provider: custom:mac-studio`.

## Debugging-discipline note for future me
I declared "root cause found" ~4 times and was wrong each time (api_key_env, stale CLI_CONFIG,
tool-calls, timeout) before assembling the full picture. When every component verifies in
isolation but the integration fails: stop pattern-matching from logs, REPRODUCE the exact
runtime call path with a realistic payload, and read the actual result field — don't infer.
