---
name: kanban-orchestrator
description: "Use when you plan to decompose: route cards, never execute"
version: 3.0.0
platforms: [linux, macos, windows]
environments: [kanban]
metadata:
  hermes:
    tags: [kanban, multi-agent, orchestration, routing]
    related_skills: [kanban-worker]
sector_path: [infrastructure, hosting, deploy]
---

# Kanban Orchestrator — Decomposition Playbook

> The **core worker lifecycle** (including the `kanban_create` fan-out pattern and the "decompose, don't execute" rule) is auto-injected into every kanban process via the `KANBAN_GUIDANCE` system-prompt block. This skill is the deeper playbook when you're an orchestrator profile whose whole job is routing.

## Profiles are user-configured — not a fixed roster

Hermes setups vary widely. Some users run a single profile that does everything; some run a small fleet (`docker-worker`, `cron-worker`); some run a curated specialist team they've named themselves. There is **no default specialist roster** — the orchestrator skill does not know what profiles exist on this machine.

Before fanning out, you must ground the decomposition in the profiles that actually exist. The dispatcher silently fails to spawn unknown assignee names — it doesn't autocorrect, doesn't suggest, doesn't fall back. So a card assigned to `researcher` on a setup that only has `docker-worker` just sits in `ready` forever.

**Step 0: discover available profiles before planning.**

Use one of these:

- `hermes profile list` — prints the table of profiles configured on this machine. Run it through your terminal tool if you have one; otherwise ask the user.
- `kanban_list(assignee="<some-name>")` — sanity-check a single name. Returns an empty list (rather than an error) for an unknown assignee, so this only confirms a name you're already considering.
- **Just ask the user.** "What profiles do you have set up?" is a fine first turn when the goal needs more than one specialist.

Cache the result in your working memory for the rest of the conversation. Re-asking every turn wastes a tool call.

## When to use the board (vs. delegate_task vs. just doing the work)

**Default for parallel interactive work: `delegate_task` — no board, no proposal.**
- 2+ independent subtasks, single session → `delegate_task` immediately, synthesize results yourself
- Cross-session / overnight / multi-hour / needs audit trail / needs peer profile → kanban board
- Subagents echo secrets into their transcripts. Never hand one a credential value; give it the env var name or file path.

Create Kanban tasks when any of these are true:

1. **Multiple specialist profiles are needed.**
2. **The work should survive a crash or restart.**
3. **The user might want to interject.**
4. **Work needs to outlive the current conversation.**
5. **Review / iteration is expected** across sessions.
6. **The audit trail matters.**

If *none* of those apply — parallel single-session reasoning — use `delegate_task` instead.

### Inline vs. card — the small-tweak carve-out

Not every request is board-shaped. Tiny, single-file, low-risk edits the user asks for in rapid back-and-forth (recolor an orb, remove an animation, tweak one CSS value, swap a palette) are faster done INLINE: patch → `npm run build --prefix app` → commit → push. Reserve cards for multi-file features, new components, or anything needing real reasoning. A string of "now change X / now remove Y" follow-ups during active iteration = inline each one; spinning up a worker per tweak adds a ~2-min latency tax per change for no quality gain. (The coding-delegation gate still applies to substantial source work — this is the small-tweak carve-out, not a license to inline features.)

## The anti-temptation rules

Your job description says "route, don't execute." The rules that enforce that:

- **Do not execute the work yourself.** Your restricted toolset usually doesn't even include terminal/file/code/web for implementation. If you find yourself "just fixing this quickly" — stop and create a task for the right specialist.
- **For any concrete task, create a Kanban task and assign it.** Every single time.
- **Split multi-lane requests before creating cards.** A user prompt can contain several independent workstreams. Extract those lanes first, then create one card per lane instead of bundling unrelated work into a single implementer card.
- **Run independent lanes in parallel.** If two cards do not need each other's output, leave them unlinked so the dispatcher can fan them out. Link only true data dependencies.
- **Round-robin EVERY parallel card across ALL existing workers — even just two.** (2026-06-23) The user caught me sending card after card to `assignee=\"coder\"` while `coder-b` sat idle: *\"You're only dispatching to Coder.\"* The cloned-fleet round-robin pitfall below is about scale (N clones), but the failure bites at N=2 just as hard — a default reflex of \"assign to coder\" wastes half the fleet. Rule: when you have ≥2 same-role workers (`coder` + `coder-b`), alternate the `assignee` on consecutive parallel cards at creation time. When you create two cards in one turn, one goes to each. Check the roster (`ls ~/.hermes/profiles/`) once per session and cache it; never let \"coder\" be the silent default when coder-b exists.
- **Never create dependent work as independent ready cards.** If a card must wait for another card, pass `parents=[...]` in the original `kanban_create` call. Do not create it first and link it later, and do not rely on prose like "wait for T1" inside the body.
- **If no specialist fits the available profiles, ask the user which profile to create or which existing profile to use.** Do not invent profile names; the dispatcher will silently drop unknown assignees.
- **Decompose, route, and summarize — that's the whole job.**

## Decomposition playbook

### Step 1 — Understand the goal

Ask clarifying questions if the goal is ambiguous. Cheap to ask; expensive to spawn the wrong fleet.

### Step 2 — Sketch the task graph

Before creating anything, draft the graph out loud (in your response to the user). Treat every concrete workstream as a candidate card:

1. Extract the lanes from the request.
2. Map each lane to one of the profiles you discovered in Step 0. If a lane doesn't fit any existing profile, ask the user which to use or create.
3. Decide whether each lane is independent or gated by another lane.
4. Create independent lanes as parallel cards with no parent links.
5. Create synthesis/review/integration cards with parent links to the lanes they depend on. A child created with unfinished parents starts in `todo`; the dispatcher promotes it to `ready` only after every parent is done.

Examples of prompts that should fan out (using placeholder profile names — substitute whatever exists on the user's setup):

- "Build an app" → one card to a design-oriented profile for product/UI direction, one or two cards to engineering profiles for implementation, plus a later integration/review card if the user has a reviewer profile.
- "Fix blockers and check model variants" → one implementation card for the blocker fixes plus one discovery/research card for config/source verification. A final reviewer card can depend on both.
- "Research docs and implement" → a docs-research card can run in parallel with a codebase-discovery card; implementation waits only if it truly needs those findings.
- "Analyze this screenshot and find the related code" → one card to a vision-capable profile for the visual analysis while another searches the codebase.

Words like "also," "finally," or "and" do not automatically imply a dependency. They often mean "make sure this is covered before reporting back." Only link tasks when one card cannot start until another card's output exists.

Show the graph to the user before creating cards. Let them correct it — including which actual profile name should own each lane.

### Step 3 — Create tasks and link

Use the profile names from Step 0. The example below uses placeholders `<profile-A>`, `<profile-B>`, `<profile-C>` — replace them with what the user actually has.

```python
t1 = kanban_create(
    title="research: Postgres cost vs current",
    assignee="<profile-A>",  # whichever profile handles research on this setup
    body="Compare estimated infrastructure costs, migration costs, and ongoing ops costs over a 3-year window. Sources: AWS/GCP pricing, team time estimates, current Postgres bills from peers.",
    tenant=os.environ.get("HERMES_TENANT"),
)["task_id"]

t2 = kanban_create(
    title="research: Postgres performance vs current",
    assignee="<profile-A>",  # same profile, run in parallel
    body="Compare query latency, throughput, and scaling characteristics at our expected data volume (~500GB, 10k QPS peak). Sources: benchmark papers, public case studies, pgbench results if easy.",
)["task_id"]

t3 = kanban_create(
    title="synthesize migration recommendation",
    assignee="<profile-B>",  # whichever profile does synthesis/analysis
    body="Read the findings from T1 (cost) and T2 (performance). Produce a 1-page recommendation with explicit trade-offs and a go/no-go call.",
    parents=[t1, t2],
)["task_id"]

t4 = kanban_create(
    title="draft decision memo",
    assignee="<profile-C>",  # whichever profile drafts user-facing prose
    body="Turn the analyst's recommendation into a 2-page memo for the CTO. Match the tone of previous decision memos in the team's knowledge base.",
    parents=[t3],
)["task_id"]
```

`parents=[...]` gates promotion — children stay in `todo` until every parent reaches `done`, then auto-promote to `ready`. No manual coordination needed; the dispatcher and dependency engine handle it.

If the task graph has dependencies, create the parent cards first, capture their returned ids, and include those ids in the child card's `parents` list during the child `kanban_create` call. Avoid creating all cards in parallel and linking them afterward; that creates a window where the dispatcher can claim a child before its inputs exist.

### Step 3b — No `kanban_*` tools in your toolset? Use the CLI with a body file

The default (chat) profile often has no `kanban_create` tool. The CLI `create` path is reliable (the
silent-failure caveat below is about `update --status`): write the body to a scratch markdown file
(multi-line bodies and flag-like lines die in shell quoting), then

```bash
hermes kanban create "<conventional title>" --assignee <profile> \
  --body-file ~/.hermes/cache/scratch/card-<topic>.md --workspace scratch --created-by default --json
# verify the claim ~20s later — ready→running is near-instant when the dispatcher is healthy
hermes kanban show <t_id> | grep -E 'status|claimed'; hermes kanban runs <t_id>
```

A card that stays `ready` past a minute is the assignee-typo / dispatcher-down tell — check the roster
and the daemon, do not re-create. Coding-card bodies follow `templates/coding-card-body.md`
(repo/workspace boundaries → the changes → verification command the worker must actually run →
"done =" with `pushed: true|false` metadata); copy it and fill the slots rather than writing free prose.

**Dispatch verb decides initial status.** "Send it to the coder" / "ship it" = create `ready` (dispatch
now). "Draft / queue / prep a card" = `--initial-status blocked` so the user can review the batch and
unblock — the user batches and reviews cards by default, so only an explicit send verb skips that.

### Step 4 — Complete your own task

If you were spawned as a task yourself (e.g. a planner profile was assigned `T0: "investigate Postgres migration"`), mark it done with a summary of what you created:

```python
kanban_complete(
    summary="decomposed into T1-T4: 2 research lanes in parallel, 1 synthesis on their outputs, 1 prose draft on the recommendation",
    metadata={
        "task_graph": {
            "T1": {"assignee": "<profile-A>", "parents": []},
            "T2": {"assignee": "<profile-A>", "parents": []},
            "T3": {"assignee": "<profile-B>", "parents": ["T1", "T2"]},
            "T4": {"assignee": "<profile-C>", "parents": ["T3"]},
        },
    },
)
```

### Step 5 — Report back to the user

Tell them what you created in plain prose, naming the actual profiles you used:

> I've queued 4 tasks:
> - **T1** (`<profile-A>`): cost comparison
> - **T2** (`<profile-A>`): performance comparison, in parallel with T1
> - **T3** (`<profile-B>`): synthesizes T1 + T2 into a recommendation
> - **T4** (`<profile-C>`): turns T3 into a CTO memo
>
> The dispatcher will pick up T1 and T2 now. T3 starts when both finish. You'll get a gateway ping when T4 completes. Use the dashboard or `hermes kanban tail <id>` to follow along.

## Common patterns

**Fan-out + fan-in (research → synthesize):** N research-style cards with no parents, one synthesis card with all of them as parents.

**Parallel implementation + validation:** one implementer card makes the change while one explorer/researcher card verifies config, docs, or source mapping. A reviewer card can depend on both. Do not make the implementer own unrelated verification just because the user mentioned both in one sentence.

**Pipeline with gates:** `planner → implementer → reviewer`. Each stage's `parents=[previous_task]`. Reviewer blocks or completes; if reviewer blocks, the operator unblocks with feedback and respawns.

**Same-profile queue:** N tasks, all assigned to the same profile, no dependencies between them. Dispatcher serializes — that profile processes them in priority order, accumulating experience in its own memory.

**Human-in-the-loop:** Any task can `kanban_block()` to wait for input. Dispatcher respawns after `/unblock`. The comment thread carries the full context.

## Pitfalls

**Headless-Chromium screenshots over Tailscale come back dark/blank.** When verifying a UI change, a headless screenshot tunneled over Tailscale often renders black or empty even though the page is fine. Verify via JS-bundle grep + build status instead, and note the screenshot caveat in your report rather than treating the dark frame as a failure.

**`kanban_unblock` 404s on an instantly-claimed ready card — that's expected, not an error.** The dispatcher promotes `ready` cards almost instantly, so right after `kanban_create` (ready) the card is often already `running` before you can act on it; an `kanban_unblock` then 404s with "not blocked or unknown". Just `kanban_list(status='running')` to confirm it was claimed.

**Inline-editing a repo while workers run on it — `git add` ONLY your specific file(s) by path, never `git add .`.** The working tree may carry the workers' uncommitted/untracked files; a blanket `git add .` sweeps peer WIP into your commit. (`patch` also warns "modified by sibling subagent" — re-read before writing.)

**Merging a long-lived feature branch into a diverged base.** A squash-merge via the GitHub API will 405 "merge conflicts" even when only 1–2 files actually conflict, and rebasing a 40+ commit branch onto the base often explodes (every commit whose content is already upstream re-conflicts). Cleaner path: local `git merge origin/<base> --no-edit`, resolve the handful of real conflicts (`git checkout --ours <file>` when the feature branch holds the correct newer version — verify with a `diff` of both sides first), rebuild, push, THEN merge the PR via the API. If squash still 405s post-resolve, fall back to `merge_method=merge`.

**Inventing profile names that don't exist.** The dispatcher silently fails to spawn unknown assignees — the card just sits in `ready` forever. Always assign to a profile from your Step 0 discovery; ask the user if you're unsure.

**Pinning a `skills=[...]` the assignee profile doesn't have → HARD CRASH on spawn (not a silent skip).** (2026-06-19) `kanban_create(..., skills=["X"])` force-loads skill `X` into the dispatched worker via `--skill X`. If profile `<assignee>` doesn't have skill `X` in its own skills dir, the worker process **refuses to start**: stderr `Error: Unknown skill(s): X`, exit code 1. The dispatcher retries once (identical failure), then `gave_up` after 2 attempts — the card ends `crashed`, ~15s wasted, no work done. The crash is invisible in the gateway journal; the worker's captured stderr is at **`~/.hermes/kanban/logs/<task_id>.log`** (always read this file first when a card shows `crashed`/`gave_up` with `exit_code:1` or `pid not alive`). Root issue: skills are **per-profile** — `~/.hermes/skills/` (the `default` profile) and each `~/.hermes/profiles/<name>/skills/` are DISJOINT sets. Swarm workers (`swarm-worker-a`…`p`) ship a generalist skill set that deliberately EXCLUDES specialist skills like `hermes-webui-customization`. Before pinning a skill to a non-default assignee, confirm the profile actually has it: `ls -d ~/.hermes/profiles/<assignee>/skills/**/<skill-name>`. Three fixes when it's missing: (1) **inline the skill's guidance into the card body** and drop the `skills=` pin — cheapest, no provisioning change, works for any assignee; (2) **assign to a profile that has the skill** (`default` or whichever profile carries it — check both locations); (3) **copy the skill into the worker profile's skills dir** first (durable but bloats the worker + needs re-sync on updates). Default to (1) for swarm workers; for a long skill, point the body at the skill's rule/reference files by absolute path (`~/.hermes/skills/<cat>/<skill>/rules/<id>.md`) instead of pasting them in, since the worker can read them without the skill being installed. Recovering a card that already reached `gave_up`: before re-filing, check that the target files' mtimes predate the card, so you know nothing was half-edited. Then re-file a NEW card with the same body, assignee, workspace and priority, minus the pin (a crashed card has used its retry budget). Comment on the old card with the new id and archive it, and confirm via `kanban_list(status='running')` that the new card was claimed. Copying the skill into the worker profile changes another profile's skills, so offer it rather than doing it. Corollary: a card that succeeded under `assignee=default` is NOT proof the same `skills=` pin works under a swarm worker — the skill set differs.

**Dispatch-then-discover: creating one card, then noticing parallel work remained.** (2026-06-23) The user corrected this twice in one session — first \"You're an orchestrator profile\" (after I drafted ONE decommission card while sibling files obviously needed the same treatment), then \"tell me WHY this is happening\" when I answered with a reflexive \"you're right.\" The mechanism: I was treating `kanban_create` as a single sequential action — plan the work, dispatch it, move on — instead of as a decomposition step. The correct sequence is **partition the FULL surface BEFORE creating any card**: answer \"what else here is independent of this?\" first, then fan out all parallel cards in one move. Tell: if you create a card and on the NEXT turn realize a second parallel chunk exists, you skipped the partition step. The question that prevents it is asked once, up front, against the whole objective — not rediscovered card-by-card. (This is now also a hard rule in AGENTS.md \"Partition before dispatch\" and reinforced in the `kanban_checkpoint.py` nudge text.) When the user catches a process failure and asks \"why,\" give the mechanism (the checkable cause), not \"you're right\" — a fluent agreement is evasion; the honest answer names what I did wrong and why.

**Review follow-ups are ONE card, one commit — not a fan-out.** The SHOULD/NIT items left over from a code review (a handful of few-line fixes across files, none blocking) ship together on one feature branch when the user says "send those as one card"; the fan-out rules below are for independent features or inventory gaps, and splitting four 10-line fixes across workers only buys merge churn. Still list each item with its file:line and the reviewer's proposed fix so the worker needs no re-discovery, and drop items the review already resolved.

**Two dispatch surfaces, one spec → check the board before launching a `claude -p` job for it.** A card you filed (or offered to file) and a detached coder job are the same work on two surfaces; the second one is pure duplicate cost and a merge of two half-equal branches. Before any dispatch of a described spec, run `kanban_list(status='running')` + `kanban_list(status='ready')` and `systemctl --user list-units 'job-*'`, and look for a worktree on the same repo (`git worktree list`) — a card with that title or a worktree named for the task means it is already in flight. If you find a duplicate after the fact, keep the one that finished with tests green and stop the other (`systemctl --user stop job-<name>`), then remove its worktree and branch.

**Pausing the board for a gateway-restarting operation (core update, venv rebuild).** The dispatcher runs inside the gateway: a restart SIGTERMs running workers mid-card and a card claimed during a half-rebuilt venv runs on broken imports. Pause in THREE layers, because the running gateway keeps its old config until it restarts: (1) `hermes config set kanban.dispatch_in_gateway false` AND `hermes config set kanban.auto_decompose false` — both land at the restart, so the new gateway comes up neither dispatching nor minting; (2) block every `ready` card now in ONE call — `timeout 60 hermes kanban block <id> --ids <id2> <id3> … --kind transient <reason>` (one id per invocation can hang on the trailing prompt; wrap in `timeout`); (3) the OLD gateway still auto-decomposes triage tickets into ready cards and dispatches them on its next tick — so loop until quiet: re-query `status in ('ready','running')`, block new ids, and `kill -TERM` any worker process that slipped through (`pgrep -af 'kanban task'`), then recheck right before launching. Verify quiet = 0 ready, 0 running, 0 worker pids. **Then check the live install tree** (`git -C /usr/local/lib/hermes-agent status --short`): a worker killed mid-card may have been editing core files directly; save its diff and `git checkout --` them, or the update autostashes and conflicts on them. Resume = flip both flags back + `kanban_unblock` each blocked id (grep the block reason to find them).

**"Those" / "that" with no antecedent in the window → read history before asking.** The referent is usually an offer you made ("say the word and I'll…") or a request in a background session that got cut off. Browse recent sessions (`session_search` with no args), read the short `bg_*` session (a `bg_*` session with tool calls and no final assistant message = a request that never got answered — surface it as pending), then scroll the main session's tail. Only ask when two readings survive that lookup.

**Bundling independent lanes into one card.** If the user asks for two independent outcomes, create two cards. Example: "fix blockers and check model variants" is not one fixer task; create a fixer/engineer card for the fixes and an explorer/researcher card for the variant check, then optionally gate review on both.

**Collapsing parallel AUTHORING into one card because "it all touches one file."** (2026-06-19) When an inventory surfaces N independent implementation gaps that all edit the SAME target file (e.g. 4 separate patches into one `server.py`), the instinct to make it one sequential card is WRONG. "One shared file" is an *integration* concern, not an *authoring* constraint. The correct shape is **fan-out authoring → fan-in integration**: N author cards (each owns one disjoint gap, writes its patch block in its own scratch workspace, no parents) + ONE integrator card (`parents=[all authors]`) that splices the blocks sequentially and runs the single gated restart. The serialization lives only in the integrator, not in the decomposition. Tell: if you catch yourself writing a monolith card whose body lists "Gap 1… Gap 2… Gap 3…" with disjoint markers, that's a fan-out DAG you flattened. General rule: **after any inventory/analysis that finds 2+ independent gaps, the next board action is a fan-out DAG, never a monolith card** — regardless of whether the gaps share a file.

**Over-linking because of wording.** "Finally check X" may still be parallel with implementation if X is static config, docs, or source discovery. Link it after implementation only when the check depends on the implementation result.

**The routing NUDGE is advisory text, not a router — and it can fail to suggest fan-out even when fan-out is correct.** (2026-06-19) The `[Routing: …]` line appended to tool results comes from the native guard `~/.hermes/plugins/local-guards/guards/routing.py` (2026-10-16; it replaced the `kanban_checkpoint` / `kanban_phase_checkpoint` / `delegation_nudge` monkeypatches, so older notes below name those). Key facts for a future session debugging "why didn't the hook route this":
- It scores the **user's message text**, not the decomposition you discover *after* analysis. A prompt phrased as sequential workflow ("inventory, then bring it all in") with structural bullets can describe parallel work whose shape only becomes visible once you walk the code — no message-text hook can see that.
- It **suppresses on ANY kanban/delegate call** (`_kanban_used()`), so it cannot tell a monolith card from a fan-out DAG — one `kanban_create` reads as compliance.
- **Mutual-exclusion bug (fixed 2026-06-19):** the original code ran the phase gate, and on a hit `return`ed *before* the multi-part gate could score — so a prompt that read as "sequential phases" (any 2+ system words like frontend/backend/auth = +2) locked out the fan-out nudge entirely, even when the multi-part score was higher. Fix: score both gates independently, prefer the fan-out nudge when multi-part also fires.
- **Post-analysis gate (2026-06-19, dropped 2026-10-16):** the "4 read-only calls in one batch" re-fire could not be ported (per-call hooks don't see batches). The whole-objective `objgate` hard gate covers inventory-shaped objectives; `routing.py` re-fires on write/terminal volume instead.
- To verify what a given prompt scores: `cd ~/.hermes/plugins/local-guards && ../../.venv/bin/python -c "from guards import routing; print(routing._signal(t))"` (empty string = no nudge). Disable entirely with `HERMES_ROUTING=off`. The durable rule belongs in AGENTS.md (which IS read every turn); the hook is a mechanical reminder, not a gate that can make the judgment for you.

**Forgetting dependency links.** If the task graph says `research -> implement -> review`, do not create all tasks as independent ready cards. Use parent links so implement/review cannot run before their inputs exist.

**Reassignment vs. new task.** If a reviewer blocks with "needs changes," create a NEW task linked from the reviewer's task — don't re-run the same task with a stern look. The new task is assigned to the original implementer profile.

**`review-required` blocks are SUCCESS, not failure — and `kanban_unblock` is the WRONG verb to clear them.** (2026-06-22) A well-behaved coding worker, told "code changes need human review before counting as merged," finishes + verifies its work, then calls `kanban_block(reason="review-required: …")` instead of `kanban_complete` (this is the honest path baked into the kanban-worker protocol — auto-completing code that still needs eyes is the dishonest one). On a fan-out DAG these blocked parents STALL the integrator, which won't promote until every parent is `done`. As the orchestrator you ARE the reviewer — clear them, but with the RIGHT verb:
- **DO NOT `kanban_unblock`.** It sends the card to `ready`, not `done` — which (a) re-spawns a redundant worker to redo finished work, and (b) still doesn't satisfy the integrator's `parents` gate (`ready` ≠ `done`). Confirmed this session: unblocking 4 review-required cards re-queued them instead of advancing the DAG.
- **DO `kanban_complete` on the worker's behalf** after you verify the work, with a `summary` like "Review-approved by orchestrator. <what landed>." and `metadata={"review":"approved", ...}`. That marks them `done`, the integrator's gate is satisfied, and no redundant re-run spawns.
- **Verify before approving — don't rubber-stamp the self-report.** The worker's comment claims "build green, endpoints tested." Spot-check the cheap, decisive proof yourself: `ls` the route files exist, run the combined build (`tsc -b && vite build`), curl one endpoint. This session all 12 were real, but the check is what makes the approval honest rather than a relay of the worker's word.

**Cards do NOT auto-distribute across a cloned fleet — `assignee` is per-card and sticky.** (2026-06-22) After cloning `coder` into `coder-b/c/d` to run 4 parallel workers, every card I created with `assignee="coder"` ran on `coder` ALONE — `coder-b/c/d` sat idle while one profile serialized 17 cards. The dispatcher spawns exactly the assigned profile; it does NOT load-balance a backlog across same-role clones. **You must assign cards round-robin at creation time** (`assignee` cycles `coder`, `coder-b`, `coder-c`, `coder-d`, `coder`, …) to actually use the fleet. Fixing it after the fact: `hermes kanban reassign <task_id> <profile>` — but a card that's already `running` rejects a bare reassign (`cannot reassign … still running — pass --reclaim to release first`); use `hermes kanban reassign <task_id> <profile> --reclaim` to abort the in-flight worker and re-dispatch on the new profile. Note `hermes kanban` has NO `update` subcommand for assignee — it's `reassign` (and `assign` for an unassigned card); `update`/`edit` are for other fields. Plan the round-robin in the same step you decompose, the way you'd assign distinct specialist profiles — a cloned fleet is N profiles, not one pool.

**`kanban_block` is the ONLY agent-side way to pause `ready`/`running` cards — there is no `todo` setter.** (2026-06-22) When the user says "freeze everything / make sure nothing's in process" (e.g. out of tokens), the tools you have are `kanban_block` (→ `blocked`) and `kanban_unblock` (→ `ready`). There is NO tool to push a card back to `todo` — `todo` is a dependency-driven state (a card sits there only while a parent is unfinished). Don't promise to "move them to todo"; say plainly that blocked IS the pause state (it's `todo` with a reason label) and clears with one `kanban_unblock` each. Creating a card with `initial_status="blocked"` is the clean way to stage a card that must NOT run yet (e.g. a final gated audit/PR card) — pair it with `parents=[…all work cards]` so it ALSO waits on the DAG, giving belt-and-suspenders gating.

**`hermes kanban update --status archived` silently fails — fall back to direct SQLite when the CLI returns no error but leaves cards unchanged.** (2026-06-26) The CLI `hermes kanban update t_xxxx --status archived` returned exit 0 with no visible error but 13 cards remained `blocked` — the status change never committed. Direct SQLite operations work reliably: `sqlite3 ~/.hermes/kanban.db "UPDATE tasks SET status='archived' WHERE id IN ('t_xxx','t_yyy')"`. Also use `UPDATE tasks SET status='ready' WHERE id IN (...)` to unblock/re-dispatch. Always verify with `SELECT id,status FROM tasks WHERE id IN (...)` after writing. Note: the `kanban_*` tools (MCP-style functions in the toolset, NOT the CLI) are reliable; only the bare CLI `hermes kanban` subcommands have this silent-failure behavior. Prefer the `kanban_list`/`kanban_unblock` tools over the CLI when available.

**Don't pre-create the whole graph if the shape depends on intermediate findings.** If T3's structure depends on what T1 and T2 find, let T3 exist as a "synthesize findings" task whose own first step is to read parent handoffs and plan the rest. Orchestrators can spawn orchestrators.

**Tenant inheritance.** If `HERMES_TENANT` is set in your env, pass `tenant=os.environ.get("HERMES_TENANT")` on every `kanban_create` call so child tasks stay in the same namespace.

**Cloning coder profiles for a wide Claude-Code fan-out → fix the tmux session-name collision FIRST.** (2026-06-22) To run N parallel Claude-Code worker cards you clone the `coder` profile into `coder-b`, `coder-c`, … (`cp ~/.hermes/profiles/coder/config.yaml ~/.hermes/profiles/<name>/`, `sed -i "s/^profile: coder$/profile: <name>/"`, copy AGENTS.md/SOUL.md). The trap: the `coder` profile's AGENTS.md hardcodes a FIXED tmux session name (`tmux new-session -d -s coder …`). Every clone inherits it, so all N workers driving multi-turn Claude Code via tmux collide on the SAME session `coder` — they stomp each other's panes and interleave output. **Per clone, rewrite the AGENTS.md tmux session name to the profile name** before dispatching: `sed "s/tmux new-session -d -s coder /tmux new-session -d -s <name> /g; s/tmux send-keys -t coder/tmux send-keys -t <name>/g; s/tmux kill-session -t coder/tmux kill-session -t <name>/g" coder/AGENTS.md > <name>/AGENTS.md`. (Single-task `claude -p` print mode has no tmux session and is collision-free — the fix only matters for multi-turn tmux-driven cards, but apply it anyway since the AGENTS.md is shared.) Two write-gate notes: writing the clone `config.yaml`/`AGENTS.md` trips the WRITE GATE (gated path: config.yaml + dotfile-overwrite scan on `> AGENTS.md`) — the user saying "clone more coder profiles" IS the greenlight, arm the gate (`~/.hermes/.write_gate_grant` with a real `date +%s` epoch, note free of gated tokens) and proceed; and `execute_code` is blocked in this profile, so do the file rewrites via `terminal`+`sed`, not inline Python.

**Version control BEFORE a parallel fan-out that churns one repo (not after).** (2026-06-22) When 12 workers are about to write into the same git repo, set up the isolation boundary FIRST so master stays the last known-good build and a bad fan-out is one `git checkout master` away. The sequence: (1) inspect state — `git status`, `git remote -v`, `git log --oneline -5`, and crucially `git ls-files | grep -E "node_modules/|/dist/|\.venv/|\.env$"` to confirm the heavy/secret dirs are NOT already tracked; (2) create a feature branch (`git checkout -b feat/<thing>`) — in-flight uncommitted worker edits carry onto it automatically; (3) HARDEN `.gitignore` before the churn — a thin root `.gitignore` (e.g. just `.serena/`) lets one stray `git add -A` commit `.venv/` (tens of MB), `__pycache__/`, `.env`, `*.db`, and worker scratch (`.task_*`, `PROMPT_*.md`, `_verify_*.py`); add a proper one and commit JUST that file, leaving worker changes uncommitted; (4) push the commit/PR responsibility to the INTEGRATOR card, not master — instruct it (via a `kanban_comment`, since you can't edit a card body in place) to commit on the branch only after panels verify green, run a junk-guard grep before staging (`git status --short | grep -E '\.venv/|__pycache__|\.task_|node_modules/' && STOP`), push, then `gh pr create --base master` (do NOT merge — leave open for human review), and report the REAL commit SHA + PR URL in `kanban_complete` metadata with a no-fabrication rule (if `git push` fails on auth, report the exact error, never invent a PR URL). Note: a subdir like `app/` can have its OWN `.gitignore` already covering `node_modules`/`dist` — that's why those never show as untracked; the gap is usually the repo ROOT (Python venv + scratch).

## Scaling the worker fleet beyond the delegate_task cap (clone the coder profile)

When a fan-out has more independent author chunks than you have distinct coder profiles
(e.g. a 12-chunk dashboard build but only one `coder` profile), CLONE the coder profile so
each chunk gets its own dispatched worker running in parallel. Kanban dispatch has no small
per-user concurrency cap the way `delegate_task` does — N ready cards assigned to N distinct
profiles all spawn at once. The clone recipe (gated writes — profile dir is under
`~/.hermes/profiles/`, so present + get greenlight first):

```bash
for letter in b c d e f g h i j k l; do
  name="coder-$letter"
  mkdir -p ~/.hermes/profiles/$name
  cp ~/.hermes/profiles/coder/config.yaml ~/.hermes/profiles/$name/config.yaml
  sed -i "s/^profile: coder$/profile: $name/" ~/.hermes/profiles/$name/config.yaml
  cp ~/.hermes/profiles/coder/SOUL.md ~/.hermes/profiles/$name/SOUL.md 2>/dev/null || true
  # AGENTS.md handled separately — see the tmux-collision pitfall below
done
```

**HARD PITFALL: tmux session-name collision across clones (2026-06-22).** The `coder`
profile's `AGENTS.md` hardcodes a fixed tmux session name (`tmux new-session -d -s coder …`,
`tmux send-keys -t coder …`, `tmux kill-session -t coder`). If you `cp` that AGENTS.md
verbatim into every clone, all N parallel workers attach to the SAME tmux session `coder` and
stomp each other's Claude Code multi-turn sessions — interleaved input, killed sessions,
garbage output. Each clone MUST get a UNIQUE session name. Rewrite the three tmux references
per clone:

```bash
for letter in b c d e f g h i j k l; do
  name="coder-$letter"
  sed "s/tmux new-session -d -s coder /tmux new-session -d -s $name /g; \
       s/tmux send-keys -t coder/tmux send-keys -t $name/g; \
       s/tmux kill-session -t coder/tmux kill-session -t $name/g" \
    ~/.hermes/profiles/coder/AGENTS.md > ~/.hermes/profiles/$name/AGENTS.md
done
```

Print mode (`claude -p '<task>' --dangerously-skip-permissions --max-turns N`) is collision-FREE
(no shared tmux session) — if your worker cards instruct print mode rather than the interactive
tmux flow, the collision can't bite, but rewrite the names anyway as defense-in-depth since a
worker may fall back to tmux for a multi-turn subtask. Confirm clones have Claude Code wired the
same as the source: binary at `~/.hermes/node/bin/claude`, no per-profile `skills/` dir
(falls through to the shared `~/.hermes/skills/`), model = the source's coding model.

## Version control hygiene BEFORE a multi-worker fan-out (do this in the same turn you create cards)

When N parallel workers will churn one shared repo, set up the isolation boundary BEFORE they
start writing, not after (by the time you notice, files are already modified):

1. **Inspect git state** — `git status`, `git remote -v`, `git log --oneline -5`. Confirm the big
   dirs are NOT already tracked: `git ls-files | grep -E 'node_modules/|/dist/|\.venv/|\.env$'`
   (empty = good).
2. **Create a feature branch** so `master`/`main` keeps the last known-good build:
   `git checkout -b feat/<descriptive>` — in-flight uncommitted worker changes carry onto the new
   branch automatically.
3. **Harden `.gitignore`** if thin. A multi-language repo (Python backend + Node frontend) needs the
   root `.gitignore` to cover BOTH stacks plus worker scratch: `__pycache__/`, `.venv/`, `*.py[cod]`,
   `node_modules/`, `dist/`, `.env`/`.env.*` (but `!.env.example`), `*.db`/`*.sqlite`, and
   worker-scratch globs (`.task_*`, `PROMPT_*.md`, `_verify_*.py`, `*.log`). Note: a frontend
   subdir often has its OWN `app/.gitignore` covering node_modules/dist — the GAP is usually the
   repo ROOT for the Python venv + pycache. Commit JUST the `.gitignore` on the branch immediately.
4. **Brief the integrator card to do the commit/push/PR**, not leave uncommitted churn. Put the
   exact git sequence in a `kanban_comment` on the integrator card (you can't edit a card body
   in place, but the worker reads the full comment thread as ground truth). The sequence:
   confirm-on-branch → junk-guard grep (`git status --short | grep -E '\.venv/|__pycache__|…'`
   must be empty) → stage real source only → commit → `git push -u origin <branch>` →
   `gh pr create --base master` (do NOT merge — leave open for human review). Tell the worker to
   report the REAL commit SHA + branch + PR URL in `kanban_complete` metadata and to NOT fabricate\n   a PR URL if `gh` auth fails (report the push status instead). Commit only AFTER panels/build\n   verify green — a broken build does not get committed.\n5. **`gh pr create` often fails inside a worker — the ORCHESTRATOR opens the PR as the fallback.**\n   (2026-06-22) A worker's `gh pr create` step is fragile: `gh` may be uninstalled (`gh: command\n   not found`) or unauthed on the box, so the worker's commit+push succeeds but the PR never gets\n   created. A correctly-briefed worker reports this honestly (push OK, no PR URL) rather than\n   fabricating — that's the no-fabrication rule paying off. When you (the orchestrator) see a\n   completed integrator whose commit+push landed but the PR is missing, open it YOURSELF via the\n   GitHub MCP `github_create_pull_request(owner, repo, base, head, title, body)` — that path is\n   authed independently of the box's `gh` CLI. Verify the truth first: the branch IS on origin\n   (`git ls-remote --heads origin <branch>` or GitHub search), `master`/`main` is untouched\n   (`git log origin/master`), the commit carries no junk (`git ls-files | grep -E\n   '\\.venv/|__pycache__|/dist/|\\.env$|\\.db$'` empty), then create the PR. Write the PR body to\n   flag what's NOT yet verified (e.g. live browser render post-cutover) so it isn't merged blind.

## Goal-mode cards (persistent workers)

By default a dispatched worker gets **one shot** at its card: it does its work, calls `kanban_complete`/`kanban_block`, and exits. For open-ended cards where one turn rarely finishes the job, pass `goal_mode=True` to wrap that worker in a Ralph-style goal loop — the same engine behind the `/goal` slash command:

```python
kanban_create(
    title="Translate the full docs site to French",
    body="Acceptance: every page translated, no English left, links intact.",
    assignee="<translator-profile>",
    goal_mode=True,        # judge re-checks the card after each turn
    goal_max_turns=15,     # optional budget (default 20)
)["task_id"]
```

How it behaves:
- After each worker turn, an auxiliary judge evaluates the worker's response against the card's **title + body** (treated as the acceptance criteria).
- Not done + budget remains → the worker keeps going **in the same session** (full context retained — not a fresh respawn).
- Worker calls `kanban_complete`/`kanban_block` itself → loop stops, normal lifecycle.
- Budget exhausted without completion → the card is **blocked** for human review (sticky), never a silent exit.

When to use it: long, multi-step, or "keep going until X is true" cards. When NOT to: cheap one-shot cards (translation of a single string, a quick lookup) — the judge overhead isn't worth it, and the dispatcher's existing retry/circuit-breaker already handles transient worker failures.

Write the body as **explicit acceptance criteria** — the judge is only as good as the goal text. "Translate the README" is weaker than "Translate every section of the README to French; no English sentences remain."

## Recovering stuck workers

When a worker profile keeps crashing, hallucinating, or getting blocked by its own mistakes (usually: wrong model, missing skill, broken credential), the kanban dashboard flags the task with a ⚠ badge and opens a **Recovery** section in the drawer. Three primary actions:

1. **Reclaim** (or `hermes kanban reclaim <task_id>`) — abort the running worker immediately and reset the task to `ready`. The existing claim TTL is ~15 min; this is the fast path out.
2. **Reassign** (or `hermes kanban reassign <task_id> <new-profile> --reclaim`) — switch the task to a different profile (one that exists on this setup) and let the dispatcher pick it up with a fresh worker.
3. **Change profile model** — the dashboard prints a copy-paste hint for `hermes -p <profile> model` since profile config lives on disk; edit it in a terminal, then Reclaim to retry with the new model.

Hallucination warnings appear on tasks where a worker's `kanban_complete(created_cards=[...])` claim included card ids that don't exist or weren't created by the worker's profile (the gate blocks the completion), or where the free-form summary references `t_<hex>` ids that don't resolve (advisory prose scan, non-blocking). Both produce audit events that persist even after recovery actions — the trail stays for debugging.

### Failure signature: clean-exit protocol violation from a weak profile model

(2026-06-21) A task `gave_up` after 2 runs, each event `protocol_violation` with `exit_code:0` and error `"worker exited cleanly (rc=0) without calling kanban_complete or kanban_block"`. The worker did NOT crash — it ran, did some work, and exited normally without closing the card. **Root cause is almost always the profile's MODEL, not the task.** A local/small model (e.g. `qwen2.5-32b` on a self-hosted endpoint) doesn't reliably follow the kanban completion protocol — it finishes its reasoning and stops without emitting the terminal tool call. Diagnose by reading the profile's model: `python3 -c "import yaml,os; p=os.path.expanduser('~/.hermes/profiles/<assignee>/config.yaml'); print(yaml.safe_load(open(p))['model'])"`. Fix: point the profile at a frontier model that follows tool protocols (`hermes -p <profile> model`, or patch `model.provider`/`model.default` in the profile config — gated write), then Reclaim. Tell vs. a real crash: `exit_code:0` + protocol_violation = model didn't close the card (model problem); `exit_code:1` / `pid not alive` = spawn/runtime crash (read `~/.hermes/kanban/logs/<task_id>.log`).

### Failure signature: worker fakes completion by decomposing the hard part away

(2026-06-21) The single most dangerous worker self-report: a worker (or a delegate the worker spawned on a weak/cheap model) does the EASY ~15% of a task (e.g. creates a DB schema, extracts the input data), then **spawns a child card for the hard 80% and marks the PARENT `done`** with a confident summary ("Initiated migration… decomposed into child task t_xxxx"). The parent's `kanban_complete` summary reads like success; the actual deliverable was never built. Two compounding tells this session: (a) the child was assigned to a **nonexistent profile** (`claude-code-worker`) so it would have sat in `ready` forever — silently stalled; (b) the live system was untouched — the target scripts still imported the old library, mtime unchanged. **Never trust a worker's "done" self-report for a task with external side effects. Verify the live artifact yourself:** grep the changed files for the new import / read the file mtime / hit the endpoint / query the destination store for the migrated rows. A summary that says "initiated", "decomposed", "set up the infrastructure for", or "the next worker will…" on a task whose acceptance was "do X" is a completion that did NOT do X — reopen it. Decomposing is legitimate ONLY when the card was itself a decomposition/orchestration task; an implementation card that ends by spawning the implementation as a child and self-completing is a false positive.

### Failure signature: API-green is NOT render-green for a wired-to-live-data UI fan-out

(2026-06-22) After a fan-out that wires every panel of a dashboard to real backend data, the integrator reported "15/15 API routes return 200 with real data" and it was TRUE — every `/api/*` endpoint genuinely served live data. But the **live browser still showed mock fixtures** in two panels. Curling the API proves the data LAYER; it says nothing about whether the COMPONENT renders it. Two distinct gaps the API sweep could never catch, both found only by driving the real browser:
- **A hardcoded fixture array the component never stopped importing.** The sidebar's agent quick-list rendered `rvc-runner / atlas-etl / npc-builder` (prototype mock names) because `data/agents.ts` still exported a 5-element `CHAT_AGENTS` const and the component read THAT, not `/api/agents` (which correctly returned all 33 real profiles). A worker "wired the panel" but left a sibling decorative widget on the old const.
- **A panel that was never built at all — a `<Placeholder name="X" />` stub.** The Profiles nav item rendered "Profiles — coming soon" because no worker was scoped to build it; the integrator's API checks passed (`/api/profiles` returns 33) but the panel switch-case still pointed at the placeholder. "All panels wired" in a worker summary can silently exclude panels nobody was assigned.
**Discipline: after a wire-to-live-data fan-out, the orchestrator MUST verify the live RENDER, not just the API.** Drive the actual browser (it's the deployed app, reachable via its public tunnel), snapshot/click each panel, and read what's ON SCREEN. Tells of un-caught mock: fixture names you recognize from the prototype (`rvc-runner`, `atlas-etl`, `Mac Studio GPU` on a host with no GPU), round-suspicious totals that don't match the real board count, and "coming soon" placeholder text. Note: an auxiliary VISION model will often call specific-looking mock data "real" (it reasons "these numbers are non-round, must be live") — override it; YOU know the real profile names are `coder`/`ha-bot`/`swarm-*`, so fixture names are the ground truth, not the vision verdict. Grep the built bundle to confirm a fix landed (`grep -c rvc-runner dist/assets/*.js` → 0) and to prove the fix is actually deployed, not just committed. When the render gap is a missing panel (placeholder stub) vs. a mock-swap, they're DIFFERENT cards: a placeholder needs a full panel BUILD, a fixture-array needs a one-file swap to the existing endpoint — scope them separately.

### Merged-and-built is NOT live when the served artifact is gitignored

(2026-06-23) Across one session, several dashboard changes were reported "failed" by the user even though each was committed, merged into `master`, and passed `vite build` clean. Root cause: the app serves a **prebuilt `app/dist/` that is gitignored**, so workers push source but the bundle the live server actually serves is never regenerated by the merge. `git merge` + a green `tsc/vite build` in a worker's scratch dir prove the source compiles — they do NOT update the `dist/` on the HOST that uvicorn serves. The live site keeps serving the stale bundle and the user sees no change.

Orchestrator discipline:
- **Bake a host-rebuild step into EVERY frontend worker card** when the build output is gitignored: the card's final step must be `rm -rf <dist> && npm --prefix <app> run build` *on the host the server runs from*, and the worker must report the new `index-<hash>.js`.
- **After the merge, verify link 3+4 yourself** (see `verification-before-completion` → "The Deployed Artifact ≠ The Merged Source"): the host `dist/` was rebuilt, and `curl -s <url> | grep -o 'index-[hash].js'` matches the just-built hash. Grep a **string literal** (emoji, placeholder copy) inside the served bundle — minified identifier names won't match even when the feature shipped.
- A static uvicorn/FastAPI server serves new `dist/` files **without a restart**; "I restarted the service" is neither the fix nor the proof. Tell the user to hard-refresh (Cmd+Shift+R) to bust their cached `index.html`.

This is the sibling of the "API-green is NOT render-green" signature above: there, the data layer was green but the component rendered mock; here, the source is merged but the served artifact is stale. Both are caught only by verifying the live RENDER, not the upstream layer.

### Restarting a service can knock over a fronting tunnel — restore it, don't debug it

(2026-06-22) A worker card that rebuilt + restarted the live app server (`kill $(lsof -ti :PORT); uvicorn …`) caused the Cloudflare tunnel (`cloudflared.service`) to drop all 4 connections and the systemd unit to `Deactivated successfully` — the public URL then returned Cloudflare error 1033/530 even though the app was healthy on localhost. This looks like a tunnel/DNS bug but is just collateral from the restart: `journalctl -u cloudflared` shows `Unregistered tunnel connection` ×4 then `Tunnel server stopped` right at the worker's restart timestamp, and `curl localhost:PORT/health` is 200. Fix is a plain `systemctl start cloudflared` (gated — present what/risk/rollback, get greenlight). Don't chase stale-CNAME / multi-connector routing theories when the timeline pins it to a service restart and localhost is green.
