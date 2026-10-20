# Extending the hermes-claude-auth Bypass with Custom Runtime Patches

> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

How to add an in-process behavioral patch to Hermes (e.g. a guard that injects a
system-reminder mid-loop, a tool-call interceptor, a cost guardrail) by riding the
existing `hermes-claude-auth` sitecustomize import hook. This is the durable
pattern; the delegation-checkpoint guard built 2026-06-06 is the worked example.

## When to use this vs. a prompt rule

A static AGENTS.md / SOUL.md rule is NOT a real safeguard — under pressure the
model skims it (the WARNING block at the top of those files says exactly this).
When you need behavior that *actually triggers at the decision point* — counting
calls, watching token budgets, injecting guidance when a threshold trips — you
need a runtime patch, not another prompt line. Reach for this pattern when the
requirement is "fire X when the live session does Y," not "tell the model to
remember Z."

Honest ceiling: a mid-loop nudge injected as tool-result guidance is still a
SALIENCE bump, not a hard stop. It raises odds; it cannot compel a tool call.
A true hard-block (refuse the Nth call) is buildable but riskier — it can strand
legitimately-inline work and violates least-astonishment. Default to the nudge;
escalate to a block only on explicit user direction.

## The load mechanism (and its hard limit)

`~/.hermes/patches/anthropic_billing_bypass.py` is imported by the venv's
`sitecustomize.py` via a MetaPathFinder hook that fires when
`agent.anthropic_adapter` loads. The hook hard-codes `import
anthropic_billing_bypass` and **nothing else** — it does NOT scan `patches/` for
other modules. So a new `patches/my_patch.py` will NOT auto-load just by existing.

Four wiring options, each with a real, non-hideable tradeoff:
- **A — chain from `anthropic_billing_bypass.py`** (one import line): clean load,
  but the file is hermes-claude-auth-managed → **overwritten on its next
  `install.sh`** → silent disable.
- **B — edit venv `sitecustomize.py`**: same reinstall-overwrite risk + a venv edit.
- **C — `.pth` file in site-packages**: survives hermes-claude-auth reinstall, but
  **wiped by `hermes update`** (venv rebuild).
- **D — fold the logic directly into `anthropic_billing_bypass.py`**: most robust
  load (rides the one guaranteed import), but couples your patch to a third-party
  managed file. Same reinstall-overwrite risk as A/B.

There is NO seam that survives BOTH `hermes update` AND a hermes-claude-auth
reinstall without a re-apply step. Don't claim otherwise. Mitigation: a self-heal
check in an existing cron (Daily Backup / Infra Watchdog) that greps the live file
for a marker string and re-applies from a kept copy + pings the user if missing.
Turns "silently fails someday" into "self-heals and tells you."

## CRITICAL gotcha: the bypass is Anthropic-import-gated

`agent.anthropic_adapter` is imported **only** when
`agent.api_mode == "anthropic_messages"` (verified `agent/agent_init.py:584`).
A pure DeepSeek / OpenAI-wire session uses `chat_completions` mode and never
imports the adapter → the sitecustomize hook never fires → anything folded into
the bypass (Option D) never installs for that session.

BUT: the gateway is one long-lived process, and class-method patches are
process-wide. So the **first** Anthropic session in the process installs the
patch onto the shared class, and **every** later session (including DeepSeek
delegation subagents) inherits it. Net effect for an Anthropic-main setup: the
guard covers everything after the first Anthropic call. The only gaps:
(1) a non-Anthropic session that runs before any Anthropic session in a fresh
process; (2) switching main model entirely off Anthropic → adapter never imports
→ patch never installs at all. If you need true provider-independence, add a
second import line to `sitecustomize.py` itself (independent of the adapter) —
at the cost of a second managed-file touch point.

## The seam: patch the right class and method

VERIFIED FACTS (don't re-derive, but DO re-confirm against the live tree before
relying — internal names drift):
- The agent class is **`run_agent.AIAgent`** — NOT `RunAgent`. (A grep for the
  method's `def` line shows it in `run_agent.py`, but the *class name* is
  `AIAgent`. Always import the real module and check, don't trust the grep.)
- `AIAgent._execute_tool_calls(self, assistant_message, messages,
  effective_task_id, api_call_count=0)` is the per-tool-round instance method.
  It has `self` (→ per-session state, since each session is one instance) AND the
  live `messages` list (→ where to append a nudge). This is the right seam for
  cumulative tracking + injection.
- The existing `agent/tool_guardrails.py` controller is **per-turn**
  (`reset_for_turn`) and only covers repeated-failure / no-progress loops — it
  does NOT track cumulative cross-turn session state, so it's the wrong tool for
  session-level budgets. Use a wrapper on `_execute_tool_calls` instead.
- Live context size: `self.context_compressor.last_prompt_tokens` (the most
  recent API-reported prompt-token count = CURRENT context size). Note this is
  NOT the same as the audit's cumulative-summed input tokens — cumulative is
  always ≥ current (compression shrinks the latter). Using current-context at an
  80K floor is a tighter, less-noisy trigger; document the choice, don't pretend
  they're identical. Can be `-1` transiently right after compression — guard for
  `val > 0`.
- Inject guidance by appending a suffix to the last `role == "tool"` message,
  mirroring the existing `append_toolguard_guidance` convention so the model
  reads it as in-band loop guidance next turn.

## Deferred-install pattern (when the target module isn't loaded yet)

If your patch runs at adapter-import time but needs to patch `run_agent` (which
is imported only LAZILY, inside functions — verified: no module-scope import of
`anthropic_adapter` from `run_agent`, and importing the adapter standalone does
NOT pull in `run_agent`), do NOT force an eager `import run_agent` — that drags a
heavy import into adapter load. Instead, reuse the sitecustomize MetaPathFinder
idiom: arm a finder that patches the class the moment `run_agent` finishes
importing naturally. Handle both states: already-loaded (patch immediately) and
not-yet-loaded (defer via finder). See the worked code below.

## Mandatory safety shape

- Run the ORIGINAL method first; only then run guard logic. Never let the guard
  delay or interfere with real tool results landing.
- Wrap EVERY guard path in try/except that no-ops + writes ONE stderr line.
  A guard failure must NEVER break tool execution. (Verified with a forced-error
  test: original still runs, results still land, no exception escapes.)
- Idempotent install: set a marker attribute on the class; re-apply is a no-op.
- Latch one-shot side-effects (e.g. fire-once-per-session) on the instance so a
  fresh session resets naturally.
- Preserve the wrapped method's signature exactly — confirm with
  `inspect.signature` against the live class before and after.

## Build-and-verify workflow (do this in /tmp, NOT in ~/.hermes/patches)

Promotion to `~/.hermes/patches/` + gateway restart is a GATED change (managed
file + restart). Build and prove in isolation first:

1. Develop the module in a scratch dir (`/tmp/<name>-dev/`).
2. Write a synthetic harness: a FakeAgent shaped like the real class + fake
   `tool_calls` objects (`.function.name`), drive simulated turns, assert
   silent-below-threshold / fires-once / latch-holds / never-fires-after-target /
   guard-error-no-ops. No Hermes runtime needed for this — pure logic.
3. Verify the REAL binding in an isolated subprocess (gateway untouched):
   `cd /usr/local/lib/hermes-agent && venv/bin/python -c "import run_agent, mypatch;
   ..."` — confirm the real class name, signature match, marker set, idempotency,
   and (for deferred installs) that importing `run_agent` AFTER arming triggers
   the patch.
4. If folding into the bypass (D), ALSO confirm you didn't regress the bypass:
   run the complexity-classifier dry-run (`_maybe_upgrade_model` simple→sonnet,
   complex→opus) and `apply_patches(aa)` against the real adapter.
5. Produce the exact diff for the user, present report (what / load mechanism /
   risks / rollback), wait for greenlight, THEN write + restart + verify in logs
   (`grep "[your-marker] installed"` and confirm bypass still loads).

Rollback is always: restore the `.bak-<ts>` copy + `hermes gateway restart`.

## Worked code skeleton (deferred class-method wrapper)

```python
def _make_wrapper(original):
    def _wrapped(self, assistant_message, messages, effective_task_id, api_call_count=0):
        result = original(self, assistant_message, messages, effective_task_id, api_call_count)
        try:
            # ... cumulative counters on self._myguard_*, read live tokens,
            #     append suffix to last role==tool message when threshold trips,
            #     latch self._myguard_fired = True ...
            pass
        except Exception as exc:
            sys.stderr.write(f"[myguard] error (no-op): {type(exc).__name__}: {exc}\n")
        return result
    return _wrapped

def _patch_agent_class(run_agent_module):
    cls = getattr(run_agent_module, "AIAgent", None) or getattr(run_agent_module, "RunAgent", None)
    if cls is None or getattr(cls, "_myguard_patched", False):
        return bool(cls)
    orig = getattr(cls, "_execute_tool_calls", None)
    if not callable(orig):
        return False
    cls._execute_tool_calls = _make_wrapper(orig)
    cls._myguard_patched = True
    return True

def _install():
    existing = sys.modules.get("run_agent")
    if existing is not None and getattr(existing, "AIAgent", None) is not None:
        _patch_agent_class(existing); return
    from importlib.abc import MetaPathFinder
    from importlib.util import find_spec as _find_spec
    class _Finder(MetaPathFinder):
        _done = False
        def find_spec(self, fullname, path=None, target=None):
            if fullname != "run_agent" or self._done:
                return None
            if self in sys.meta_path: sys.meta_path.remove(self)
            try: spec = _find_spec(fullname)
            finally:
                if self not in sys.meta_path: sys.meta_path.insert(0, self)
            if spec is None or spec.loader is None: return None
            orig_exec = getattr(spec.loader, "exec_module", None)
            if not callable(orig_exec): return None
            finder = self
            def patched_exec(module):
                orig_exec(module); finder._done = True
                try: _patch_agent_class(module)
                except Exception as exc:
                    sys.stderr.write(f"[myguard] deferred error (no-op): {exc}\n")
            spec.loader.exec_module = patched_exec
            return spec
    sys.meta_path.insert(0, _Finder())
```

## Env-tunable + disable convention

Make thresholds env-tunable and add a kill switch, matching the bypass's own
style: `HERMES_<GUARD>_<KNOB>` ints with safe defaults, and
`HERMES_<GUARD>=off` (also accept 0/false/no/disabled) to disable entirely
without removing the file.
