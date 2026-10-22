# Subagent Context Budget — sizing the delegation model's num_ctx

When deciding what context the delegation model should run at, the question is NOT
"how big can we afford" but "what does a real subagent actually consume." A subagent
arrives pre-loaded with overhead BEFORE any task work. Measure the floor, then add
working headroom, then size num_ctx to cover it — not to some round number.

## How to measure the floor live (don't trust these constants forever)
- AGENTS.md injection: capped by `config.yaml: context_file_max_chars` (was 20000 chars
  ≈ 5000 tok). Read the live value; the injected MEMORY % header lags config.
- Tool schemas: load `tools/*_tool.py`, JSON-dump the schema dicts, `len // 4` ≈ tokens.
  Measured 2026-06: terminal=1410, session_search=1263, delegate=1060, skill_manager=1024.
  21-tool "full cli" set ≈ 13,400 tok. terminal+file+web ≈ 3,700 tok.
- Skills: each auto-loaded skill 8–14k chars (~2–3.5k tok). Count how many the task triggers.
- MEMORY.md + USER PROFILE + Honcho ≈ 800–1000 tok combined.

## The two subagent classes (2026-06 measured)
| Class | Toolset | Skills | Overhead floor | Needs num_ctx ≥ |
|---|---|---|---|---|
| Light | terminal+file (constrained) | 1–2 | ~16,250 tok | 24k |
| Heavy | full cli (21 tools) | 3–5 | ~40,300 tok | 48k |

Light floor breakdown: AGENTS 5000 + tools 3700 + skills 3750 + mem/honcho 800 +
task 1000 + output headroom 2000 ≈ 16,250.
Heavy floor: AGENTS 5000 + tools 13500 + skills 10000 + mem 800 + task/context 3000 +
mid-task reads 5000 + output 3000 ≈ 40,300.

## The leverage move: trim the TOOLSET per child, not just the window
`delegate_task` takes a per-child `toolsets` list. A web-research child doesn't need
kanban/memory/session_search — dropping to `['web']` or `['terminal','file','web']` cuts
~10k tokens off the floor. This is bigger savings than shrinking num_ctx and it keeps
quality. Right-size toolsets FIRST, then size the model context to the trimmed floor.

## Conclusion that held this session
For qwen2.5-32b on the 56GB Studio: **32k (native ceiling)** fits light subagents with
room and heavy-but-trimmed subagents, while freeing VRAM for 2-slot parallelism. 16k was
wrong — it's below even the light floor. The rare kitchen-sink (full toolset + 5 skills)
child should route to Anthropic direct, not the Studio.
