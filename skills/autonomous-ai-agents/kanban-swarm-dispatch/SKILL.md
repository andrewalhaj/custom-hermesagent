---
name: kanban-swarm-dispatch
description: "Use when you plan to delegate 2+ independent tasks"
sector_path: [development, agent-fleet, orchestration]
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [kanban, swarm, delegation, orchestration, parallel, verifier]
    related_skills: [third-party-tool-evaluation, hermes-maintenance, verification-before-completion]
    created_by: agent
load_when:
  - "task has multiple parts and you're deciding how to execute it"
  - "user asks to audit/research/analyze several independent things at once"
  - "considering kanban swarm, parallel workers, or fan-out work"
  - "about to run `hermes kanban swarm`"
  - "user asks to 'work with' or delegate to a peer agent/profile (ha-bot, executor, etc.)"
---

# Kanban Swarm Dispatch

When and how to route work to the Kanban swarm. The swarm's ONLY real advantage is
**parallel decomposition of independent work**. Route by SHAPE, never by difficulty.

## ⚠️ The routing decision happens BEFORE the plan, not after greenlight (burned 2026-06-20)

The classic failure: user hands a multi-part IMPLEMENTATION objective ("port 11 upstream
features into the live app"), you do the inventory correctly with `delegate_task` (right tool
for parallel READ work), then — without re-deciding the route — keep the same `delegate_task`
workers going to WRITE the patches. That is the bug. Investigation and implementation are
DIFFERENT shapes and get DIFFERENT routes, even inside one objective.

- **Read-only inventory / research / comparison → `delegate_task`.** Ephemeral, single-turn,
  fine to die with the session. This is correct.
- **Writing patches to production files / multi-chunk build / anything needing an audit trail
  and a verifier gate → kanban board.** Persists across sessions, survives restart, has the
  Opus verifier. `delegate_task` workers for this produce unverifiable output you then have to
  hand-review, and half of it comes back broken (proven: 3 workers, 2 patch sets failed
  validation — the "fast path" cost more time than the board would have).

**Make the route decision BEFORE presenting the plan to the user.** Reversed order — plan →
user says "yes" → you start executing inline — is the trap: once greenlit you're in execution
momentum and `delegate_task` feels immediate while the board feels like overhead. The board's
spec-writing + dispatch-tick latency is NOT overhead; it's the cost of verifiable, durable work.

**Rationalizations that are BANNED for implementation work** (each one fired this session and
was wrong):
- *"This is single-session."* — Investigation is single-session; implementation is not. The
  split is by CATEGORY (read-only vs writes-to-production), not by your estimate of how long
  it'll take. "I'll finish it this conversation" is always available and always wrong here.
- *"The workers are producing artifacts for me to review, not implementing."* — Writing patches
  IS implementing. "Output for orchestrator review" is not a get-out-of-board card.
- *"The user already said yes."* — Greenlight on the PLAN is not greenlight on the ROUTE. The
  route should have been decided (and stated) before the plan.

## ⚠️ Checkpoints are PROSE nudges you can rationalize past — treat a fired checkpoint as a STOP, not a footnote (2026-06-20)

Every routing NUDGE on this host (`plugins/local-guards/guards/routing.py`, which replaced the
kanban/delegation checkpoint patches, and `skill_review_checkpoint.py`) injects TEXT into a tool result. None
of them BLOCKS execution. The proven failure mode: the checkpoint fires, you write a one-line
"proceeding inline because X" acknowledgement, and keep going — the system accepts the
acknowledgement without enforcing anything. This session the kanban checkpoint fired exactly at
the `delegate_task` dispatch and was read-and-ignored; the delegation checkpoint fired 4× and
was each time answered with a justification + proceed.

The user's standing position (USER.md): *"Values mechanical enforcement over prose nudges — a
reminder I can rationalize past isn't a fix."* So:
- When a kanban/routing checkpoint fires on IMPLEMENTATION work, the default is **STOP and route
  to the board**, not "acknowledge and continue." A one-line inline justification is only valid
  for genuinely sequential dependency chains (debug→fix→verify on one file), and you must name
  the dependency, not just assert "sequential."
- A justification you could write for almost any task ("each step depends on the last",
  "single-session", "I'll review the output") is not a justification — it's a rationalization.
  If the reason isn't SPECIFIC to why THIS work can't be parallelized/boarded, the checkpoint wins.
- `skill_review_checkpoint` listing irrelevant skills (e.g. github-auth/ocr on a WebUI task)
  does NOT license ignoring it — load the skill that actually fits the work (here:
  kanban-swarm-dispatch) instead of dismissing the whole nudge.

## The shape-gate — run this BEFORE executing any multi-part task

Ask two questions:

1. **Independent?** Does it split into 2+ chunks where no chunk needs another chunk's
   output? (A dependency chain — probe→diagnose→fix→verify — is NOT independent.)
2. **Worth the board overhead?** Is this cross-session, overnight, or multi-hour work
   that should outlive a conversation? (Single-session tasks → delegate_task instead.)

**Routing decision:**
- **2+ independent, single-session → `delegate_task` immediately.** No proposal, no greenlight. Fan out in parallel; synthesize results yourself. WRITE GATE governs what subagents execute. This is the DEFAULT.
- **Cross-session / overnight / audit trail needed / peer profile → kanban board.** Only shape that warrants board overhead.
- **Sequential dependency chain → do it DIRECTLY.**

**"plan to" trigger:** "plan to X and Y" or "plan to review/check/compare" = explicit parallelism signal → `delegate_task` immediately, no asking.

Why not route by "complexity": complex work is usually a sequential reasoning chain. Swarm workers run on the configured delegation model; route hard sequential work directly and reserve swarm/delegation for genuinely parallel chunks.

## The pod (verified topology)

- `swarm-worker-a/b/c` — parallel workers, **qwen2.5-32b on Mac Studio** (local, free, P=4 slots), own state.db each
- `swarm-verifier` — skeptical gate, **claude-opus-4-8** (strongest model gates, doesn't do work)
- `swarm-synthesizer` — composer, **deepseek-v4-pro**
- `dispatch_in_gateway: true` → BOUNDED-AUTONOMOUS: the gateway tick auto-claims+spawns ready
  workers within the concurrency cap. Safety = verifier gate + cap + timeouts, NOT per-action
  approval. You propose + greenlight the dispatch; you do NOT approve each worker step.

## Dispatch — the verified command

```bash
hermes kanban swarm \
  --worker "swarm-worker-a:<short title for chunk A>" \
  --worker "swarm-worker-b:<short title for chunk B>" \
  --worker "swarm-worker-c:<short title for chunk C>" \
  --verifier swarm-verifier \
  --synthesizer swarm-synthesizer \
  --created-by default \
  --priority 5 \
  "<the overall goal — what the synthesizer should ultimately produce>" \
  --json
```

Returns `{root_id, worker_ids[], verifier_id, synthesizer_id}`. The root card auto-completes
immediately as the shared blackboard anchor — that is correct, not a bug. Workers then move
`ready → running → done` on the gateway tick (no manual push needed). If a tick seems slow,
`hermes kanban dispatch --dry-run` shows what it would spawn; `hermes kanban dispatch` forces a pass.

## Cross-profile dispatch — delegate to a PEER agent, not just the swarm (verified 2026-06-08)

The board (`~/.hermes/kanban.db`) is a **single SQLite board shared across ALL profiles**.
`tasks.assignee` is just a profile name, and the gateway dispatcher (`dispatch_in_gateway: true`,
every `dispatch_interval_seconds`) **claims a ready card and spawns WHATEVER profile is in
`assignee`** — it is NOT hardcoded to `swarm-*`. So you can assign a card to a domain-owning
peer profile (e.g. `ha-bot`/HAJarvis, `executor`, `voice-changer`) and the gateway spawns THAT
profile's own headless agent run — same brain, same skills, same host access, same domain
authority as its live bot. This is the mechanism for "work WITH agent X" when a domain has been
delegated to X: you become coordinator + verifier, X executes on its own turf. You do NOT reach
into X's profile files yourself.

**This is NOT the swarm.** The swarm = `swarm-*` workers + Opus verifier + synthesizer for
parallel decomposable read/analysis. Cross-profile dispatch = ONE card to ONE peer for
single-domain work that belongs to that peer. Different shape, different command.

**Single-card create (verified flags):**
```bash
hermes kanban create "<title>" \
  --assignee ha-bot \           # any profile from `hermes kanban assignees`
  --created-by default \
  --priority 5 \
  --goal --goal-max-turns 30 \  # goal-loop for open-ended cards one shot won't finish
  --max-runtime 30m \           # bounded; dispatcher SIGTERMs+requeues on overrun
  --skill home-assistant \      # force-load a skill into the worker (repeatable)
  --body "<full self-contained spec: context + tasks + RULES + report-back instruction>" \
  --json
```
Returns the card `{id, status:"ready", assignee}`. The card moves `ready → running` on the
gateway tick (usually within `dispatch_interval_seconds`; `hermes kanban dispatch` forces a pass
but the gateway may beat you to it — `Spawned: 0` from a manual pass often just means the gateway
already claimed it).

**VERIFY it's a real run of the right profile** (don't trust status alone):
```bash
ps -eo pid,args | grep "p ha-bot" | grep "task t_" | grep -v grep   # the worker process
python3 -c "import sqlite3;c=sqlite3.connect('kanban.db');c.row_factory=sqlite3.Row;[print(dict(r)) for r in c.execute(\"SELECT profile,status,worker_pid,last_heartbeat_at FROM task_runs WHERE task_id='<id>' ORDER BY started_at DESC LIMIT 1\")]"
hermes kanban log <id> | tail   # live event stream — confirm it read its card + is working
```
`task_runs.profile` must equal the assignee and the heartbeat must be fresh. THEN, when it
reports back, verify its result against live ground truth (the host/files it touched) before
relaying — a peer's self-report is no more trusted than a swarm worker's.

**Confirm CLI flags before dispatching — don't guess.** `hermes kanban create --help` for flags,
`hermes kanban assignees` to confirm the target profile is on-disk + recognized.

**⚠️ A `--skill` name unknown to the ASSIGNEE'S profile is FATAL, not silently dropped (corrected
2026-06-09 — supersedes the earlier "silently no-ops" claim).** The worker validates skills at
spawn and aborts hard: `Error: Unknown skill(s): <name>` → the run crashes instantly, the
dispatcher retries once, crashes again, and **gives up → card stuck `blocked`** (consecutive_failures
hits the limit). This session, dispatching the Projects-tab card with `--skill wall-dash` crash-looped
twice in ~1 min before I caught it in `kanban/logs/<id>.log`. **Skill names differ across profiles** —
the dashboard skill is `wall-dash` in the *default* profile but `wall-dashboard` in *ha-bot*'s. So:
1. **Before dispatching, list the ASSIGNEE'S actual skill dir names**, not default's:
   `find ~/.hermes/profiles/<assignee>/skills -name SKILL.md | sed 's#.*/skills/##;s#/SKILL.md##' | awk -F/ '{print $NF}' | sort` — and force-load only names that appear there. Also fix any skill-name references inside the `--body` spec to match the peer's names.
2. **Don't trust the returned `skills:[...]`** as proof it'll run — it echoes what you passed; the crash happens later at spawn. Tail `kanban/logs/<id>.log` after the first tick to confirm it loaded, not crashed.
3. **Recovery from a crash-blocked card:** `kanban edit` has NO `--skill` flag, so you can't repair the skill list in place. `hermes kanban archive <id>` the dead card and **recreate with corrected skill names + a NEW idempotency key** (the old key would otherwise return the dead card's id).

## Cross-profile dispatch — ANY profile can be a kanban assignee (proven 2026-06-08)

The swarm pod is not the only thing the board can drive. The board is a SINGLE shared SQLite store (`~/.hermes/kanban.db`; satellite profiles have NO separate board), `tasks.assignee` is just a profile name, and the gateway dispatch tick spawns WHATEVER profile is in `assignee` — it is NOT hardcoded to `swarm-*`. So you can hand a bounded task to a peer agent (e.g. HAJarvis/`ha-bot`) and it runs in ITS OWN profile: own skills, own host access, own domain authority. This is the right mechanism for "work WITH a delegated-domain agent" — the work is performed by that agent's brain, you stay coordinator + verifier.

**Verified create-single-card path** (distinct from `hermes kanban swarm`):
```bash
hermes kanban create "<title>" \
  --assignee <profile> --created-by default --priority 5 \
  --goal --goal-max-turns 30 --max-runtime 30m \
  --skill <domain-skill> \
  --body "<self-contained spec: context + tasks + RULES + report-back instructions>" --json
```
- `--goal` + `--goal-max-turns N`: for open-ended work one shot won't finish (audit + edits + live inspection). The card loops until a judge agrees it's done or the turn budget runs out.
- `--max-runtime 30m`: bounded; the dispatcher SIGTERMs a stuck run and requeues.
- `--assignee <profile>` must be a REAL on-disk profile — confirm with `hermes kanban assignees` (lists every profile + idle/busy). The dispatcher's 60s tick claims the ready card; force a pass with `hermes kanban dispatch` if impatient (it may show `Spawned: 0` if the gateway tick already grabbed it — check the card status, not the dispatch output).

**Verify it's a REAL cross-profile run, not a misfire:** `ps -eo pid,args | grep -- '--profile <name>'` should show `hermes -p <profile> … chat -q work kanban task <id>`, and `task_runs.profile` (in kanban.db) should equal `<profile>` with a fresh heartbeat. Then the card moves `ready→running→done`.

**The headless run is async, NOT a live chat between bots.** Same profile/brain, different entry point — a task card, not real-time agent-to-agent dialogue. Kanban gives "assign → it executes → you verify," not interactive back-and-forth. If the user pictures two bots conversing live, kanban isn't that.

**First cross-profile dispatch to a given profile is a TEST, not a guarantee** — the swarm path is proven for `swarm-*`; a new assignee profile *should* spawn identically but watch the first one (claim → run-under-profile → report) before trusting it. Worst case is an errored/stalled card (no infra change, fully observable); rollback = `hermes kanban archive <id>`.

**Still verify the result against ground truth before relaying** — the assignee's report is a SELF-REPORT (this skill's existing rule). This session: ha-bot reported "archived 2 docs, corrected 6, wrote a new doc"; verification against the live host + filesystem (the `.bak`s exist, the archive dir landed, the new doc matched what's actually on VPS-A) is what made it trustworthy, not the report.

## CRITICAL — the introspection doctrine (worker-context gotcha)

The filesystem is **SHARED** (all profiles under `~/.hermes/`; absolute-path reads work
cross-profile). BUT each worker profile has its **OWN empty `cron/jobs.json`, own `state.db`,
and no `references/` dir**. So a worker that introspects via **profile-scoped `hermes`
subcommands** (`hermes cron list`, `hermes profile ...`) or **relative paths** reads its OWN
empty profile and falsely reports default-profile artifacts as "missing" (verified 2026-06-08:
2/3 workers hallucinated "0 cron jobs / file missing").

When a swarm task must inspect the **default profile's** own state, the task prompt MUST:
1. Give **absolute paths** to every artifact (`~/.hermes/cron/jobs.json`,
   `~/.hermes/references/<file>`, `~/.hermes/config.yaml`, `~/.hermes/memories/MEMORY.md`).
2. Explicitly forbid profile-scoped introspection: *"read files/DBs directly (cat/read/python
   sqlite3 on the absolute path); do NOT use `hermes cron`, `hermes profile`, or any
   profile-scoped `hermes` subcommand — they resolve to YOUR empty worker profile, not default's."*
3. For DB reads: use the Python `sqlite3` module on the absolute db path (the `sqlite3` CLI
   binary is not installed on this host).

**Do NOT "fix" this by aligning worker cwd/profile to default** — that points workers at
default's state.db/kanban store and breaks the per-profile isolation that prevents SQLite
contention between parallel workers. The doctrine (absolute paths in the prompt) is the fix.
Full write-up: `~/.hermes/references/swarm-introspection-doctrine.md`.

## Cross-profile dispatch — delegate a whole task to a PEER domain agent (proven 2026-06-08)

The swarm (`swarm-*` workers) is for parallel decomposition of YOUR work. A different, equally-real pattern: **hand an entire bounded task to a peer agent that owns a domain** — e.g. give the HA/dashboard work to HAJarvis (`ha-bot` profile) instead of reaching into his territory yourself. This is the right move when a task falls under another agent's delegated domain.

**Why it works (verified architecture):**
- The board is a **single shared SQLite** at `~/.hermes/kanban.db`. Peer profiles have NO separate board (no `profiles/<name>/kanban.db`) — they read/write the same one.
- `tasks.assignee` is just a **profile name**, and the gateway dispatch loop (`dispatch_in_gateway: true`, every `dispatch_interval_seconds`) **spawns whatever profile is in `assignee`** — it is NOT hardcoded to `swarm-*`. `hermes kanban swarm` merely sets `assignee=swarm-worker-*`; nothing stops `assignee=ha-bot`.
- Result: the gateway spawns a real headless run of that profile (own skills, own host access, own domain authority), it does the work on its own turf, and results land back on the shared card (`result`, `task_comments`, `task_runs`).

**Dispatch a single card to a peer profile:**
```bash
hermes kanban create "<task title>" \
  --assignee ha-bot --created-by default --priority 5 \
  --goal --goal-max-turns 30 \        # goal-loop for open-ended work one shot won't finish
  --max-runtime 30m \                  # bounded; dispatcher SIGTERMs on overrun
  --skill home-assistant \             # force-load domain skills into the peer run
  --body "<full self-contained spec: context + tasks + RULES (back up before edit, scope limits, report-back format)>" \
  --json
```
Then `hermes kanban dispatch` (or just wait one tick). Confirm `hermes kanban assignees` lists the target profile as `on disk` first.

**This is NOT a live chat between the two bots.** It's an async headless task run of the same profile/brain — assign → it executes → you verify. There is no real-time agent-to-agent dialogue in the current setup; kanban gives you "delegate work + collect verified result," not conversation.

**Verify it's actually the peer running (not a misfire):** check `ps -eo pid,args | grep "<profile>"` for the `hermes -p <profile> … chat … kanban task <id>` process, and `task_runs.profile == <profile>` in kanban.db. A real run shows the peer's own bypass/init in `hermes kanban log <id>`.

**Caveats:**
- `--skill X` is **FATAL if X doesn't exist in the PEER's profile** — it crash-loops the worker to a `blocked` card, it does NOT silently no-op (corrected 2026-06-09; see the bolded skill-name warning above). Skill dir names differ across profiles (`wall-dash` default vs `wall-dashboard` ha-bot). List the assignee's actual skill names before dispatching; recovery = archive + recreate with corrected names + new idempotency key.
- First cross-profile dispatch to a given profile is a TEST, not a guarantee — watch one card claim → run → report before trusting it. Worst case is an errored/stalled card (fully observable on the board, `hermes kanban archive <id>` to clear); no infra changes unless the peer itself gates and acts within its own scope.
- Still applies: **the peer's result summary is a SELF-REPORT.** Verify against ground truth (the peer's own files, its host) before relaying to the user — this session a ha-bot run reported "8 docs edited, archive created, new doc written"; all of it checked out against the live host, but only because it was verified, not trusted.

## Idempotency + repeatable dispatch (proven 2026-06-09)

When you may run the same dispatch logic more than once (a retry, a re-prompt, a cron that
re-fires), pass **`--idempotency-key <stable-string>`**. If a non-archived card with that key
already exists, `kanban create` returns ITS id instead of creating a duplicate — so re-running is
safe. Use a key that encodes the task + date, e.g. `ha-orphan-pointers-20260609`. This session
created two distinct peer cards (a memory-pointer task and a dashboard-tab task), each with its
own idempotency key, and both dispatched cleanly without dup risk.

Extract the new card id cleanly without eyeballing the JSON dump:
```bash
hermes kanban create "<title>" --assignee ha-bot --created-by default \
  --goal --goal-max-turns 25 --max-runtime 25m --priority 5 \
  --idempotency-key <task-date> --skill <a> --skill <b> \
  --body "$(cat /tmp/task-spec.md)" --json \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('TASK:',d['id'],'status:',d['status'])"
```
Write the `--body` spec to a `/tmp` file first (heredoc/write), then `--body "$(cat …)"` — large
multi-paragraph specs are unwieldy inline and easy to mangle with shell quoting.

## The "you only talk to me" orchestration model (the answer to "do I dispatch or chat with the bot?")

For getting work DONE, the default front door is the orchestrator (you): the user talks to you,
you fan work out — `delegate_task` subagents, the swarm, and cross-profile cards to peer agents —
and you verify against live state before reporting back. The user does NOT need to open a peer
bot's chat to get that peer's domain work done; route it through a kanban card.

The ONE caveat to state honestly: peer agents (HAJarvis `@HAjarviss_bot`, etc.) are **separate
Telegram bots with their own chat windows.** You can dispatch a task TO a peer via the board and
verify its output, but you **cannot send a message into its chat thread** (`send_message` has no
path to another bot's conversation). So:
- The user never needs to talk to a peer to get delegated work done — you orchestrate it.
- The user might still open a peer's chat directly ONLY if they want a live real-time
  back-and-forth WITH that specialist (e.g. live-debugging a dashboard in its window).
Frame it that way when asked: "one conversation, everything downstream answers to me; the only
reason to open another bot is a direct live chat with that specialist."

## Monitor + verify

1. Poll: `hermes kanban show <root_id>` and each `<worker_id>` — watch `status` and the run
   summary (`→` line). Workers are deepseek-flash, usually <2 min each.
2. The verifier (`<verifier_id>`) reads every handoff and either completes with metadata
   `{"gate": "pass"}` or **`kanban_block`s** with specific rework items. If it blocks, the
   synthesizer stays `todo` — nothing ships. That is the gate working.
3. **VERIFY THE RESULT AGAINST GROUND TRUTH before relaying it.** Worker output (and even the
   synthesizer's merge) is a SELF-REPORT, not verified fact. Spot-check headline claims against
   the live filesystem/DB yourself. (The Opus verifier is good — it independently re-checks —
   but you are the final gate to the user.)
4. Clean up when done: `hermes kanban archive <each task id>` (root, workers, verifier,
   synthesizer). Confirm no stray worker processes: `ps -eo pid,args | grep swarm- | grep -v grep`.

## Inert tracking cards — track a project on the board WITHOUT triggering dispatch (proven 2026-06-09)

Not every card is work for a worker to execute. To make a human-driven, interactively-built project VISIBLE on the wall-dash Projects tab (which renders the shared `kanban.db` via `kanban_export.py` every 5 min), you create **tracking cards** — one per project phase — that must NOT be auto-claimed and run by the gateway dispatcher.

The gateway (`dispatch_in_gateway: true`, 60s tick, `auto_decompose: true`) auto-claims any **`ready`** card and spawns its assignee as a headless run. To keep a card inert:
- **`--initial-status blocked` AND leave `--assignee` empty.** Unassigned alone is weaker (a future `default_assignee` config would make ready+unassigned dispatchable); blocked+unassigned is defense-in-depth. Dispatcher shows blocked/unassigned cards as `Skipped (unassigned)` and never spawns them.
- ⚠️ **The live gateway tick PROMOTES blocked→ready out from under you.** Cards created `blocked` were observed flipped to `ready` by the next tick (the create-time status isn't sticky against the running gateway). After creating, **re-block explicitly** with `hermes kanban block <id> "<reason>"` and re-verify status.
- ⚠️ **`hermes kanban complete <id>` ALSO fires a tick that re-promotes sibling `blocked`→`ready` (proven 2026-06-09).** It's not only the periodic 60s tick — any board-mutating command (notably `complete`) appears to kick a dispatch pass that flips your other inert cards back to `ready`. So the sequence "create blocked → block → complete one card → done" will leave the *remaining* siblings `ready` again. **Re-block AFTER every `complete`/board mutation and re-verify**, until all non-done cards read `blocked`. Don't trust a single re-block to stick.
- ⚠️ **CLI "cannot …" output is misleading — ground truth is the DB, not stderr (proven 2026-06-10).** Two cases hit in one session: (a) `hermes kanban complete <id> "<summary>"` — `complete` takes MULTIPLE positional task ids, so a summary string passed as a 2nd positional is parsed as another task id → prints `cannot complete <summary> (unknown id or terminal state)` while the real id IS completed. Never pass a completion message positionally. (b) `hermes kanban block <id> "<reason>"` on an already-blocked card prints `cannot block <id>` — that means already-blocked, not failure. After any sequence of block/complete calls, verify true state with a direct read: `python3 -c "import sqlite3;...kanban.db... SELECT id,status FROM tasks WHERE title LIKE '<Project>%'"`.
- **Re-promotion is possible-not-certain (datapoint 2026-06-10):** 7 blocked+unassigned siblings survived a `complete` on their sibling WITHOUT flipping to `ready` (verified via DB). Keep the verify-after-every-mutation rule, but don't assume promotion happened from CLI noise alone — read the DB before re-blocking.
- **Dashboard Projects-tab boards route by TITLE PREFIX** — defined in the `BOARDS` list in `~/.hermes/scripts/kanban_export.py` (case-insensitive, first match wins, `None` = catch-all). To put cards on a project board, title them `<Prefix> — Phase N: …`; to add a new project board, add one dict line to `BOARDS` (export refreshes every 5 min).
- **`cannot block <id>` output usually means ALREADY BLOCKED, not failure (observed 2026-06-10).** Re-blocking siblings after a `complete` returned `cannot block tX` for every card — yet the DB showed all of them still `blocked` (the complete-tick promotion didn't fire that time). Same with `cannot complete <id>` printed before `Completed <id>` when extra positional text is passed. Treat these CLI messages as noise; the ONLY trustworthy check is the sqlite read (`SELECT id,status FROM tasks WHERE title LIKE '<prefix>%'`). Verify state, don't parse CLI prose.
- **`hermes kanban block` on an already-blocked card prints `cannot block <id>` — that's benign, not a failure (proven 2026-06-10).** During the Mealio build, every re-block pass after `complete` returned `cannot block` for all siblings, yet the DB showed them still `blocked` — the gateway hadn't promoted them, so the block was a no-op refusal. Verify card state via the DB (`SELECT id,status FROM tasks`) not the CLI message; only act if a card actually reads `ready`.
- **`hermes kanban complete <id> "<summary>"` does NOT take a summary positional (proven 2026-06-10).** The second arg is parsed as another task_id — output shows `cannot complete <your summary text> (unknown id)` followed by `Completed <id>`. The completion succeeds; the summary is silently dropped. To record a result on the card, put it in the card body at create time or use a comment mechanism — don't rely on a positional summary arg.
- ⚠️ **`block` bulk-flag signature gotcha (corrected 2026-06-09).** `hermes kanban block --ids id1 id2 …` STILL errors (`the following arguments are required: task_id, reason`) — argparse demands the positional `task_id` + `reason` even when `--ids` is passed, so `--ids` alone does not bulk-block. **The reliable path is a shell loop of single calls:** `for id in t_a t_b t_c; do hermes kanban block $id "reason"; done`. Don't reach for `--ids`; loop the positional form.
- The blocked cards still render in the dashboard's **Active** column (the viewer treats ready/running/blocked/todo/triage as active). Mark a phase `done` (`hermes kanban complete <id>`) only after verifying the phase's artifact exists on disk — same live-artifact gate as the completion-ping rule below.

When a project phase IS owned by a peer domain (e.g. the deploy/nginx/Tailscale-serve step belongs to HAJarvis/`ha-bot`), that one card becomes a cross-profile dispatch to the owner at the time you reach it — not built by you reaching onto its host. The other phases (general build) stay under `default`. Split ownership by domain, card by card.

**Board hygiene for tracked projects (Andrew's standing instruction, 2026-06-10: "keep the kanboard updated").** Once a project lives on the board, EVERY subsequently shipped change — post-completion features, hotfixes, retrofits — gets its own card, even when the work was done conversationally: `kanban create` with `--initial-status blocked` + `--idempotency-key <feature-date>`, then `kanban complete <id> "<verified summary>"` immediately after live verification. The board must never lag the deployed reality. After ANY board mutation, run `python3 ~/.hermes/scripts/kanban_export.py` to push the snapshot to wall-dash immediately (output lands at /tmp/kanban-state.json then scp's to VPS-A) instead of waiting for the 5-min cron.

**`hermes kanban block`/`complete` print benign "cannot block/complete <id>" errors when the card is ALREADY in that state** — observed 2026-06-10: a re-block loop printed `cannot block t_x` for every card, yet the DB showed all of them correctly `blocked` (they'd never been promoted). The CLI message is not proof of failure; ground-truth is the `tasks.status` column in kanban.db. Verify there before treating a re-block as failed.

**When a project PIVOTS and its tracking cards go stale** (architecture changed → cards describe the dead design), don't try to `edit` them — rewrite the board: archive the superseded cards, recreate against the new architecture, gate each `done` on a verified on-disk artifact, and refresh the dashboard export immediately. Full procedure (with the paired design-doc archive + dashboard-refresh steps): `references/board-rewrite-on-pivot.md`.

## CLI output quirks on inert-card workflows (observed 2026-06-10, Mealio board)

- **`hermes kanban complete <id> "summary text"` parses the summary as a SECOND task id.** Output: `cannot complete <your text> (unknown id or terminal state)` followed by `Completed <id>`. The completion itself works; the text is NOT attached as a result. Don't pass a summary positional — `complete` takes ids only. Put the verified-artifact summary in your reply/board comment instead.
- **`hermes kanban block <id> "reason"` on an ALREADY-blocked card prints `cannot block <id>`.** Benign no-op, not a failure. Never judge card state from block/complete stdout — read ground truth: `python3 -c "import sqlite3; ... SELECT id,status FROM tasks WHERE title LIKE '<prefix>%'"` on `~/.hermes/kanban.db`.
- **Project-phase tracking pattern that worked cleanly:** create all phase cards `--initial-status blocked` + unassigned + per-card `--idempotency-key <project-phase-date>`; after EACH `complete`, run the re-block loop then verify via the sqlite read. This session the blocked+unassigned siblings were never promoted across multiple complete ticks (the re-block calls all no-op'd) — but the DB verify after every mutation stays the rule, since promotion HAS been observed before (2026-06-09).
- **Title prefix = dashboard board routing.** `kanban_export.py` BOARDS maps case-insensitive title prefixes to Projects-tab boards (e.g. `"mealio"` → Mealio board). Cards must be titled `<Prefix> — <phase>` to land on the right board; add the prefix entry to the script BEFORE creating cards.

## `hermes kanban gc` does NOT delete cards — it prunes events/logs only (proven 2026-06-09)

`gc` flags are `--event-retention-days` / `--log-retention-days` (default 30); it deletes old `task_events` and worker **log files** for terminal tasks, NOT the task rows. To actually declutter stale cards from the board (and thus the dashboard), you must **delete the rows**: back up `kanban.db` first (`cp kanban.db kanban.db.bak-<ts>`), confirm no non-terminal cards would be lost (`select id from tasks where status not in ('archived','done')` → must be empty), then `delete from tasks where status in ('archived','done')` via the Python `sqlite3` module (the `sqlite3` CLI binary isn't installed). Reversible via the `.bak`.

## Pitfalls

- **`delegate_task` rejects any goal/context containing `{word}` or `<word>` as an "unexpanded template marker" — the whole batch fails, not just that task.** Write curl's `-w '%{http_code}'` as prose ("print the HTTP status code with -w"), replace `<free port>` / `<file>` placeholders with a concrete value or "same filename + .bak", and scan a 7-task batch for both bracket shapes before sending; each rejection re-sends the entire payload.
- **Ownership routes BEFORE danger — peer-host writes go to the owner even when the user approves the SSH (burned 2026-06-10).** The approval gate checks *danger*; it does NOT check *ownership* — an approved `ssh root@VPS-A "…edit…"` is still a routing failure if the target is a peer's domain (wall-dash → ha-bot). This session shipped a dashboard feature inline over SSH despite this skill, a prior dispatch precedent, and two fired checkpoints. Hard rule: **state-changing work on a peer-owned host/path routes to the owner as a cross-profile card. Inline execution requires (a) one stated line of justification in the reply AND (b) a tracking card on the board** — so the shared record never diverges. A mechanical guard (`domain_ownership_checkpoint.py`, map: `references/domain-ownership.json`) now nudges on the first owned write; dismissing it without justification+card is a protocol violation, not a judgment call.
- **Completion ping must gate on the LIVE artifact, not task status (proven 2026-06-09).** When the user says "ping me when it's done," don't fire on `status==done` or the worker's summary alone — both are self-reports. Read the peer's LIVE file/DB the task was supposed to change, confirm the actual change landed, THEN ping. This session: a ha-bot card pointered 7 HA facts into ha-bot's MEMORY.md; the ping only fired after directly grepping `profiles/ha-bot/memories/MEMORY.md` for the pointer line AND spot-checking 2 of the 7 facts retrieved from cold store at ≥0.80. If you build a one-shot completion watcher for this, latch it with a `/tmp` state file so it pings exactly once, exit 0 on transient DB-lock so it stays silent until the real terminal state, and DELETE the watcher once it fires (don't leave a dead cron behind — if the task finishes before you schedule it, just verify inline).
- **Routing by difficulty instead of shape.** Complex ≠ parallel. Most complex work is a
  sequential chain → handle directly. Only fan out genuinely-independent chunks.
- **Forgetting the introspection doctrine on self-audits.** Without absolute paths +
  the no-profile-scoped-`hermes` rule, workers hallucinate "missing." Bake it into the prompt.
- **Trusting worker/synthesizer output as fact.** Always verify against live state before
  relaying — the verifier reduces but does not eliminate the need for your own check.
- **Never assign swarm work to `stable-2026-06-02` or `pre-update-2026-06`** — those are
  rollback snapshots, not worker profiles.
- **Don't auto-classify-and-fan-out unattended.** Bounded-autonomy = you propose + show the
  decomposition + greenlight; the gateway auto-dispatches within caps. Autonomous classification
  of arbitrary incoming work into swarm jobs is a larger autonomy grant than the doctrine allows.
- **Don't reach into a peer profile's files when the work is delegated to that peer.** If a
  domain (HA/dashboard → ha-bot) is owned by another agent, route the work as a cross-profile
  kanban card to THAT profile and let its own brain execute — don't edit its `references/`,
  `memories/`, or skills yourself. Cross-profile writes to another profile need explicit user
  say-so regardless.
- **Cross-profile dispatch is single-domain, NOT swarm-shaped.** Delegating one agent's domain
  work is ONE card to ONE assignee — running the 5-profile swarm (workers+verifier+synthesizer)
  for it is wrong-shaped overhead. Reserve the swarm for genuinely parallel decomposable chunks.
- **Headless dispatched runs CANNOT answer interactive approval prompts (verified 2026-06-08).**
  A dispatched worker/peer runs headless, so any command that trips an interactive approval gate
  (e.g. a raw-public-IP `curl`, certain destructive shell ops) can't be cleared — the run prints
  "Headless — can't clear the interactive approval" and must drop or work around that command. A
  well-behaved agent self-corrects (ha-bot dropped a raw-IP curl and used `docker inspect
  PortBindings` instead), but don't rely on it: in the task `--body`, prefer non-gated equivalents
  (inspect/read commands over probes that hit approval triggers) and tell the worker to route
  around an approval block rather than retry it. A run that loops on an un-clearable prompt burns
  its turn/runtime budget for nothing.
- **Weak local model = protocol violation, NOT a logic error (2026-06-21).** When a dispatched
  worker profile uses a local model (qwen2.5-32b, any model under ~32B params), the worker may
  exit clean (rc=0) WITHOUT calling `kanban_complete` or `kanban_block` — the dispatcher records a
  protocol violation, increments the failure counter, and the task goes `blocked`. The symptom is
  `worker exited cleanly (rc=0) without calling kanban_complete or kanban_block` in the event log,
  NOT a task-logic error. **Fix:** the worker profile's `model.default` + `model.provider` must
  point to a cloud model (Anthropic/OpenRouter) capable of following structured protocol. Local
  models fire on reading/decomposing but fail on completion discipline. Do NOT retry the same
  assignment — change the profile model first, then unblock.
- **Ghost-profile assignee — card sits in `ready` forever (2026-06-21).** `claude-code-worker`
  and `admin-a` are examples of assignee names that don't correspond to any on-disk profile. The
  gateway dispatcher will never claim a card assigned to a non-existent profile — it silently
  skips. Before creating a card, confirm `hermes kanban assignees` lists the target profile. If a
  card was already created with a ghost assignee, fix it directly in the DB:
  `sqlite3 ~/.hermes/kanban.db "UPDATE tasks SET assignee='<real-profile>' WHERE id='<task-id>'"`.
  Ghost-profile cards are the #1 reason for "why isn't this being worked on."
- **Delegation model too weak → false completions (2026-06-21).** When `delegation.model` in
  `config.yaml` is set to a weak model (e.g. `claude-haiku-4-5-20251001`), `delegate_task`
  subagents will decompose work into subtasks and falsely complete instead of doing the actual
  coding. The subagent's self-report looks convincing ("schema created, data extracted, child task
  created") but the core deliverable was never produced. **Verify every delegated outcome against
  ground truth** — file contents, DB rows, live processes — before trusting the summary. If
  delegation results are persistently hollow, check `config.yaml → delegation.model` and set it to
  the main model family (matching `model.default`). The docs state "delegation runs on the main
  model family" but the live config may disagree.

## Status note

Proven mechanically once (2026-06-08): autonomous dispatch works; the Opus verifier
independently re-checks ground truth and correctly blocks bad handoffs. Treat as "proven once,
not production-hardened" — prefer low-stakes parallel jobs until a track record accumulates.
