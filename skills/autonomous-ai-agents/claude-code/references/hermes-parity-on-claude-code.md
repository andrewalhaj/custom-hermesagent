# Making a separate Claude Code install behave like Hermes (parity add-on)

Use this when the user wants Claude Code on another device (typically the work laptop from `work-device-starter-pack.md`) to "operate like you". Parity is built from Claude Code's native features: CLAUDE.md imports, subagents, hooks, plugins and MCP. Ship it as ONE add-on zip with ONE installer that MERGES into the existing settings.json and never replaces it.

## Mapping table (check the current docs before you build; the feature set moves fast)
| Hermes | Claude Code equivalent |
|---|---|
| SOUL.md / USER.md | Separate `~/.claude/SOUL.md` and `USER.md`, imported from `~/.claude/CLAUDE.md` with `@` lines. Port the principles faithfully, but translate every Hermes mechanism and drop home or personal content. |
| MEMORY.md + Supabase recall | The employer-tenant memory MCP plus its recall hooks (see the memory section of `work-device-starter-pack.md`) |
| `delegate_task` children | Subagents (`Agent` tool): own context, parallel, `background: true`, per-agent `model` / `tools` / `disallowedTools`, nesting within a depth limit. "Agent teams" and background agents cover kanban-style fan-out. |
| Delegation discipline (checkpoint guard) | Not native: Claude Code delegates only when a task matches an agent's description. Add (a) the orchestrator rule to SOUL.md, (b) custom agents in `~/.claude/agents/`: `researcher` (Sonnet, read-only plus web, cites sources), `builder` (inherits the main model, small tested slices), `verifier` (no Edit/Write, adversarial fresh-context PASS/FAIL), and an `Explore` override on Haiku; (c) a nudge hook (below). |
| write_gate + backup-first rule | Permission `ask`/`deny` rules plus a PreToolUse hook that backs up config/script files once per session before an edit and DENIES the edit if the backup fails |
| verification_checkpoint | A Stop hook: if files were edited since the last user message with no shell/test run afterwards, block once and ask for evidence. It must honour `stop_hook_active`, or it loops. |
| delegation_checkpoint | A PostToolUse counter that injects one reminder per user turn at the Nth edit/command |
| Session history / extractors | See "Session continuity" below |
| Model | `model` set to Opus in settings.json; the ask permission mode as default (not auto or bypass) |
| Cron, Telegram, watchdogs, profiles | Do not port. Claude Code runs only while open; ongoing monitoring stays on the Hermes host or the employer's tools. |

Guards that only police Hermes machinery (kanban, routing, memory capacity, skill-review, billing bypass) have no counterpart; say so instead of inventing one.

## Hook rules (all of them)
- Stdlib Python run by the existing memory venv interpreter (Python starts faster than PowerShell for per-tool-call hooks). Every hook fails open on internal errors, can be disabled on its own via an env var, keeps a size-capped log that never contains file contents or secrets, and has unit tests plus a real `claude -p` run in a scratch project's `.claude/settings.json`, never the host's `~/.claude/settings.json`.
- Read the hooks docs for each event's input fields and output schema before coding. Exec form (`"command": "<python>"` + `"args": ["<script>"]`) is proven to fire and avoids shell-quoting of Windows paths; the installer writes absolute paths with JSON-escaped backslashes and re-parses the merged settings.json. The PG*/env values reach hooks through settings.json's top-level `env` key.
- Keep per-session/per-turn state in `tempfile.gettempdir()`, never `os.environ.get('TMPDIR','/tmp')`: TMPDIR is unset on Windows, so state writes fail silently and once-per-session logic fires on every call.
- Never write hook output into `~/.claude/backups/`; Claude Code keeps its own `.claude.json.backup.*` files there. Use a dedicated dir such as `~/.claude/file-backups/`.
- When anchoring "since the last user message" in `transcript_path` JSONL, skip user entries whose content is a `tool_result`; every tool result is logged as a user message, so a naive anchor resets after each tool call and Stop/nudge hooks never fire.
- Real `claude -p` hook tests on the Hermes host still write to `~/.claude/` (backups, state). Remove those artifacts after the test run.
- SessionEnd has a short default timeout (about 1.5 s) and no decision control: spawn a detached worker (`creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW` on Windows, `start_new_session=True` on POSIX) and exit; the worker's `claude -p` call needs `--no-session-persistence --strict-mcp-config` plus a recursion-guard env var, or each summary spawns another session to summarise.
- Plugin skills preload in a subagent's `skills` frontmatter by namespaced name (`<plugin>:<skill>`, e.g. `ponytail:ponytail-review`); a skill with `disable-model-invocation: true` is skipped silently, so also tell the agent to invoke it through the Skill tool.

## Session continuity ("why can't you recall last session?")
Claude Code starts each session blank. Offer the fixes in this order:
1. `claude --continue` (the last session in this folder) or `claude --resume` (a picker). Sessions are stored per folder, so always start from the same project folder.
2. A CLAUDE.md rule: `memory_add` a session summary (done / decisions / open items) before `/exit` or at the end of a phase; when the user refers to earlier work, run `memory_list_recent` before saying "I don't know". Keyword recall misses prompts with no content words ("6.11 is done").
3. Automatic: a SessionEnd hook that spawns a DETACHED worker and returns at once. The worker reads `transcript_path`, redacts secrets and runs `claude -p` on Haiku with a recursion-guard env var. It stores the result tagged `session` plus a session-id tag, and replaces the previous summary on resume rather than duplicating it. A companion SessionStart hook injects the latest one or two summaries for this folder. Each summary costs one small model call on the employer's plan; say so.
- If a screenshot shows **auto mode** on, flag it: Claude approves its own commands in that mode, which contradicts any "show each command and wait" runbook. Shift+Tab cycles it back.

## Ponytail as the mandatory coding mode
Read the plugin's `hooks/*.js` before advising. In v5.x:
- A SessionStart hook injects its ruleset at the default level `full`, resolved from `PONYTAIL_DEFAULT_MODE`, then the config file's `defaultMode` (`%APPDATA%\ponytail\config.json` on Windows), then `full`.
- A SubagentStart hook injects the same ruleset into EVERY subagent unless `PONYTAIL_SUBAGENT_MATCHER` is set.
- It switches off when the user sends a bare `stop ponytail` / `normal mode`, or `/ponytail off`.
- Without Node.js the hooks fail SILENTLY and coding continues without it.
To enforce it:
- pin `PONYTAIL_DEFAULT_MODE=full` in the settings `env` and leave the matcher unset;
- add a SOUL rule: if the startup text doesn't show the Ponytail mode, stop and tell the user rather than code without it;
- have `verifier` run the Ponytail review skill on code changes;
- add checklist checks (`node --version`, mode visible at startup and in subagents).
- Before shipping the plugin, grep its hooks for network calls (`https.request`, `fetch(`, `net.connect`) and list each `child_process` use, so the data-egress line in the tools record is factual. Plugins install only from inside Claude Code (`/plugin marketplace add` + `/plugin install`); the installer prints those commands rather than running them.

## "Just copy all your skills over"
Do not copy wholesale.
1. Enumerate enabled skills (all installed, minus `skills.disabled` in config), excluding `.curator_backups` and `.bak` paths.
2. Diff the list against the starter pack.
3. Leak-scan every file of each remaining skill (home IPs, host names, `~/`, hermes, supabase, ollama/qwen, laya, kaggle, trading, tailscale, telegram, token/key mentions). Expect most skills to hit.
4. Sort them into three buckets: host-only (Hermes, kanban, home automation, games, trading: useless off-host), personal (media generation, job hunting), and work-useful. Port only the work-useful bucket.
5. Port with one subagent: scrub, translate tool names (`delegate_task` → subagents, `browser_exec` → Playwright MCP, cron → "on-demand; Claude Code has no scheduler"), allow merging overlapping design skills (with every merge reported), ASCII text, and frontmatter checked against the skills docs.
6. Leave out integrations the employer hasn't confirmed using (Linear, Notion, Box, GitHub); ask about them.

## Extra tools (MCP / CLIs) on an employer device
- The user is a tenant admin: no manager-approval memos or "ask X first" gates. Write a tools record instead: per tool, publisher, licence, transport, auth, what data leaves the laptop and to whom. Lowest privilege first: read-only delegated Graph scopes by default; admin-consent scopes are granted by the user himself (document portal + CLI consent steps and pre-check his directory role), write scopes opt-in.
- The installer registers the tools by default, with a `-Skip<Tool>` switch for each and `-WhatIf`.
- GitHub: `gh` CLI only (`winget install GitHub.cli`, `gh auth login`; gh keeps the token in Windows Credential Manager) and only when `gh auth status` shows an employer account. Do not ship the archived `@modelcontextprotocol/server-github` reference server or store a PAT in an env var/config file. Never the personal GitHub on a work laptop.

## Building the add-on
Fan the build out to parallel children with a staging-dir contract: `cc-parity/{identity,hooks,agents,mcp,skills}/`, each child writing only its own subfolder. An integrator runs afterwards to build the installer, the merged settings fragments, the README, the checklist section and the zip. When the user approves or adds scope mid-build, steer the running child rather than respawning it. A steer can arrive too late (it comes back as `missed_steer`), so the integrator checks that every steer actually landed.

Parent review before integration (each child's report is a claim):
- grep the whole staging tree for removed concepts (`<manager-name>|approv`, `hermes`) and for proper names or facts the user never supplied; children invent specifics (e.g. a manager's surname on an "approved by" line) to fill templates.
- Re-run the ASCII + leak scans and the test suite yourself; read the settings fragments.
- Diff data files children touched (CSV, JSON datasets) against the source: a child under a zero-hit scan rule will alter legitimate data to dodge a substring match (a font name containing a banned word). Restore the data and whitelist the hit instead.
- Write the defects to `cc-parity/INTEGRATION-FIXES.md` so the integrator applies them, instead of patching children's files piecemeal.

Installer merge into settings.json on Windows PowerShell 5.1 (put these in the integrator brief):
- `ConvertFrom-Json` has no `-AsHashtable` and returns PSCustomObject: add keys with `Add-Member` / `psobject.Properties`, never by hashtable indexing.
- `ConvertTo-Json` defaults to depth 2 and silently flattens nested hooks to strings: always `-Depth 20`.
- Single-element arrays collapse to scalars: wrap with `@()` / `,@()`.
- Back up first, append to existing hook arrays without duplicating on re-run, write UTF-8 without BOM, re-parse the written file and restore the backup on failure. Test it with portable `pwsh` (tarball under scratch) against a fake profile seeded with the real starter settings; pwsh 7 is not 5.1, so list the 5.1-only risks.
