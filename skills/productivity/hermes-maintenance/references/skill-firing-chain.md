# Why skills don't fire — the 4-link chain (don't over-credit the cliff fix)

Detail behind §1 Skill Lifecycle. Fixing truncation (the 60-char cliff) is necessary but
NOT sufficient. Whether a skill actually fires is a chain; the cliff is only link 2.

## The chain

1. **In the index at all** — not suppressed by
   `config.yaml → skills.platform_disabled.<platform>`. A suppressed skill NEVER fires on that
   channel regardless of links 2-4. Audit this FIRST. On one box, 75 skills were suppressed on
   Telegram, several wrongly (`hermes-agent`, `kanban-*`, `github-*` — the operator's own ops
   domains). Un-suppress = remove the name from that list (GATED config edit, present
   analysis+risk+rollback). High-leverage, low-effort, silent killer when wrong.
2. **Line visible / keyword-bearing** — the cliff fix (≤60 chars, trigger word first). The
   keyword must survive into the rendered line.
3. **Agent reads the index and matches** — model attention over a ~300-item flat list. Dilution,
   not keyword precision, dominates. BIGGEST real lever; the cliff fix does not touch it.
4. **Agent chooses to load vs. wing it** — discipline. No instruction prose reliably fixes this
   (the agent skipped its own recall gate this session — prose isn't enough).

**Honest impact:** the cliff fix alone moves ~10-20% of the affected-skill firing problem.
Links 3 and 4 dominate. Tell the user this plainly — never sell a visibility fix as a firing fix.

## Mechanical fix for links 3+4 — `pre_llm_call` relevance injector

There is NO `post_update` hook event. Verified `VALID_HOOKS`: api_request_error, on_session_start,
on_session_end, on_session_finalize, on_session_reset, pre_llm_call, post_llm_call, pre_tool_call,
post_tool_call, pre_api_request, post_api_request, pre_approval_request, post_approval_response,
pre_gateway_dispatch, subagent_start, subagent_stop, transform_llm_output, transform_terminal_output,
transform_tool_result.

For per-turn skill surfacing use **`pre_llm_call`** — fires before every model call, may inject
context via stdout `{"context": "..."}`. A hook that reads the latest user message, scores it
against all skill name+description+load_when, and injects
`Relevant skills this turn: a, b, c — load with skill_view` converts "scan 300 + decide to load"
into "the right 2-3 are named in front of you." Mechanically attacks links 3 AND 4 at once.

For post-update self-healing of skill descriptions (since no `post_update` event exists), use an
`on_session_start` hook that runs a fast idempotent guard (exit 0 in ms when nothing truncated;
heal via `--apply` only when a core update reintroduced >60-char descriptions). Allowlist the one
`(event, command)` pair by writing the exact `{event, command, approved_at}` record into
`shell-hooks-allowlist.json` (the schema `_is_allowlisted()` reads) — narrower than blanket
`hooks_auto_accept: true`. Verify with `hermes hooks doctor` (must show allowlisted + ran-clean).
Note: `--accept-hooks` is a GLOBAL flag placed BEFORE the subcommand (`hermes --accept-hooks hooks
test ...`), not after `doctor`.

## Pitfall — bag-of-words scoring is too weak to ship

A keyword/IDF scorer surfaced WRONG skills on ~40% of probes ("voice changer" →
`simpo-training`/`meme-generation`; "kanban dashboard" → `home-assistant-dashboard-designer`).
IDF weighting (down-weight tokens common across many skills — "training"/"audit"/"dashboard") and
requiring a specific long+rare overlap term helped but did NOT clear the bar. **A relevance hook
that surfaces wrong skills is WORSE than none — it actively misleads.** If building this, use the
existing embedding store (Supabase / `knowledge.py`) for semantic similarity, not keywords. The
plumbing is sound and reusable — `pre_llm_call` + stdout `{"context"}` + `platform_disabled`
filtering + a `/tmp` mtime-keyed index cache so you don't walk ~300 SKILL.md every turn; only the
SCORER needs to be semantic. Always test offline against a battery of real queries — including
ones that MUST stay silent (greetings, vague "help me with that thing") — before wiring it live.
