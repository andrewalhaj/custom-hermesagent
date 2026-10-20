# Scrub fan-out recipe (working-tree phase)

How to turn a gitleaks report on a private repo into a clean tree in ~15 minutes wall clock, with the orchestrator only partitioning, launching and verifying.

## 0. Inventory (orchestrator, one script)

From the gitleaks JSON of a `git archive` export, build two sets per file: infra rules hit (`tailscale-cgnat-ip`, `known-vps-ips`, `host-codenames`, `tailnet-hostname`, `private-domain`) vs `root-deploy-path` only. Print rule ids + line numbers with IPs redacted (`sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}/<IP>/g'`) so the partition can be made without echoing addresses into the transcript. Save the lists to a scratch JSON — every later step reads from it.

Lanes:
| Lane | Files | Tool |
|---|---|---|
| A | code with infra literals | `claude -p` jobs, ≤6 files each, grouped by concern (ops/watchdogs, pattern/regex files + the leak-audit script itself) |
| B | code with `/root` paths only (incl. `scripts/` and `templates/` inside skills/) | `claude -p` jobs, ≤6 files each |
| C | prose with infra literals | `delegate_task` subagents, one per skill subtree, ≤8 files each |
| D | prose with `/root` paths only | orchestrator script (see SKILL.md step 3) |

All lanes edit the same checkout in parallel: file sets must be disjoint, and every worker is told "no git state changes" — the orchestrator commits once at the end.

## 1. Env contract (sequential, before lane A)

1. Script extracts each literal from the scripts into `cache/scratch/env-additions.txt` as `KEY=VALUE`; stdout shows `KEY: <n> chars` only; keys already in `.env` are skipped.
2. Append to `.env` via a script file with a dated backup copy first (gated write → native approval prompt).
3. Job 0 (`claude -p`, ~1 min): create `scripts/hermes_env.py` (`HERMES_HOME`, `env(key, default)`, `require(key)`, `path(key, default)`; lazy `.env` parse, quotes/`export` tolerated, later duplicate wins, missing file = empty) and `scripts/lib/hermes_env.sh` (`hermes_env KEY [DEFAULT]`, `hermes_require KEY`; greps the file, never sources it) plus a pytest file. Wait for it; lanes A/B import these.

Key naming: role-based, never codename-based (`VPS_A_HOST`, `STUDIO_SSH`, `HA_HOST`, `BACKUP_OFFSITE_SSH`/`_DIR`, `WALL_DASH_URL`, `KB_HOST_NAME_PATTERN`, `UPDATE_NOTIFY_CHAT_ID`). Optional behaviours (offsite copy, remote rerank) skip with a warning when unset; required ones exit naming the key.

## 2. Alias contract (paste into every A/C brief)

- CGNAT addresses → role placeholder `<LH01-tailnet-ip>` / `<LH02-tailnet-ip>` / `<VPS-A-tailnet-ip>` / `<ha-tailnet-ip>`, else `<tailnet-ip>`; public VPS addresses → `<VPS-A-ip>` / `<VPS-B-ip>`.
- Codenames: each real machine name / codename family → its public alias (LH01, LH02, VPS-A, VPS-B), DB admin user → `<db-admin-user>`; variable names carrying a codename get the alias too (`<CODENAME>_HOST` → `VPS_A_HOST`).
- Tailnet names → `<tailnet>.ts.net`; private domain → `example.com` keeping the subdomain label.
- the absolute home-dir prefix (`/<home>/.hermes`) → `HERMES_HOME` in code, `~/.hermes` in prose; other absolute home paths → `~/<x>`.
- Regex/classifier files that enumerate host names: move the alternation into an env key (`KB_HOST_NAME_PATTERN`), omit the regex entry when empty — never compile `\b()\b`.
- Test corpora: swap real addresses for RFC 5737 (`203.0.113.x`) unless a rule keys on the CGNAT range — then keep one non-host CGNAT example with `# gitleaks:allow`.
- The leak-audit script builds its pattern at runtime from the local strict gitleaks config (`${GITLEAKS_STRICT_CONFIG:-$HOME/.config/gitleaks/gitleaks.toml}`), exit 2 if missing.

## 3. Worker verification clause (every brief)

`grep -nE '<cgnat regex>|<root-home>/|<codename list>|<tailnet name>|\.ts\.net|<domain>|<vps ip prefixes>' <files>` must print nothing; `bash -n` / `python3 -m py_compile`; full pytest with the managed interpreter; `git diff --stat` of their files; report with values redacted.

## 4. Orchestrator verification (never skipped)

1. `git status --short` — no files outside the assigned sets, no stray scratch files.
2. Every test tree under the published dirs (`scripts/tests`, `scripts/knowledge/tests`, `plugins/*/tests`) + `bash -n` every shell file + `py_compile` every python file in the tracked tree.
3. Broad grep over ALL tracked files, `skills/.bundled_manifest` excluded (bare `<root-home>/`, codenames, `.ts.net`, domain, 9+-digit numbers) — the strict rule set is narrower than the leak, and skill prose outside `scripts plugins hooks` carries most of the leftover paths.
4. gitleaks on a fresh `git archive` export with the strict config; only allowlisted checksum hits may remain.
5. Sample the prose diff for snippets where `~` landed inside quotes (`grep -E "['\"]~/"` on added lines).

## 5. Publish checklist (after §4 is clean, on the keep branch)

1. Probe the PAT against the target repo (`pat-permission-probe.sh <owner> <repo>`, github skill): Contents 404 on an empty repo is fine; Administration 200 is needed for the visibility flip and vulnerability alerts; Workflows RW only shows as a push rejection.
2. Add `.github/workflows/gitleaks.yml` (copy from a sibling repo; `branches: [<remote default branch>]`), `.github/dependabot.yml` (ecosystems with a tracked manifest only), `.gitleaks.toml` (generic rules + path allowlist for the manifest checksum + regex allowlist for doc placeholders such as `(?i)bearer\s+(sk-)?x{8,}`). Allow-list `.gitignore`: add `!.github/`, `!.github/**`, `!.gitleaks.toml`.
3. Export the tree and scan it twice: `gitleaks dir . --config .gitleaks.toml` from inside the export (what CI runs) and with the strict local config. Both must be clean except allowlisted checksums. A generic home-path rule means the whole-tree `<root-home>/` → `~/` sweep from lane D must have covered skills prose too.
4. Commit on the keep branch (no `SKIP_GITLEAKS`). Then, as its own call: `git checkout --orphan public && git add -A && git commit -m "feat: publish …"`; verify `git log public | wc -l` = 1, `git diff --stat master public` empty, strict grep on `public` = 0.
5. `git remote add origin …`, push `public:<remote default branch>` (gated push — arm first). Local history branch is never pushed.
6. Wait for the CI run, audit the pushed content via the API (`Accept: application/vnd.github.raw`), then flip visibility as its own gated call; enable secret scanning + push protection; add the repo to the weekly audit list.

## Launch shape

`claude -p` jobs: one `systemd-run --user` unit each via the `run_one.sh` template in the `claude-code` skill (`templates/run_one.sh`), prompts in `$OUT/prompts/<name>.txt`, `--model sonnet --effort medium --max-turns 45`, MCP disabled. Typical job: 1–2 min, $0.20–0.35. Subagents: `delegate_task`, one brief per subtree, results arrive after the turn ends — do the lane-D script and the code-job verification while waiting.
