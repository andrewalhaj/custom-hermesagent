# Complexity Classifier — Configuration & Tuning

> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

The classifier lives in `~/.hermes/patches/anthropic_billing_bypass.py` and runs
as part of the OAuth bypass hook (`patched_build` → `_maybe_upgrade_model`).  It is
called on **every OAuth Anthropic request** and decides whether to upgrade Sonnet → Opus.

---

## Architecture

```
patched_build()                         # line ~900 in bypass patch
  └─ apply_claude_code_bypass(...)      # standard OAuth transforms
  └─ _maybe_upgrade_model(result)       # complexity check (this classifier)
       └─ _classify_complexity(...)      # keyword scanner
```

Call sequence: `apply_claude_code_bypass` runs first (system prompt relocation,
billing header, tool names), THEN the classifier inspects the transformed payload.
This means the classifier sees the FINAL system + messages that will be sent.

---

## Tunables

All at the top of the classifier block in `anthropic_billing_bypass.py`:

| Variable | Default | Effect |
|---|---|---|
| `_COMPLEX_SIGNALS` | 33 patterns | Keywords that contribute to complexity score |
| `_COMPLEX_SCORE_THRESHOLD` | 2 | Signals needed for upgrade |
| `_COMPLEX_LEN_THRESHOLD` | 2000 | Prompt length (chars) — lowers threshold to 1 |
| `_OPUS_MODEL` | `"claude-opus-4-8"` | Target model for upgrades |

**Upgrade logic:**
- `score >= 2` → **upgrade**
- `score == 1 AND prompt_len > 2000` → **upgrade** (long, single-signal = likely complex)
- Otherwise → stay on Sonnet

**Gateway:** the model field only gates on `"sonnet"` in the name — an already-Opus request
is never downgraded.

---

## Tuning Workflow

1. **Add/remove signals:** Edit `_COMPLEX_SIGNALS` list. Use lowercase substrings —
   the classifier lowercases all text before matching.

2. **Adjust thresholds:** Bump `_COMPLEX_SCORE_THRESHOLD` to 3 for stricter gating,
   or lower to 1 for eager upgrades.

3. **Dry-run:** Test with the Python snippet in the skill before restarting:

   ```bash
   python3 -c "
   import sys; sys.path.insert(0, os.path.expanduser('~/.hermes/patches'))
   from anthropic_billing_bypass import _maybe_upgrade_model

   # Test: should match your new signals
   api = {'model': 'claude-sonnet-4-6', 'messages': [
       {'role': 'user', 'content': 'your test prompt here'}
   ]}
   print(_maybe_upgrade_model(api))
   "
   ```

4. **Apply:** Restart the gateway: `hermes gateway restart`

5. **Verify in logs:** After a complex request, check gateway logs:
   ```bash
   journalctl --user -u hermes-gateway -n 50 | grep "Complexity upgrade"
   ```

---

## Signal Design Principles

- **Keep signals distinctive.** `"deploy"` is better than `"fix"` — false positives waste Opus tokens.
- **Multi-word signals are fine.** `"across the codebase"` won't match `"codebase"` alone.
- **Avoid common English words.** `"system"` alone would match nearly everything.
- **Test before committing.** Run the dry-run snippet with realistic prompts.

## When Opus Is Wasteful

Do NOT add signals for tasks that Sonnet handles fine:
- Simple Q&A / factual lookup
- Single-line code edits
- Configuration changes (Sonnet is faster/cheaper and does these fine)
- Routine health checks / status probes
- Boilerplate generation (CRUD endpoints, standard config files)

## When to Add a Signal

Add a signal when you find yourself manually switching to Opus for a task class
that Sonnet consistently underperforms on. Common candidates:
- Complex refactors touching 5+ files
- Security audits with cross-system analysis
- Architectural design requiring tradeoff reasoning
- Debugging multi-service failure cascades
- Performance optimization with profiling data
