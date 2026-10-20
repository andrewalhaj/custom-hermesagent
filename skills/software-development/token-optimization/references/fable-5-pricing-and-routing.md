# Claude Fable 5 / Mythos 5 — pricing + routing facts (verified 2026-06-09)

> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

Source: anthropic.com/news/claude-fable-5-mythos-5 + anthropic.com/claude/fable
(live-extracted 2026-06-09, launch day). Verify against current docs before
quoting — prices change.

## The models
- **Claude Fable 5** (`claude-fable-5` on the API): first Claude 5-family model,
  "Mythos-class made safe for general use." Sits ABOVE Opus 4.8 in capability.
- **Claude Mythos 5**: same underlying model, safeguards lifted in some areas;
  access-gated (Project Glasswing / trusted-access orgs only). NOT in any public
  model catalog — do not attempt to route to it.

## Pricing (launch)
- $10 / M input, $50 / M output. 90% prompt-caching input discount applies.
- vs Sonnet 4.6 ($3/$15): input ~3.3×, **output ~3.3×** — the delegation/output
  discipline is proportionally MORE valuable on Fable, not less.
- US-only inference available at 1.1× pricing.

## Operational quirks (affect cost + behavior)
- **Safety fallback rerouting**: cyber/bio-flagged queries get silently answered
  by Opus 4.8 instead (<5% of sessions; you are NOT charged Fable prices for
  rerouted requests). Infra/security work can occasionally trip it.
- **30-day data retention is mandatory** when using Fable.
- Catalog IDs seen: `anthropic/claude-fable-5` (nous, openrouter providers),
  `claude-fable-5` (direct Anthropic).

## Routing lesson (this box)
- Direct Anthropic: `model.default=claude-sonnet-4-6`, `model.provider=anthropic`.
- Any cron created with `model: None` inherits the DEFAULT model — under Fable
  that's $50/M output on every run. ALWAYS pin new LLM crons to a cheap model
  (deepseek-v4-flash/-pro) at creation time; this leak was caught live within
  an hour of the Fable switch.

## OAuth bypass complexity classifier (2026-06-10)

The `anthropic_billing_bypass.py` patch auto-upgrades `claude-sonnet-*` requests
to the heavy tier model (`claude-fable-5` as of 2026-06-10) when the classifier
fires. Critical design learnings:

### The system-prompt over-fire bug (fixed 2026-06-10)

**Original bug:** The classifier scanned `system + messages` combined. AGENTS.md
alone scores 4 complexity signals (`audit`, `diagnose`, `root cause`, `troubleshoot`)
— so EVERY request was upgraded to Fable regardless of task complexity. The "router"
was effectively always-on.

**Fix:** Scan ONLY the last human turn — the last `role: "user"` message that carries
real text. Deliberately ignores:
1. The system prompt (keyword-saturated boilerplate)
2. `tool_result`-only user turns (mid-agentic-loop, would drop a complex task back
   to Sonnet halfway through the agentic loop)

### Correct last-human-turn extraction

```python
def last_human_text(messages):
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # ONLY plain text blocks; tool_result blocks have no top-level 'text'
            text = " ".join(
                str(b.get("text") or "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if text.strip():
            return text.lower()
    return ""
```

### Thresholds (current)
- `_COMPLEX_SCORE_THRESHOLD = 2` — number of signal matches needed
- `_COMPLEX_LEN_THRESHOLD = 2000` — chars: lower threshold to 1 on long prompts
- `_HEAVY_MODEL = "claude-fable-5"` (was `_OPUS_MODEL = "claude-opus-4-8"`)

### Test cases (all must pass after any classifier edit)

| Scenario | Expected |
|---|---|
| Trivial turn + full system prompt | Stay Sonnet |
| Complex user turn (refactor + debug + migration) | Upgrade to Fable |
| Mid agentic loop — complex ask → tool_result turn | Stay Fable (reads the earlier text turn) |
| Trivial turn after complex session | Drop to Sonnet |
| Already Fable request | Never downgrade |

### Durability
- Live file: `~/.hermes/patches/anthropic_billing_bypass.py`
- No golden or self-heal (patch_guard retired 2026-10-16) — check marker `_classify_complexity` by hand after updates
- Requires gateway restart to activate (process loads the patch at startup)
