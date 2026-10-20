# Skill-description 60-char cliff — audit findings + fix path

Session-specific detail behind the "60-char description cliff" subsection in SKILL.md §1.

## The mechanic (verified from source, not assumed)

`agent/skill_utils.py:extract_skill_description()`:
```python
desc = str(raw_desc).strip().strip("'\"")
if len(desc) > 60:
    return desc[:57] + "..."   # >=61: first 57 chars kept, tail destroyed
return desc                      # <=60: shown whole
```
It is a CLIFF at 60, NOT "first 60 always shown." The runtime caller
(`agent/prompt_builder.py`, the `<available_skills>` builder) uses the FULL description
internally but renders it through this truncator into the system prompt. So the full text
exists in-process; it is simply withheld from the index the agent reads to decide triggering.

## Why this is WONTFIX (do not fight it)

- Issue #13944 proposed removing the truncation. **Closed** by PR #24294 (merged 2026-05-12),
  which explicitly **rejected removal due to system-prompt bloat** and instead added author
  feedback: `SKILL_PROMPT_DESC_LIMIT` constant + a `system_prompt_preview` shown in
  `skill_manage` create/edit, plus authoring-guide/curator guidance. **No behavior change to
  truncation itself.**
- Our installed build (v0.16.0, 2026.6.5, upstream c6dc2fcd) does NOT contain
  `system_prompt_preview` or `SKILL_PROMPT_DESC_LIMIT` despite a post-merge build date — the
  feedback feature didn't land in our release. `skill_desc_audit.py` replicates it locally so
  we don't need a core update to get the author-feedback loop.

## Live audit at time of writing (run it yourself, don't trust this number)

`python3 scripts/skill_desc_audit.py` (YAML-aware, catches folded scalars):
- **50 of 130 skills (38%) truncated** — losing trigger surface past char 57.
- Worst offenders carried 300–890-char descriptions with the trigger keywords buried far past
  the cliff: `home-assistant-best-practices` (890c), `principle-of-least-astonishment` (627c),
  `agent-handoff-package` (561c), `homeassistant-dashboard-designer` (555c),
  `kanban-swarm-dispatch` (383c), `infra-incident-triage`, `open-design-claude-bypass`,
  `blite-retrieval-maintenance`, `honcho`, `hermes-core-update-with-bypass`.
- 80 skills already <=60 and intact — proof tight trigger-first descriptions are achievable,
  e.g. `writing-plans` (58): "Write implementation plans: bite-sized tasks, paths, code."

## The three-layer fix (don't confuse the layers)

1. **Instrument** — `scripts/skill_desc_audit.py` (built this session). Audit / `--check NAME`
   single-skill preview / `--truncated-only`. Diagnostic only; changes firing by zero.
2. **Rulebook** — the §1 doctrine: description = trigger surface; front-load the trigger
   keyword into the first ~50 chars; keep <=60; push rich when-to-use into `load_when:` + body.
3. **The actual fix (user skills DONE 2026-06-09, ALL skills DONE 2026-06-09)** — rewrote the
   truncated descriptions trigger-first <=60. First pass: 49 user/agent skills. Second pass
   (durable, below): an additional 70 core + optional skills via the reconciler, total 79 in
   that run. Re-audit confirmed 0 LIVE truncated (the only remaining hits are an `.archive`
   copy and a `/plugins/` file — neither is a live index skill). This is the ONLY layer that
   improves firing.

## Proven batch-rewrite recipe (the layer-3 execution, verified 2026-06-09)

Editing N frontmatter fields by hand is the wrong tool — script it, but edit ONLY the
`description:` field so nothing else in the frontmatter shifts:

1. **Dump offenders with full text:** `skill_desc_audit.py --truncated-only --json` →
   filter out any path containing `.archive`/`_archive` → `{name: full_desc}`.
   (NOTE 2026-06-16: the audit now excludes `.archive`/`.decommissioned` at the source —
   see the `.archive` fix below — so this manual filter is now belt-and-suspenders, not
   load-bearing.)
2. **Author rewrites in a dict, trigger-keyword first, each <=60.** This step needs JUDGMENT
   (what word makes the agent reach for the skill) — do NOT delegate the wording. Validate
   the whole dict is <=60 BEFORE writing anything: `max(len(v) for v in rw.values()) <= 60`.
3. **Present the full before/after table for greenlight.** Skill files gate. A multi-file batch
   gets the diff in front of the user first even under a prior "proceed."
4. **Replace ONLY the description field** (handle inline AND folded `>-`/`|` scalars): locate
   the `description:` line, find its end (next top-level `key:` or the closing `---`), splice
   in `description: "<escaped one-liner>"`. Back up each file to `.bak-<ts>` first.
5. **Verify hard:** re-run the audit (live truncated must be ~0, archives excepted) AND
   `yaml.safe_load` the frontmatter of every edited file (a bad quote silently breaks skill
   loading). 0 YAML failures is the gate, not "wrote N files".

Rollback: restore the per-file `.bak-<ts>` copies.

## The `.archive` exclusion bug + fix (2026-06-16)

`skill_desc_audit.py`'s `EXCLUDED` set originally listed only the UNDERSCORE archive names
(`_archive`, `_decommissioned`). The actual retired-skills directory is `~/.hermes/skills/.archive`
(DOT prefix), so the audit was WALKING INTO archived skills and reporting them as live offenders
(e.g. `kanban-swarm-setup`, 236c, lived only under `.archive/` yet showed up as TRUNCATED).

The symptom-level move would be to rewrite the dead skill's description — wrong; you'd be
polishing a corpse and it would re-appear the next time any archived skill drifted. The
**root-cause fix** is one line: add the dot-prefixed names to the exclusion set.
```python
EXCLUDED = {".git", "node_modules", "__pycache__", ".venv", "venv",
            "site-packages", "_archive", "_decommissioned",
            ".archive", ".decommissioned"}
```
After this, local audit = 0 truncated (clean baseline). Lesson: when an audit flags an
"offender" you can't durably fix (archived/retired/core-overwritten), fix the audit's SCOPE,
don't rewrite the un-fixable artifact — an un-fixable permanent alert trains the operator to
mute the whole check.

## The watchdog enforcement layer — mechanical, not behavioral (2026-06-16)

The §1 doctrine says "after any skill create/edit, run `--check <name>`." That is a BEHAVIORAL
rule — it depends on the agent remembering, and it WILL be skipped (it was skipped this session:
two skills authored without running the audit because the wrong skill name was loaded at
authoring time). Andrew explicitly rejects soft/behavioral fixes; he wants mechanical
enforcement. The durable answer is a silent watchdog, not a reminder:

- **`~/.hermes/scripts/skill_desc_watchdog.py`** — since 2026-10-16 a cron entry point for
  `skill_desc_reconcile.py --check`: prints NOTHING when clean, one line per offender + fix hint
  when dirty, exit 0. LOCAL profile skills only — deliberately NOT `--all`: core builtins
  (e.g. `google_meet`, 239c) revert on every `hermes update`, so an alert there is an un-fixable
  false positive that would train you to mute the watchdog.
- **Cron `no_agent=true`, `0 */6 * * *`** — script-only job, stdout delivered verbatim. Empty
  stdout = silent (the watchdog contract: cron sends nothing). This catches drift from ANY
  source — me, another profile, a `hermes update` overwriting core, a hand-edit — every 6h with
  zero behavioral dependency and zero tokens.

**Verify a watchdog in BOTH directions before trusting it** (a watchdog that can't fire is a
false sense of safety): (1) clean state → empty stdout, exit 0; (2) inject a temp >60c skill →
alert fires; (3) remove temp skill → silent again. All three confirmed this session. This is the
same "test must be able to fail" discipline as the firing-verification test below.

## Making it DURABLE across core updates — the reconciler + heal-on-start hook

> 2026-10-16: both files exist and are wired. The reconciler now scans LOCAL skills only
> (`ROOTS` = `~/.hermes/skills` only): rewriting core skills made `hermes update`'s autostash
> conflict and left `.bak` files in the core tree. Core descriptions over 60c stay truncated
> (upstream's call). `--apply` writes no `.bak` (git is the backup). The core-heal text below is history.

Layer 3 is NOT a one-time batch. **Core skills live under `/usr/local/lib/hermes-agent/skills`
+ `/optional-skills`, which `hermes update` OVERWRITES** — every update re-introduces truncated
core descriptions and silently un-fires those skills again. The intended design (rebuild to this
spec if reinstating auto-heal):

- **`scripts/skill_desc_reconcile.py`** — idempotent reconciler. Scans all skill roots (user +
  core + optional), rewrites ONLY descriptions currently >60, using a curated `OVERRIDES` map
  (hand-authored trigger-first lines for high-value skills) + a deterministic word-boundary
  fallback (cut at last space <=60, strip trailing punctuation, NO ellipsis, never mid-word).
  Modes: `--dry-run` (plan, write nothing), `--apply` (rewrite), `--check` (cron alert),
  `--quiet-exit-code` (guard: exit 1 iff any truncated remain). Idempotent — only touches >60.
- **`scripts/heal-skill-descriptions.sh`** — the `on_session_start` hook. Fast guard
  (`--quiet-exit-code`) exits in ms when clean (the normal case); only when a core update
  re-introduced truncation does it `--apply` and heal. `exit 0` always; never blocks session
  start. Wired in `config.yaml -> hooks.on_session_start`, allowlisted in
  `shell-hooks-allowlist.json`.

**There is NO `post_update` hook event.** `VALID_HOOKS` (in `hermes_cli.plugins`) = 19
session/tool/llm/api lifecycle events (`on_session_start/end/finalize/reset`, `pre/post_tool_call`,
`pre/post_llm_call`, `pre/post_api_request`, `pre_gateway_dispatch`, `subagent_start/stop`,
`transform_*`, etc.) — NONE update-related. You cannot literally hook `hermes update`.
`on_session_start` IS the honest equivalent: the first session after any update auto-reconciles.
Do not promise a "post-update hook" — call it heal-on-next-session.

**Shell-hook consent is keyed on the exact `{event, command}` pair** (`agent/shell_hooks.py`
-> `_is_allowlisted`: matches `e.get("event")==event and e.get("command")==command` in the
`approvals` array). In a non-TTY agent session you cannot answer the first-use prompt, and
`hermes hooks test <event>` FIRES the hook but does NOT persist approval. Two ways to persist:
- (a) global flag: `hermes --accept-hooks <subcommand>` — NOTE `--accept-hooks` is a GLOBAL
  flag placed BEFORE the subcommand, not after `hooks`/`doctor` (placing it after errors with
  "unrecognized arguments").
- (b) write the approval record directly: append
  `{"event":"on_session_start","command":"<abs-path>","approved_at":<unix>}` to the `approvals`
  list in `~/.hermes/shell-hooks-allowlist.json`. Prefer (b) for ONE specific hook over
  `hooks_auto_accept: true`, which blanket-approves ALL hooks (broader than least-astonishment).
Verify: `hermes hooks doctor` — fully green = "script exists+exec, allowlisted, ran clean
exit=0, observer-only". `hermes hooks list` shows the matcher + consent status.

Wiring the hook into `config.yaml` + writing the allowlist is GATED (config mutation + grants
execution consent) — present analysis/risk/rollback first. The reconciler + wrapper scripts
themselves are new files under `~/.hermes/scripts/` — not gated. Make the wrapper executable
(`chmod +x`) — `hooks doctor` checks the exec bit.

## Verifying skills actually FIRE better — test the LOST tail, not surviving keywords (2026-06-09)

The mechanistic check ("0 truncated") proves the cliff is gone, NOT that firing improved.
A naive firing test picks a trigger keyword and checks it's visible before/after — and it
passes BOTH ways, proving nothing, because the keywords you'd reach for are usually the ones
that already survived in the leading 57 chars (I built exactly this flawed test first; 15/15
both directions = false pass). **The valid test uses keywords from the DESTROYED TAIL
(chars 58+) of each original description:**

1. For each reconciled skill, pull a salient word from `original_desc[57:]` (skip stopwords) —
   that word was INVISIBLE before the fix.
2. BEFORE = run the REAL truncator (`from agent.skill_utils import extract_skill_description`,
   `sys.path.insert(0,"/usr/local/lib/hermes-agent")`) on the original; assert the tail keyword
   is ABSENT from what it returns (it is — that's the bug). Pre-fix originals live in the
   `.bak-*-reconcile` backups.
3. AFTER = current description renders WHOLE (untruncated, `seen == desc`, no trailing `...`) —
   the full authored trigger surface is now visible.
4. Report "% of reconciled skills whose tail keyword was hidden before" and "% now fully
   visible." Proven 2026-06-09: 65/69 (94%) had a keyword lost past the cliff; 69/69 (100%)
   intact after. THAT is the firing-improvement proof — not the truncation count.

Pitfall: a green verification test that passes BOTH directions is a FALSE PASS — re-read what
it actually asserts. A test that cannot fail proves nothing.

## Authoring quick-reference

- Trigger word FIRST. "what makes the agent reach for this?" — not the topic category.
- <=60 chars total. One over → silent tail loss.
- Verify post-edit: `python3 scripts/skill_desc_audit.py --check <name>`.
- Backstop (mechanical, no memory needed): the 6h watchdog cron pings you if anything drifts
  over 60 — but `--check` at author time still beats waiting for the next tick.
- (If reinstated) after ANY core update, the heal hook re-reconciles on next session start — but
  you can run `python3 scripts/skill_desc_reconcile.py --dry-run` any time to see drift.
