> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

> ⚠️ **HISTORICAL — bypass ARCHIVED 2026-10-13.** The complexity classifier
> (`_classify_complexity`/`_maybe_upgrade_model`) in `anthropic_billing_bypass.py`
> is moot: the bypass is not loaded by the new local-guards gateway loader, and
> `_HEAVY_MODEL` equals the main model. Retain for reference if the bypass is
> re-enabled. Do NOT treat as current operating procedure.

# Complexity classifier — tuning & the system-prompt over-fire trap

The auto-upgrade lives in `~/.hermes/patches/anthropic_billing_bypass.py`:
`_classify_complexity()` decides, `_maybe_upgrade_model()` swaps the model.
Wired in at the bottom of the patched `build_anthropic_kwargs` wrapper
(`_maybe_upgrade_model(result)`), so it runs on the FINAL payload every
OAuth request. Tunables are decoupled module constants near the top of the
classifier block:

- `_HEAVY_MODEL` — the upgrade target (was `_OPUS_MODEL="claude-opus-4-8"`;
  set to `"claude-fable-5"` 2026-06-10). Renaming the constant means grepping
  for the OLD name returns 0 — verify with `grep -c _HEAVY_MODEL` after a rename,
  and confirm the docstrings/comments that say "Opus" got updated too.
- `_COMPLEX_SIGNALS` — keyword list (refactor, architecture, audit, debug,
  migration, deploy, "build a", "from scratch", …).
- `_COMPLEX_SCORE_THRESHOLD` (default 2) — signals needed to upgrade.
- `_COMPLEX_LEN_THRESHOLD` (default 2000) — on prompts longer than this,
  a single signal is enough.

## THE TRAP — scanning the system prompt makes it ALWAYS upgrade (PROVEN 2026-06-10)

The original `_classify_complexity` concatenated **system prompt + ALL
messages** and keyword-scanned the lot. The system prompt (AGENTS.md + soul +
tool docs) is keyword-saturated — AGENTS.md alone scored 4 signals
(`audit`, `diagnose`, `root cause`, `troubleshoot`) → `UPGRADE=True`. Net
effect: **every single request upgraded to the heavy/expensive tier**, including
"what time is it?", because the boilerplate context alone clears the threshold.
The router looked configured but was effectively always-on — invisible because
auth still worked and the only symptom was the bill.

Measure it before trusting any threshold change — extract the live signal list
and score your real system prompt:

```python
import re
src = open(os.path.expanduser('~/.hermes/patches/anthropic_billing_bypass.py')).read()
signals = re.findall(r'"([^"]+)"',
    re.search(r'_COMPLEX_SIGNALS = \[(.*?)\]', src, re.S).group(1))
agents = open(os.path.expanduser('~/.hermes/AGENTS.md')).read()
print(sum(1 for s in signals if s in agents.lower()))   # >0 ⇒ system prompt biases the score
```

## The fix — classify on the LAST HUMAN TURN ONLY

Scan only the most recent `role:"user"` message that carries real **text**:

- **Skip the system prompt** entirely (it's not the task — it's boilerplate).
- **Skip tool_result turns.** The Anthropic API encodes tool results as
  `role:"user"` messages whose content blocks are `type:"tool_result"` (no
  top-level `text`). Naive "last user message" grabs a tool_result mid-agentic-
  loop → a complex task that already upgraded would **flip back to Sonnet
  halfway through**. Iterate `reversed(messages)`, take the first user message
  that yields a non-empty plain-text block (`block.get("type")=="text"`), break.
- **Skip assistant turns** too (they echo task keywords back and would re-trip
  the score after the human moved on).

Result: per-turn classification on the actual current ask. Trivial turn after a
complex session correctly drops back to Sonnet; mid-loop tool results don't
downgrade; genuinely complex asks upgrade.

## Verify against the DEPLOYED code, not your reimplementation

Load the live patched module and exercise `_maybe_upgrade_model` directly —
don't trust a parallel reimplementation of the logic:

```python
import importlib.util
spec = importlib.util.spec_from_file_location("b",
    "~/.hermes/patches/anthropic_billing_bypass.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
sysblk = [{"type":"text","text":open(os.path.expanduser('~/.hermes/AGENTS.md')).read()}]
def run(model, msgs):
    kw={"model":model,"messages":msgs,"system":sysblk}
    return mod._maybe_upgrade_model(kw)
```

Required passing scenarios (all five MUST hold before syncing golden):
1. trivial turn + full system prompt present → stays Sonnet (the bug)
2. complex user turn → upgrades to `_HEAVY_MODEL`
3. mid tool-loop (complex ask, then a tool_result user turn) → STAYS upgraded
4. trivial turn after a complex earlier turn → drops back to Sonnet
5. already-`_HEAVY_MODEL` request → never downgraded (gate is `"sonnet" in model`)

## Activation + golden discipline (don't skip)

The patch (when loaded — bypass is ARCHIVED 2026-10-13) loads at process start via the sitecustomize import hook (CLI/py3.11 venv only; gateway uses local-guards plugin) → **edits
only take effect after a gateway restart** (gated). Until restart, the running
session keeps the OLD behavior.

(patch_guard retired 2026-10-16: there is no golden copy or self-heal for this
file any more.) If the bypass is re-enabled, back it up in git and
`py_compile` it BEFORE restarting.
