# Auto-Retrieval (per-turn RAG) — architecture findings

Investigated 2026-06-08 when asked "will you retrieve from Supabase automatically?"
Conclusion: **storage/offload is autonomous, but retrieval is NOT background-automatic.**
Retrieval is cue-driven — a hot-tier pointer (or the conversation topic) triggers an
agent-initiated `knowledge.py search`. There is no hook that searches + injects on every turn.

## Why a clean hook CANNOT do per-turn auto-injection (verified in code)

Hermes DOES have a live hook system (the 2026-06-05 reference doc claiming "no hook API"
was stale): `gateway/hooks.py` + `gateway/builtin_hooks/`, hooks discovered from
`~/.hermes/hooks/<name>/{HOOK.yaml, handler.py}`. Events: `gateway:startup`, `session:start/end/reset`,
`agent:start`, `agent:step`, `agent:end`, `command:*`.

The blocker for auto-RAG specifically:
- `agent:start` fires via `hooks.emit(...)` (gateway/run.py ~line 9499) — **fire-and-forget; return values discarded.** Only `emit_collect()` captures returns, and `agent:start` does not use it.
- The `hook_ctx` dict passed to the hook is built locally and **never read back** — `_run_agent` uses `context_prompt`, which is assembled EARLIER (gateway/run.py ~line 8994 `build_session_context_prompt(...)`, appended at ~9419/9459) and passed straight to the model as a system message (~line 16783).
- Net: a hook can run side-effects but cannot mutate the prompt the model sees. **True per-turn auto-RAG requires a CORE PATCH** at the context-assembly seam (~line 9459 in gateway/run.py), which is the fragile, guard-stripping category — would need re-applying by hand after every Hermes update (patch_guard retired 2026-10-16).

## Prototype results (scripts/auto_retrieve_proto.py)

Built the retrieval ENGINE standalone (calls knowledge.py's real hybrid `search()` — do NOT
reimplement; v1 bug was passing a raw string to `tbl.search()` → degenerate constant 0.50
scores. The real path is `embed([query])` → `tbl.search(qvec)` → RRF+MMR+graph scoring).

- **Latency:** 87–237ms warm (first call ~2.2s = one-time embedding-model load, amortized on a long-lived gateway). Cheap enough per turn.
- **Relevance is the real cost, not speed.** On-topic queries score 0.86–0.88 and retrieve perfectly. But off-topic queries ("tell me a joke" → 0.68, "what's the weather" → 0.67) do NOT go silent — the embedding space is dense enough that *something* always looks moderately relevant. At a 162-row corpus, most user turns aren't infra-topical, so naive per-turn injection is mostly noise.
- **Floor must be ~0.80, not 0.30** — only top-tier matches are trustworthy. High precision, low recall: at 0.80 the joke/weather cases go silent correctly, but many soft-topic genuine queries also get dropped.

## Recommendation (the decision shape)

- **B-lite (preferred at this corpus size):** high-floor (≥0.80) precision retrieval the agent invokes *itself* when a turn is clearly infra/memory-topical. Backed by the proven engine, NO core patch, no per-turn tax, no noise. This is ~90% of the value of auto-RAG (cold facts surface without depending on hand-written pointers) without the fragility. Essentially the pointer model, but the "pointer" is a judgment-triggered semantic search instead of a hardcoded breadcrumb.
- **B-full (true unconditional per-turn auto-RAG):** patch `gateway/run.py` ~9459 to call the engine every turn at floor 0.80, inject above-threshold hits. Cost: a core-file patch (reverted by every Hermes update; re-apply by hand) + ~150ms/turn + occasional noise. Buildable, but crosses into core-patching → firmly gated. NOTE: B-full was subsequently BUILT and is LIVE (see the main SKILL.md "B-full auto-RAG is LIVE" section — `grep -c '_bfull_retrieve' gateway/run.py`). Corpus size as of 2026-06-16 is ~398 rows (was 162 at this doc's writing); the crossover reasoning below stands, only the row-count anchor changed.

## Reusable artifact

`~/.hermes/scripts/auto_retrieve_proto.py` — the standalone engine + latency/relevance harness.
Reuse it to re-measure precision/recall after the corpus grows, to re-tune the floor, or as the
function a future B-lite/B-full wiring calls.
