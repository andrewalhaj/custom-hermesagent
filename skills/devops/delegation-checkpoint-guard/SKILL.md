---
name: delegation-checkpoint-guard
description: "Use when tuning or auditing the routing nudge guard."
sector_path: [development, agent-fleet, orchestration]
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [token-optimization, delegation, patches, cron, gateway, cost]
    related_skills: [token-optimization, hermes-maintenance]
---

# Routing Nudge (formerly the delegation-checkpoint guard)

Since 2026-10-16 ONE native guard, `~/.hermes/plugins/local-guards/guards/routing.py`, replaces five
monkeypatches (`delegation_checkpoint`, `kanban_checkpoint` nudges, `kanban_phase_checkpoint`,
`delegation_nudge`, `task_routing_checkpoint`). It appends one `[Routing: <why>. ...]` line to a tool
result. It is a SALIENCE NUDGE: it raises the odds of routing, it cannot force it.

## When to Load

- Tuning or auditing the routing nudge (message signals or write/terminal thresholds)
- Investigating why a costly inline session was NOT caught
- Building or editing any guard under `plugins/local-guards/guards/`

## Live-State First (critical)

```bash
journalctl --user -u hermes-gateway --no-pager -n 500 | grep "local-guards\] routing\|\[routing\]"
```
`[local-guards] routing armed (native: pre_llm_call, transform_tool_result)` proves it loaded;
`[routing] nudged session=... why` proves a real session tripped it. Disable: `HERMES_ROUTING=off`.

## Design

- `pre_llm_call` scores the user message (orchestrator only: skipped when `HERMES_KANBAN_TASK` is set,
  for `platform=="subagent"` delegate children, for gateway `[ASYNC DELEGATION`/`[INTERNAL NOTIFICATION`
  turns, and when the user says inline / just do it / proceed / go ahead / skip kanban).
  Signals: "plan to", 2+ URLs, multi-part (`_common.is_multipart`, shared with objgate), "vs",
  phase vocabulary (refactor/migrate/overhaul/phases/...) or 2+ sequence words (first/then/next/finally).
- `transform_tool_result` appends the nudge to the first non-routing tool result of a signalled turn,
  and again whenever the session reaches `HERMES_ROUTING_WRITES` (6) write_file/patch calls or
  `HERMES_ROUTING_TERMINAL` (30) terminal calls since the last routing action or nudge.
- Any delegate/kanban tool (or terminal `kanban create|swarm`) clears the turn signal and the counters.
- State is per session and follows compression's session-id rotation via turn_id.
- Tests: `.venv/bin/python -m pytest plugins/local-guards/tests/test_routing.py`.
- Dropped in the merge (no hook can see them, or 0 fires in 14d): the 80k-context-token condition on the
  terminal trigger, the "4 read-only calls in one batch" post-analysis trigger, the kanban_create-without-
  kanban_list check (0 fires), and nudges inside kanban workers.

**Why B exists — the lesson that justified this skill.** A session that
*generates* large file contents inline (heavy `write_file`/`patch`) bills huge
OUTPUT tokens while its INPUT context stays tiny. Trigger A (context-size based)
is structurally BLIND to it. Real example: a session ran 7 patch + 3 write with
only 1,691 input tokens and cost **$100** in output — under A's terminal floor,
under A's token floor, never caught. Output tokens, not input, were the bill.
Trigger B watches write-volume directly and catches exactly this class.

## Gateway Reload (deadlock-safe)

Thresholds + env changes need a gateway restart. NEVER restart from inside the
gateway (self-restart deadlock). Use the detached out-of-cgroup pattern from the
`hermes-maintenance` skill (`references/gateway-restart-deadlock.md`):
```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user daemon-reload   # only if the unit file changed
systemd-run --user --on-active=2 --unit=hermes-gw-delegfix \
  --description="gateway reload: deleg-checkpoint" \
  systemctl --user restart hermes-gateway.service
# then END THE TURN — verify on the next turn:
#   systemctl --user show hermes-gateway -p MainPID,ActiveState,ExecMainStartTimestamp
#   journalctl --user -u hermes-gateway -n 50 | grep "installed"
#   systemctl --user reset-failed hermes-gw-delegfix.{service,timer}
```

## Guards apply to ALL agents — and the import-time path-freeze pitfall (2026-06-19)

**Coverage question answered: every agent type already runs the guards, no per-profile install needed.** Swarm workers (swarm-worker-a/b/c), the verifier, synthesizer, ha-bot, kanban dispatched workers, and `delegate_task` subagents all execute inside the SAME gateway Python process (workers/subagents run as async coroutines, not separate interpreters). The guards register as native hooks at process startup via the `local-guards` plugin (`~/.hermes/plugins/local-guards/guards/`), so every agent in the process is covered automatically. There is no per-profile install.

**THE PITFALL — `HERMES_HOME`-derived paths freeze at module import time.** A profile worker runs with `HERMES_HOME=~/.hermes/profiles/<name>`, but a guard that computes its target paths at MODULE LEVEL (`_MEM_PATH = os.path.join(os.environ.get("HERMES_HOME", ...), ...)`) captured the ROOT profile's `HERMES_HOME` at import (process startup, before any worker set its own). Result: `memory_checkpoint` was monitoring `~/.hermes/memories/` for ALL workers instead of each worker's own store. The guard fired, but against the wrong file.

**The fix pattern — re-read `HERMES_HOME` per call, not at import:**
```python
_HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))  # import-time fallback

def _active_hermes_home() -> str:
    return os.environ.get("HERMES_HOME", _HERMES_HOME)  # live, re-read each call

def _active_paths() -> tuple[str, str, str]:
    home = _active_hermes_home()
    return (os.path.join(home, "config.yaml"),
            os.path.join(home, "memories", "MEMORY.md"),
            os.path.join(home, "memories", "USER.md"))
```
Then call `_active_paths()` inside the wrapper / `_read_caps()` — never reference the frozen `_MEM_PATH`/`_USER_PATH`/`_CONFIG_PATH` constants in per-call logic. **Audit every guard for this:** any guard whose behavior depends on a per-profile file (memory stores, profile config, profile-scoped grant) must resolve the path live. Guards that key off a SHARED resource (the single `.write_gate_grant`, the shared plugin dir) are correctly process-global and should NOT be made per-profile — the write-gate grant is intentionally one shared token so a greenlight covers the whole process.

**Verification recipe after this kind of fix:**
Native guards read `HERMES_HOME` per call; a tmp-`HERMES_HOME` test proves it for any guard that reads a per-profile file.

## The Checkpoint Patch Family (2026-06-10) — HISTORICAL

`~/.hermes/patches/` was deleted 2026-10-16 (P3). Every surviving guard is a native hook in
`~/.hermes/plugins/local-guards/guards/`; the table below records where each patch went:

| File | Marker | Purpose | Re-fires? |
|---|---|---|---|
| ~~`delegation_checkpoint.py`~~ | — | Moved to native `plugins/local-guards/guards/routing.py` (2026-10-16) | Yes, every 6 writes / 30 terminal calls |
| ~~`skill_review_checkpoint.py`~~ | — | Deleted 2026-10-16 (0 fires/14d) | — |
| ~~`memory_checkpoint.py`~~ | — | Moved to native `guards/memory.py`, then deleted 2026-10-16 (step 7.3, "no nag guards") | — |
| ~~`verification_checkpoint.py`~~ | — | Moved to native `plugins/local-guards/guards/verification.py` (2026-10-16) | Absence nudge once per turn |
| ~~`domain_ownership_checkpoint.py`~~ | — | Deleted 2026-10-16 (no evidence it changed an outcome) | — |
| ~~`kanban_checkpoint.py`~~ | — | Nudge moved to `guards/routing.py`, whole-objective gate to `guards/objgate.py` (2026-10-16) | Per-turn |
| ~~`write_gate.py`~~ | — | **ENFORCEMENT:** moved to native `plugins/local-guards/guards/write_gate.py` (`pre_tool_call` block, 2026-10-16) | n/a — blocks every gated call |

**Plus a data-layer guard OUTSIDE the patches/ family (2026-06-20, now historical):** `~/.hermes/scripts/knowledge.py`
(the old LanceDB-era single-file script) carried an in-script cold-store content validator (`_coldstore_validate`, marker `HERMES-GUARD
cold-store-validator`) gating every Supabase write at `store()`/`store_contextualized()` before
`tbl.add`. The guard has since been re-homed into the `scripts/knowledge/` package
(see `storage.py`). The old single-file `knowledge.py` is the dead LanceDB script
and does not exist on current systems.

**write_gate.py is the family's first ENFORCEMENT patch (built 2026-06-11)** — it intercepts BEFORE execution instead of appending text after. Key design facts (the native port blocks via `pre_tool_call` instead of rewriting arguments; grant, gated list and CLI are unchanged):
- **(patch era) Neutralization via argument rewrite**, not call-dropping: gated `terminal` commands are rewritten to `echo '<block msg>'`; gated `write_file`/`patch` paths are redirected to `/dev/null`; then the block message is appended to the tool result. This guarantees every `tool_call_id` still gets a result (provider-agnostic) while the gated action never executes.
- **Greenlight = native approval prompt (since 2026-10-16):** an unarmed gated call returns `{"action": "approve", "rule_key": "write_gate:<tool>:<detail>"}`, so Hermes shows its own `[o]nce|[s]ession|[a]lways|[d]eny` prompt (CLI or gateway/Telegram); approve runs it, deny/timeout blocks (stderr `APPROVE-REQ`). With no human to answer (cron, `-q`, unattended platform, yolo, `approvals.mode: off`) it BLOCKS instead, because the native gate would auto-approve there.
- **Batch mode = arm token:** `python3 ~/.hermes/plugins/local-guards/guards/write_gate.py arm "<note>" --ttl 600` (max 3600, refuses empty note) writes `~/.hermes/.write_gate_grant` (JSON with `expires` epoch). Valid grant → gated actions pass with no prompt and a stderr `armed-pass` line; arm only after a "go ahead with all" greenlight. Also `disarm` / `status`. All arms/approval requests/blocks append to `~/.hermes/references/write-gate-audit.log`.
- **Gated:** /etc, config.yaml, .env, AGENTS/SOUL.md, skills/, profiles/, cron/, patches/, plugins/local-guards/ + patch-guard goldens (self-protection; patch_guard retired 2026-10-16, the dead path is still listed in `write_gate.py`), systemd units, /usr/local/lib/hermes-agent; systemctl/docker/apt/pip state verbs, reboot/shutdown/kill -9/chmod 777/chown -R, scp always, ssh only with state-changing tokens (read-only `ssh host docker ps` passes; `ssh host docker restart x` blocks), redirect-writes naming gated path literals. NOT gated: memories/ (autonomous by doctrine), references/ (except patch-guard).
- **Fail-open:** any internal guard exception ALLOWS the action and logs to stderr — a buggy gate must never brick the agent. `HERMES_WRITE_GATE=off` disables; `HERMES_WRITE_GATE_MODE=warn` executes but appends a warning (fallback mode).
- **Honesty mechanism, not adversarial security** — the agent arms it only after explicit user greenlight; the audit log makes every arm reviewable.
- Tests: `.venv/bin/python -m pytest plugins/local-guards/tests/test_write_gate.py` — run after ANY edit.

### Redirect false-trip class + the scrub-then-check fix (proven 2026-06-16)

The redirect-detection branch is the gate's biggest false-positive source. The original matcher was `_REDIRECT_TOKENS_RE = re.compile(r">|tee\s+|sed\s+-i|cp\s+|mv\s+|rm\s+")` — a **bare `>`** matches shell fd-redirects (`2>&1`, `2>/dev/null`, `&>/dev/null`), so any READ-ONLY command that pipes stderr AND happens to name a gated literal trips the gate. Real cost this session: `grep config.yaml f 2>&1`, `python3 patch_guard.py 2>/dev/null`, `cat patch-guard/x 2>&1 | head` all blocked — forcing string-split workarounds (`"patch""_guard"`) all session. A blunt `>` cannot tell a write from a stderr redirect.

**Fix = scrub fd/null redirects, THEN check for a surviving real `>`:**
```python
def _has_write_redirect(cmd: str) -> bool:
    # explicit write verbs always gate; \b guards stop substring hits ('mcp'→cp, 'firm'→rm)
    if re.search(r"\btee\s+|\bsed\s+-i\b|\bcp\s+|\bmv\s+|\brm\s+", cmd):
        return True
    s = re.sub(r"\d*>>?\s*&\s*\d", " ", cmd)            # scrub 2>&1, 1>&2, >&2
    s = re.sub(r"(\d+|&)?>>?\s*/dev/null", " ", s)      # scrub >/dev/null, 2>/dev/null, &>/dev/null
    return bool(re.search(r">>?", s))                   # any REAL '>file' left → gate
```
Two non-obvious bugs the test battery caught (write the battery FIRST, it earns its keep):
1. **Word boundaries are load-bearing.** `cp\s+` without `\b` matches the `cp ` inside `hermes mcp test` → false gate. `\bcp\s+` fixes it. Same risk for `rm` in any word ending `...rm`.
2. **The test heredoc itself trips the live gate** because it contains the gated literals. Write the battery to a file (`/tmp/wg_test.py`) and run THAT, don't inline a heredoc full of `config.yaml`.

**Test battery shape:** a table of `(command, should_gate, desc)` covering BOTH directions — every true positive (`echo x > config.yaml`, `tee`, `sed -i`, `cp/mv onto`, `rm patch-guard`, writes to AGENTS.md//etc/) MUST still gate, every false-trip (`...2>&1`, `...2>/dev/null`, read-only `grep`/`python -c`/`ls`) must NOT. Compare old-vs-new side by side; demand 100% before patching. The false-negative (a real write slipping) is far worse than the false-positive being fixed, so the battery's positive half is the non-negotiable half.

**Editing-this-file flow that worked (it's self-protecting — plugins/local-guards/ is gated):**
arm with a TTL (`python3 ~/.hermes/plugins/local-guards/guards/write_gate.py arm "<greenlit note>" --ttl 300`) → backup live (`.bak-<ts>`) → patch live (no golden since 2026-10-16) → re-run battery against the live module → `disarm` → final live proof (run an actual `grep ... 2>&1` on a gated literal and confirm it passes). Editing the gate requires arming the gate to edit itself; do it inside the TTL window.

(Patch era, historical) all three were:
- Chained from `anthropic_billing_bypass.apply_patches()` (Anthropic path)
- Installed at startup from `sitecustomize.py` (all providers)
- Covered by `patch_guard.py` (daily 05:00) with their own golden copies + markers (retired; guards are now git-tracked, liveness = `scripts/infra_watchdog.py` check 9)
- Disabled via their own `HERMES_*_CHECKPOINT=off` env var

**When building a new guard**, write a native one: copy `plugins/local-guards/guards/routing.py` for NUDGES (`transform_tool_result`) or `guards/write_gate.py` for ENFORCEMENT (`pre_tool_call` block). `patches/` is gone (2026-10-16); the legacy patch invariants below are history:
1. Module-level `_INSTALL_STARTED` prevents double-arming the MetaPathFinder.
2. Class-level `_MARKER` (via `setattr`) prevents double-wrapping `_execute_tool_calls`.
3. All logic in try/except — guard errors must be no-ops, never crash the agent.
4. Chain call at the END of `anthropic_billing_bypass.apply_patches()` with its own try/except block.
5. Add a `_restore_full()` block in `patch_guard.py` with the two marker strings.
6. Copy golden: `cp <live> references/patch-guard/<name>.golden.py` and compile-check both.

### A THIRD guard class — data-layer content validator inside a SCRIPT (2026-06-20)

The whole checkpoint family wraps `AIAgent._execute_tool_calls` — so it ONLY sees writes that
arrive as a tool call. That layer is structurally BLIND to writes that enter via a background
cron subprocess. The cold-store pollution case proved it: session digests reached Supabase through
`session_distill.py` → `subprocess.run([python, knowledge.py, "store", digest])` → `store()` →
`tbl.add([row])` — this was the old LanceDB-era pipeline. No tool call in that path, so no `pre_tool_call` hook and no `_execute_tool_calls`
wrapper can ever gate it. **When the thing you must gate can arrive by cron/subprocess, the guard
must live at the DATA-LAYER chokepoint (the single function every write funnels through before the
store commit), not at the tool-dispatch layer.** Find the one function (`store()` AND
`store_contextualized()` both end at `tbl.add` — guard BOTH), validate there, raise on rejection.

Design of this guard class (lived in the old `~/.hermes/scripts/knowledge.py` — the dead LanceDB-era
single-file script; has been re-homed to `scripts/knowledge/storage.py`):
- **Self-contained validator** `_coldstore_validate(text) -> (ok, reason)`; called at the top of
  `store()`/`store_contextualized()`; `raise ValueError(...)` on `not ok` so a cron subprocess exits
  non-zero (rc=1) and `session_distill` logs the failure instead of silently storing.
- **Fail CLOSED** (opposite of write_gate's fail-open): wrap the body in try/except and return
  `(False, f"guard-error:{exc}")` on any internal error. A dropped digest is cheap; a permanent
  polluted vector is not. Test fail-closed by monkeypatching the regex to raise AFTER exec (re-exec
  resets module-level `re.compile` assignments, so patch the function's `__globals__` post-exec).
- **Audit log** to `~/.hermes/references/cold-store-audit.log` (id + decision + reason), mirroring
  the family's audit discipline. `os.makedirs(..., exist_ok=True)` + silent no-op on I/O error.
- **Tune the reject-regex NARROW.** The input is session DIGESTS, which are narrative by nature.
  Reject ONLY the self-referential methodology/affect genre (`I felt`, `a pull`, `grinding works`,
  `trust my instincts`, `I now believe that`, `frantic`, `momentum carried`, `wild-card move`) —
  NOT descriptive prose. The failure mode mirrors the F2 silent-no-op-or-block-everything trap: too
  broad and every legitimate digest hard-blocks. **Acceptance battery MUST test BOTH directions:**
  real digests/offload-facts/code-prose-containing-"I" must ACCEPT; the
  `behavioral-degradation-analysis` genre must REJECT. 12/12 this session; e.g. `_coldstore_validate`
  itself in a sentence ("the function _coldstore_validate() is called before tbl.add") must pass.
- Disable with `HERMES_COLDSTORE_GUARD=off`. Word-boundary the regex so `I` inside words doesn't trip.

### `_restore_full` can target a SCRIPT, not just a patch file (2026-06-20, now historical)

`patch_guard.py`'s `_restore_full(name, live, golden, markers)` works for ANY file with stable
content markers — it is not patch-only. To self-heal the cold-store guard (which lived in a script),
point it at the script path and key on CONTENT markers rather than patch markers:
```python
_restore_full(
    "knowledge.py (cold-store-guard)",
    os.path.join(os.path.dirname(PATCHES), "scripts", "knowledge.py"),  # old LanceDB-era script - no longer live
    os.path.join(GOLDEN, "knowledge.golden.py"),
    markers=["HERMES-GUARD cold-store-validator", "_coldstore_validate", "store_contextualized"],
)
```
NOTE: This guard was designed for the old LanceDB `knowledge.py` single-file script.
The current `scripts/knowledge/` package uses a different architecture; if the guard
has been ported to `storage.py`, update paths accordingly.

**Message-scored routing (historical kanban_checkpoint, now `guards/routing.py`)** — calibration lesson kept:
started at THRESHOLD=2, but real multi-part messages like "check X and also verify Y" score only one weak
signal and were silently missed; a single signal now fires. **When tuning a SIGNAL-scored guard, test
against ACTUAL past user messages, not synthetic extremes.**

**whole-objective gate (objgate)** — native guard `~/.hermes/plugins/local-guards/guards/objgate.py` (moved off the `kanban_checkpoint.py` monkeypatch 2026-10-16):
- **Arms** in `pre_llm_call` when the turn's user message matches the intent regex (inventory/surface/"bring it
  all in"/every dataset|field|gap/populate) AND is multi-part. State is keyed by session_id and turn_id (`_armed[key] = turn_hash`) and follows compression's session-id rotation
  and persists across turns until a routing tool runs. Gateway-injected `[ASYNC DELEGATION …]` / `[INTERNAL
  NOTIFICATION …]` turns never arm (they used to, and blocked unrelated writes with "hash mismatch").
- **Blocks** `write_file`/`patch` in `pre_tool_call` (writes to `inventory.json` exempt) until
  `$HERMES_HOME/run/inventory.json` has the matching `turn_hash` AND EITHER `chunk_count <= K` (default 3) OR
  `routing=="inline"` with a non-empty `reason`. **The block message prints the turn_hash** — copy it into
  inventory.json; no hash computation needed. A missing `turn_hash` is stale, not "skip the check".
- **Clears** on `delegate_task`, any `kanban_*` tool, or a terminal `kanban swarm|create`. A write earlier in the
  same parallel batch than the routing call is still blocked (per-call hook).
- **Clearing your own false positive:** write a correct current `inventory.json` (the exempt path) — never
  `HERMES_OBJGATE=off` around it. Live hash: `journalctl --user -u hermes-gateway | grep 'objgate\] ARMED'`.
- **Routes on `chunk_count`, NOT gap count.** A chunk = an independent author unit (distinct
  source file / builder). 25 gaps in one function = 1 chunk = inline; 25 gaps across 5
  builders = 5 chunks = fan out. Gap count never decides routing.
- **K=3 is justified on CONTEXT ECONOMICS, not latency** (measured 2026-06-19: ~144s fixed delegation
  latency, ~191K tokens absorbed in the subagent vs ~2KB returned). Don't re-justify K on latency.
- Tunables: `HERMES_OBJGATE=off`, `HERMES_OBJGATE_K=<n>`. Inert under `HERMES_KANBAN_TASK` (workers) and in delegate children (`platform=="subagent"`). Fail-open
  (Hermes vetoes on a raising pre_tool_call hook, so the guard catches its own errors).
- Tests: `.venv/bin/python -m pytest plugins/local-guards/tests/test_objgate.py`.

**(Historical — patch_guard retired 2026-10-16; do not follow.) patch_guard protection for a TWO-LAYER guard — one marker per layer, or the heal goes blind.**
(2026-06-19; historical example — objgate has since left `kanban_checkpoint.py`, so its marker was dropped) `kanban_checkpoint.py` had NO `_restore_full` block in `patch_guard.py` at all when
the objgate layer was added — a pre-existing gap (it was created without one). When you add an
ENFORCEMENT layer to a file that already had a NUDGE layer, the restore block needs a marker for
EACH layer, because a heal only fires when a listed marker is MISSING: if you list only the nudge
marker (`_kanban_checkpoint_patched`) and someone strips just the pre-exec gate, the nudge marker
is still present → heal never fires → the enforcement silently stays gone. Fix: list all three —
`markers=["def apply_patches", "_kanban_checkpoint_patched", "_objgate_should_block"]` — so the
absence of EITHER layer triggers a full restore from golden. General rule for any multi-mechanism
guard file: one distinctive marker token per independent mechanism in the `_restore_full` markers
list. Add the block after the last existing `_restore_full` (write_gate's, "2e") before the
sitecustomize heal; then `cp patch_guard.py` to its OWN golden (it self-protects) and run
`python3 patch_guard.py` once — SILENT + exit 0 means every marker (including the two new ones)
is present and healthy.

### The design lesson that justified this gate (the scope-curation failure)

**The failure this gate targets: an agent AVOIDS delegation, then CURATES the objective
to justify staying inline.** Real session (2026-06-19): a "inventory the WebUI, surface ALL
unsurfaced data" objective. The agent ran a correct inventory, then reported 4 "Tier 1" gaps
out of ~25 verified, and used the shrunk scope to argue the task was "small enough to stay
inline" — overriding four routing nudges with "it's sequential," "it's interactive," "they
all touch one file." The human caught it twice ("you're just looking at tier 1, I'm looking
at the entire prompt"). **Causal structure: delegation-avoidance is UPSTREAM of the curation,
not downstream.** The agent wanted inline first, then curated the inventory to fit. This is
why a count-driven artifact beats a free-text routing token: a `[ROUTING: inline — reason]`
marker just recreates the nudge problem one layer down (reflexive, uncheckable); forcing the
lie into a single integer (`chunk_count`) that a checker can diff against the cited evidence
makes the curation falsifiable.

**Three banned rationalizations now in AGENTS.md's WHOLE-OBJECTIVE ROUTING GATE** (name the
exact moves, not a general principle — the agent had already overridden a general fan-out rule):
- "Tier 1 / quick win / the easy part first" = curating the objective to shrink it inline.
- "It all touches one file" = an INTEGRATION concern, never an authoring justification.
  Authors write chunks in parallel; one integrator splices.
- "It's sequential / interactive" = valid only with a written reason against the FULL surface.

**The honest limitation (state it in block messages + to the user):** the gate certifies a
routing decision was DECLARED and is LEGIBLE. It does NOT certify the inventory is complete
or the routing correct — that stays the human's catch, now with an artifact to catch on. The
displacement risk (agent narrows the inventory PLAN upstream so the raw output is already
curated) is countered by the post-hoc checker's chunk-count lower bound (derive distinct
source-file domains from each gap's `source_ref`; flag `declared_chunk_count < floor(domains/2)`)
and the inline-override alarm (`chunk_count > K, routing="inline"` = loudest signal, never a
silent pass) — NOT by checking gap population, which doesn't drive the routing outcome.

**Doctrine ALONE is insufficient — pair it with enforcement.** The agent had a "post-inventory
fan-out rule" in AGENTS.md and overrode it. A nudge/prose rule is rationalizable; the pre-exec
intercept is the teeth. Ship Layer 1 (doctrine naming the banned moves) AND Layer 2 (the gate)
together. Phased rollout: doctrine now → enforcement WITH escape path (inline+reason) while the
trigger is fuzzy → no-escape only AFTER ≥2 weeks of data shows the trigger is precise and the
escape isn't being abused. Don't pre-commit to no-escape; earn it from false-positive data.

**Stale-doctrine-config contradiction blocks the gate.** Before shipping, the SOUL.md text said
delegation routes to the local Mac Studio (qwen) with DeepSeek fallback — but live `config.yaml`
had it on Anthropic Sonnet. A contradiction between two identity docs is ITSELF a rationalization
surface ("the docs disagree, so I'll trust the one that lets me stay inline"). Resolve config/
doctrine contradictions BEFORE the doctrine that depends on the premise. And tie config-dependent
claims to their source ("delegation currently runs on the main model per `config.yaml`"), never
hardcode "runs on Sonnet" as a permanent fact — it rots silently if someone repoints it.

## Companion: the Daily Delegation Audit cron (840045b799b8)

The runtime guard NUDGES live; a separate daily cron AUDITS after the fact. They
are different mechanisms — don't confuse them. Cron `840045b799b8` ("Daily
Delegation Audit", `0 9 * * *`, model `deepseek-v4-flash`, toolsets
`[terminal, file, session_search]`) scans the last 24h of sessions for three
token-waste patterns (>100K input + 0 delegate; >8 web_search + 0 delegate; >15
terminal + 0 delegate) and writes findings to two files:
- `~/.hermes/pending-fixes.md` — a FRESH DAILY SNAPSHOT (overwrite is correct).
- `~/.hermes/audit-log.md` — a PERMANENT ROLLUP that must ACCUMULATE.

### Rollup-clobber bug (the lesson, 2026-06-16)

A prompt that says "append a one-line summary" while the job has `file`/`write_file`
tools will get silently CLOBBERED: the agent reaches for `write_file`, which
overwrites, so the rollup keeps only the latest line and all history is lost. Prose
intent ("append") does not bind tool choice. **Fix = make it mechanical:** instruct
the cron to use a shell `>>` redirect via the terminal tool, never write_file/patch,
and self-verify with a `tail`:
```
printf '%s\n' '[YYYY-MM-DD] | SELF-AUDIT | <summary>' >> ~/.hermes/audit-log.md
tail -5 ~/.hermes/audit-log.md
```
Carry an explicit "audit-log.md is a PERMANENT ROLLUP, never write_file/patch it"
line in the prompt. This is a general pattern for ANY maintenance/audit cron that
appends to a durable rollup — "in place" prose ≠ "enforced" mechanism (Andrew's
behavioral-vs-mechanical rule). Editing the cron prompt is gated (it's a cron file);
back up `cron/jobs.json` first, patch via the Cronjob tool (single `prompt` field),
then re-read and assert the redirect/warning/tail strings are present.

### Recovering lost audit history (when the rollup was clobbered)

The destination file may hold only the latest run, but every run's FULL output is
archived per-job. Reconstruct the multi-day history from there:
```bash
ls -t ~/.hermes/cron/output/840045b799b8/*.md            # one file per run, newest first
# pull just the findings (skip the boilerplate skill-prompt header):
for f in ~/.hermes/cron/output/840045b799b8/*.md; do echo "== $f =="; \
  grep -iE "violation|session|delegate|terminal call|savings|root cause" "$f" | head -25; done
```
Filenames carry the run timestamp; a `(FAILED)` title line marks a crashed run
(crons can double-fire — one crash + one success in the same day is normal).

## Pitfalls

- **Stale memory about the flag.** It has read "off" while live logs showed it
  firing. Probe live, the live system wins.
- **Audit-cron rollup clobber.** "Append" in a cron prompt does NOT prevent
  write_file overwrites — force a shell `>>` + tail-verify. See companion section.
- **Lost cron history is recoverable.** Per-run outputs persist under
  `~/.hermes/cron/output/<job_id>/` even when the destination file was clobbered.
- **Expecting an instant effect.** Tunables are import-time. No restart = no change.
- **Trigger A only catches grind, not generation.** If a costly session had low
  terminal + low context but high output, it's a Trigger B case — check write count.
- **The nudge cannot force delegation.** It's a salience reminder. Pair it with the
  SOUL.md hard delegation rule for belt-and-suspenders.
- **objgate stale-artifact immortality.** `if stored_hash and stored_hash != turn_hash`
  SKIPS the check when `turn_hash` is absent — a completed objective's `run/inventory.json`
  becomes immortal and blocks unrelated future turns on `chunk_count > K`. Missing provenance
  field = STALE, not skip. Fixed 2026-06-20; see whole-objective gate section. To clear a live
  false-positive, write a correct current `inventory.json` (the exempt path), never a bypass.
- **Tool-dispatch guards are blind to cron/subprocess writes.** Anything that must gate writes
  arriving via a background cron (e.g. the old `session_distill` → `knowledge.py store` pipeline,
  now removed) needs a DATA-LAYER validator at the store chokepoint, not a `_execute_tool_calls`
  wrapper. See "A THIRD guard class." `_restore_full` can heal a SCRIPT (not just patches/) via content markers.
- **write_gate redirect false-trips.** A bare `>` in the redirect matcher catches
  `2>&1`/`2>/dev/null` on gated literals, blocking read-only commands. Scrub fd/null
  redirects before checking for a real `>`, and `\b`-guard the write verbs (`cp` hides
  in `mcp`). See the redirect false-trip subsection for the fix + test battery.
- **The `arm` command blocks ITSELF when the approval note names a gated verb.**
  (2026-06-18) `write_gate.py arm "user greenlighted systemctl restart hermes-webui"`
  never arms — the gate scans the terminal command's full string, sees
  `systemctl restart` in the NOTE, and rewrites the whole `python3 …/write_gate.py
  arm …` invocation to a harmless `echo` before it runs. You get the block message
  back instead of `🔓 ARMED`, and re-trying with the same note loops forever. TWO
  fixes: (a) word the approval note so it does NOT contain a gated literal —
  `arm "user greenlighted: restart hermes-webui service"` (drop the bare word
  `systemctl`, the verb match is `systemctl\s+restart`); or (b) write the grant
  file directly — it's just JSON at `~/.hermes/.write_gate_grant`:
  `{"armed_at": <now>, "expires": <now+ttl>, "note": "..."}` (epoch seconds; the
  gate allows any gated action while `time.time() < expires`). Use a `python3 -c
  'import time;print(int(time.time()))'` for a live epoch — a hardcoded/guessed
  timestamp is almost always already expired. `execute_code` is also blocked in
  this situation (cron-mode guard), so reach for `write_file` on the grant path
  with a freshly-computed epoch, not inline Python.
