# Porting the setup to a separate (work) device: the starter pack

Use this when the user wants Claude Code on another machine, especially an employer laptop, carrying over working style and general skills. The symlink approach in SKILL.md only works on the Hermes host. A second device gets a scrubbed, self-contained pack instead.

## Hard rule: separation both ways
- Nothing personal goes to the work device: home IPs and hostnames, Hermes internals, the knowledge store, trading, Laya/ML programs, personal accounts or emails.
- Nothing from the employer stays on personal infrastructure. When a session surfaces employer facts already sitting in personal stores (Supabase rows, MEMORY.md), flag it unprompted. Offer to migrate them to the employer-side store and delete them from the personal side once that store exists.
- Confirm the account type and the employer's data rules before building anything; ask for the OS in the same question. If the user is a tenant admin, he decides: write a factual record of what each part does and where data goes, never an approval memo or an "ask your manager" gate.

## Pack contents (one zip, a single top-level folder)
1. `CLAUDE.md`: working style (from USER.md behavioural entries) plus the work role. No credentials, internal IPs or server names.
2. `skills/`: about 30 to 50 general, class-level skills. Leave out anything that depends on Hermes, personal infrastructure or home automation. Each one is adapted, not copied:
   - tool names: `terminal` → Bash, `read_file` → Read, `patch` → Edit, `write_file` → Write, `web_search` → WebSearch, `web_extract` → WebFetch, `delegate_task` → Agent (subagent; older docs call it Task), `memory` / `memory_add` → "record it in CLAUDE.md".
   - frontmatter: `name` = directory name (lowercase-hyphen) plus `description`. Drop Hermes-only keys (`metadata.hermes`, `load_when`, `sector_path`).
   - replace personal-infrastructure sections with generic patterns, and strip node IPs and personal domains.
3. `settings.json`: read-only commands go in `allow`, everything else asks, and destructive commands go in `deny` (force push, `diskpart`, `reg delete`, `bcdedit`, shutdown, execution-policy bypass, recursive deletes of system folders).
4. An installer (on Windows: `install-starter.ps1`, PS 5.1-compatible). It copies into `%USERPROFILE%\.claude\`, backs up what is already there, and supports `-WhatIf`. Every shipped `.ps1` must be pure ASCII AND saved as UTF-8 with BOM, CRLF: Windows PowerShell 5.1 reads BOM-less UTF-8 as Windows-1252, so an em dash becomes `â€"`, whose last byte is a curly quote that PowerShell treats as a string delimiter, giving `missing string terminator` / `missing closing '}'` parser errors. Put that rule in every child brief that ships a `.ps1`, and make the scrub fail on any byte > 0x7F after the BOM.
5. `SETUP.md`: steps in order, each citing Anthropic's docs:
   - Git for Windows;
   - Claude Code (`irm https://claude.ai/install.ps1 | iex` or `winget install Anthropic.ClaudeCode`);
   - `claude` and sign in;
   - the installer;
   - ask Claude Code to "list your skills";
   - optional connections to tool servers;
   - a first-week trial with 3 tasks.
6. `MANIFEST.md` (what was included and excluded, and why) and `scrub.py`.

Build it with ONE delegated subagent, staged under `cache/scratch/`, with the zip in `~/reports/`. Nothing on this host changes.

## Verification (the orchestrator does it, not the child)
1. Unzip into a fresh scratch dir. Count the files and skills, and parse `settings.json`.
2. Run your OWN leak grep over the unzipped tree; never accept the child's "scrub clean" on its own. The patterns to grep for:
   - tailnet range `100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.`, VPS IPs, `~/`, `.hermes`;
   - personal-stack names: hermes, supabase, kaggle, laya, ollama, qwen, tailscale, telegram, discord, LH/host names;
   - token prefixes: `sk-ant`, `ghp_`, `AKIA`, `BEGIN … PRIVATE`;
   - trading terms, the employer's internal hostnames and IPs, personal emails.
   Exclude `scrub.py` itself, since it contains the patterns.
3. Fact-check every environment claim in `CLAUDE.md` (vendor and tool names, fleet tools) against WHEN it was learned. Memory from before the user started the job describes a previous context. Grep for the term in memory and backups: if it only shows up before the job start date, flag it for the user to correct rather than ship it as fact.
4. A smoke test where this host allows it: run `claude -p` in a temp dir with the skills populated, and check it lists every expected skill name.
4b. Check each `.ps1` yourself in Python (`b[:3] == b'\xef\xbb\xbf'`, no byte > 0x7F after it, CRLF present); a `grep -P '[\x80-\xFF]'` hit on line 1 is just the BOM.
5. Tell the user what could not be verified here: for example, an installer the host has no PowerShell to run (tell them to run `-WhatIf` first), or an enterprise seat provisioning flow the docs did not confirm (ask the employer's Claude admin).

## Guiding the Windows install (the user runs it; give PowerShell commands)
- Say which shell: "open Windows PowerShell". Users with Git for Windows/MSYS2 often land in a Bash window, where `powershell -ExecutionPolicy …` fails (a pasted `$` prompt becomes an empty `$powershell` variable). From Bash the equivalent is `powershell.exe -ExecutionPolicy Bypass -File ./x.ps1`.
- Downloaded scripts: Properties → Unblock, then `powershell -ExecutionPolicy Bypass -File .\x.ps1 -WhatIf` (bypass applies to that one process only; never tell them to change the machine policy).
- The native installer puts `claude.exe` in `%USERPROFILE%\.local\bin` and may warn it is not on PATH (`claude` → CommandNotFoundException). Fix on the USER Path, no admin: read `[Environment]::GetEnvironmentVariable('Path','User')`, append the dir if absent, `SetEnvironmentVariable(...,'User')`, and also `$env:Path += ";$bin"` for the current window; already-open terminals need reopening. Diagnose with `Test-Path "$env:USERPROFILE\.local\bin\claude.exe"`.
- Git for Windows editor prompt: recommend VS Code (`code --wait`), else Notepad; avoid the Vim default for a new user. Everything else default.
- Sign-in check: `claude`, then `/status` must show the employer org; then installer, then "list your skills" and `/permissions`.
- Trust prompt for `C:\Windows\system32` means the window is elevated (admin PowerShell starts there). Tell them "No, exit", open a NON-admin PowerShell, `mkdir $HOME\claude-work; cd` into it (or the project repo), then `claude` and trust that folder; Claude Code's reach is the folder it starts in. Check elevation with `([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)`; if True, untick "Run as administrator" on the shortcut (Properties → Advanced) or in the Windows Terminal profile. Nothing in this setup needs admin.
- When the user asks for a consolidated "step by step again", give ONE numbered list in three parts (Claude Code → starter pack → memory DB), each step a command plus its expected output, starting from where they actually are. Re-read the shipped package's README/setup files BEFORE writing it, and route around any defects you find in them inside the steps (say so at the top).

## Remote access requests ("can you just do it on my laptop if I enable Tailscale?")
Decline and redirect. Joining an employer-managed laptop to the personal tailnet bridges the corporate network to personal hosts without IT approval; the agent would act with the user's Entra/`az login` session; every output would flow through the PERSONAL Claude plan instead of the employer's Enterprise agreement; and Tailscale/OpenSSH usually need admin on a managed device. The right executor is Claude Code on the laptop itself: give a paste-in prompt ("Read README.md and setup-db.md in this folder and install this; show each command and wait for my OK; stop on any error; store no passwords") and keep troubleshooting from screenshots.

## "My Claude Code is missing stuff": skills vs plugins vs Hermes internals
- The pack copies SKILLS only. Plugins on the Hermes host's Claude Code (list them from `~/.claude/plugins/installed_plugins.json` and `enabledPlugins` in `~/.claude/settings.json`) must be installed on the device: `/plugin marketplace add <owner>/<repo>` then `/plugin install <name>@<marketplace>`, restart, run its help command. Look inside the plugin's `hooks/` first: JS hooks need Node.js, which the native Windows install lacks (`winget install OpenJS.NodeJS.LTS`). Third-party marketplaces may be blocked by enterprise managed settings; that is the employer's call.
- Hermes context files: SOUL.md and USER.md have no native slot. A merged CLAUDE.md keeps the rules but loses SOUL's voice and self-knowledge sections, so prefer separate files imported with `@` lines. MEMORY.md is personal and is replaced by the memory DB. AGENTS.md is not auto-read (put `@AGENTS.md` in a project CLAUDE.md).
- Guard patches, delegation, session continuity, Ponytail enforcement, "copy all your skills" and extra-tool approval: see `hermes-parity-on-claude-code.md`.

## Post-install findings that are not faults
- `~\.claude\skills\synced\<uuid>_<uuid>\` (with `.bucket-*`, manifest and last-sync files, and inner folders such as `docx`, `pdf`, `pptx`, `xlsx`) appears to be the Claude account's synced skills. It is a container, not a skill: exclude it from frontmatter checks and never delete it. Check for duplicates with the pack's own `docx`/`pdf`/`xlsx`; if the synced ones load in Claude Code (`pptx` appears in "list your skills"), move the pack copies to a backup folder. When asking the user for a listing, give the one-level command, not `-Recurse`.
- The DB firewall is a single auto-added client-IP rule: that is correct, not a fault. Ask whether the office IP is static and whether the VPN is full-tunnel. Otherwise add one named rule per location (`az postgres flexible-server firewall-rule create ... -r <name>`) and update it when that IP changes. Never widen to a range or "allow Azure services".

## Handing the setup to Claude Code itself (runbook + checklist)
When the user wants "an md file to give Claude Code", write it FOR Claude Code to execute:
- Open with a rules block: show each command and wait for OK (read-only checks may be batched), never elevate, never handle or echo passwords or tokens (only `expiresOn`), back up any existing file before modifying it, verify each phase with its stated check, and stop on failure with the error, the single most likely cause and the fix.
- Include a known-facts table (subscription, resource group, server, DB, folders); phases in order, each with commands and a pass check; the interactive steps the user must type themselves (`/plugin`, browser sign-in, `/exit`); and a closing report table with a list of backups.
- Write the health checklist as a separate file: a Mermaid architecture diagram, an `# | Check | Command | Pass if` table per layer, end-to-end pipeline tests that clean up their own test rows, a symptom → cause → fix table, rollback steps, and a "built / optional / not built vs reference design" comparison so the user sees the honest gap. Mark the untested parts (e.g. az CLI syntax from memory) and tell Claude Code to adapt only syntax via `--help`.
- Update the checklist in place when a finding is resolved (e.g. exclude `synced`), and re-send the file.
- **Fix brief for an installed pack** (the user benchmarked it and wants Claude Code to apply fixes): one `.md` the user saves in the install dir and tells Claude Code to "do". Structure:
  1. Context with the user's own results table.
  2. Rules: back up each file to `<file>.bak-<timestamp>`, change only the listed fixes, test rows tagged and deleted in `finally`, ASCII-only `.ps1`, run the tests before and after and report both counts, stop and report if the code doesn't match the brief.
  3. Per fix: Cause (mechanism, verified against the shipped source first, not just the user's screenshot), Diagnose-before-change where the cause depends on the machine (e.g. token source), Change (exact SQL/code), Test.
  4. Verify: re-run the same benchmark and state expected ranges, including "equal is a legitimate finding".
  5. Report back: files and backups, test counts, diagnose lines, before/after table.
  Keep it shareable (no personal IPs or paths). Don't let it collide with a newer pack still being built; say which one to install.

## What to tell the user about the permission rules
`deny` and `ask` rules match the start of a command. A wrapped command (`powershell -Command "Remove-Item …"`, `cmd /c …`) can get past a deny entry. Present the deny list as a speed bump, and the ask-before-running prompt as the real protection; tell them to keep it on.

## Delivery
Send the zip as `MEDIA:~/reports/<name>.zip` with a short contents list, the fix-before-install items, and the install order. Offer another delivery route in case the upload fails.

## Persistent memory on an employer tenant (if asked)
- **Design.** A small local MCP server with `memory_add` and `memory_search` tools:
  - storage in Postgres with pgvector, in the employer's cloud;
  - embeddings from the tenant's own embeddings deployment;
  - sign-in with the user's company account, so no database password sits on the device;
  - optional automatic recall through a `UserPromptSubmit` hook (confirm in the hook docs that its stdout is added as context).
- **Lessons from Hermes, built in from the start:**
  - one deliberate writer, with no background editors;
  - search for each fact straight after saving it;
  - every fact carries a date and a source;
  - anything that looks like a secret is refused at save time.
- **Privacy answers name all three places the data goes:**
  - the database, which stays in the tenant; the real risk is misconfiguration, so allow company sign-in only, a private network or IP allowlist, and minimal access;
  - the embeddings API, which isn't used for training, though the provider may keep requests for abuse monitoring;
  - the model provider, because every recalled fact is sent in the prompt.
  Storing adds no exposure beyond typing the same text into Claude, so the rule is: never store anything you couldn't paste into Claude. Customer NDAs set the real limit.
- **Cost answers:**
  - look up live prices and cite them; the smallest burstable Postgres tier makes up nearly all the cost, and embeddings cost pennies;
  - free-tier promotions apply to new personal accounts, not company tenants;
  - the routes to $0: a database server the company already pays for, a free database offer, or developer credits.
- **Wiring package (built and tested here, delivered as a zip):** Python MCP server (official `mcp` SDK, FastMCP over stdio; `psycopg` v3; `azure-identity`) with `memory_add` / `memory_search` / `memory_remove` / `memory_list_recent`.
  - Auth: `AzureCliCredential` token for `oss-rdbms` used as the Postgres password, the user's UPN as PGUSER, `sslmode=require`, refreshed before expiry. A test-only password env var exists for local testing and is documented as such.
  - Search works with no embeddings resource: Postgres full-text (generated tsvector + GIN). For recall over a whole prompt, build an OR-of-lexemes tsquery, never `websearch_to_tsquery`, which ANDs every word; recipe: retrieval-benchmarking `references/hybrid-search-pitfalls.md`. Keep `websearch_to_tsquery` only for an explicit exact/phrase mode. Vector (1536-d) + RRF hybrid switches on only when the tenant's embeddings env vars are set; ship a backfill script. Tell the user plainly that FTS matches words, not meaning.
  - Hook latency: the `UserPromptSubmit` hook is a NEW Python process per prompt, so in-process token memos and reused HTTP clients only help the long-lived MCP server. The hook is fast only if the on-disk (DPAPI) token cache hits; otherwise every prompt shells out to `az` (1-2 s on Windows). Import `azure.identity` lazily on a cache miss, and make `diagnose.py` print the embed-token source (memo/cache/live) plus 3 consecutive embed timings.
  - Never let an insert silently store a NULL embedding: keep the row, log a WARNING, and ship `backfill_embeddings.py --missing-only`, run in the background from the session-start hook at most hourly.
  - Calibrate the hook's minimum-similarity cutoff from no-answer queries. Vector search always returns top-k, so without a cutoff every unrelated prompt gets k injected "memories".
  - Safety: explicit tool writes only, read-back after each add, secret-pattern rejection, 2,000-char cap, exact-duplicate returns the existing id.
  - `install.ps1` creates a venv under `%USERPROFILE%\.claude-memory\` and PRINTS the `claude mcp add --scope user …` command instead of editing Claude Code config. Optional `UserPromptSubmit` recall hook: 3 s timeout, silent on any error.
  - Before delivering, check the child's docs against these known traps: `init_db.py` connects to the target database and does NOT create it, so the steps must create the empty database first (portal → server → Databases → + Add); user hook settings live in `%USERPROFILE%\.claude\settings.json` (not `%APPDATA%`), a hook entry is either one `command` string (quote the python.exe and script paths inside it) or the exec form `command` + `args` array (proven to fire; avoids Windows path quoting), and the PG* variables reach the hook through the top-level `env` key; tell the user to MERGE into the starter pack's settings.json, never replace it. Every PowerShell call example needs the `&` call operator.
  - Test against a throwaway `pgvector/pgvector:pg17` container bound to 127.0.0.1 with a real MCP client session (add, duplicate, secret reject, relevance on ~15 notes, remove, hook up/down); the child must remove the container. Verify the zip yourself (file list, py_compile, leak grep, `.ps1` bytes).
  - While it builds, give the user the parallel prerequisites: `winget install Python.Python.3.12`, `winget install Microsoft.AzureCLI`, reopen PowerShell, `az login`, and `az account get-access-token --resource-type oss-rdbms --query expiresOn` as the auth smoke test.
- **Creating the database in the portal:** follow the `azure-portal-provisioning` skill (field-by-field Postgres values, Review-page check, `azure.extensions` VECTOR step).
- **Recommend a trial first (benchmark-or-kill):** two weeks on CLAUDE.md alone, logging every time Claude Code forgets something it should have known. Build the database only if those misses pile up. State the resources, the monthly cost, the data rules, and rollback (delete the resource group) before creating anything.
- **Scaling to more users, or using the store as a team knowledge hub:** see `multi-user-rollout.md`.
