---
name: repo-publication-scrub
description: "Use when scrubbing a private repo for public release"
---

# Repo Publication Scrub

Full pipeline for taking a repo public without leaking infrastructure or secrets, and for remediating a repo that already leaked. Built 2026-07-08 after the hermes-dispatcher / ha-dashboard / mealio sweep.

## Hard rules (what counts as a leak)

Zero tolerance in anything public-facing, including git history:
- **Work identity**: employer AD/DNS domain, NetBIOS domain, `DOMAIN\user` logon names, work-laptop hostnames (asset-tag shape like `XXX-LT-<serial>`), and personal names/locations — including ones that were only ever *confabulations* being blocked (a reader can't tell). The strict config only catches what's in it: add the employer's domain/hostname/username to `private-domain`/`host-codenames` the day a new employer appears, or the audit stays green while the runbooks leak (caught by external review 2026-10-19 after publish).
- IPs: public server IPs AND Tailscale CGNAT (the 100.64–127 block, i.e. 100.64.x.x–100.127.x.x)
- Tailnet hostnames: `*.ts.net`, tailnet names (e.g. `<tailnet>.ts.net`), machine names (e.g. `<host>-macmini`)
- Host codenames: internal machine names such as VPS-A / VPS-B and cloud-instance slugs (e.g. `ubuntu-<size>-<region>-1`); LH01/LH02 are the approved public-facing codenames for local hosts. Codename rules in the strict config need `(?i)` — the lowercase forms live in code keyword lists and slip a case-sensitive rule.
- Scripts that use a subscription OAuth token outside the official client (reading `~/.claude/.credentials.json`, spoofed client headers) or carry hardcoded personal report paths: one-off tooling → untrack, never publish. Using it privately is Andrew's call; publishing it is not.
- Domains: private domains such as example.com and their subdomains
- Deploy paths: absolute home-dir paths (e.g. `<root-home>/...`) → use env-var-driven `~/` defaults
- Credentials: passwords, API keys, tokens, bcrypt/scrypt hashes, cloud instance IDs (Supabase project refs, Neo4j Aura IDs)
- **Committed SQLite/DB files** — mealio leaked real user emails + password hashes this way

## Workflow: taking a repo public (or remediating)

1. **Audit first** (delegate per repo — read-only):
   - gitleaks on the TRACKED tree only: export it first — `git archive master | tar -x -C ~/.hermes/cache/scratch/head-export` then `gitleaks dir <export> --config ~/.config/gitleaks/gitleaks.toml --report-format json --report-path <json>`. Running `gitleaks dir` on the live checkout scans ignored sessions/caches/venvs and runs for many minutes with hits that will never ship. History: `gitleaks git . --config <strict> --log-opts=<branch>`.
   - Summarise the JSON by `RuleID` in a script and split the rules into two tiers before reading any file list: infra-prose (`root-deploy-path`, `tailscale-cgnat-ip`, `host-codenames`, `known-vps-ips`, `tailnet-hostname`, `private-domain`) vs secret-shaped (everything else). Thousands of prose hits are a scrub workload; a single secret-shaped hit is a rotate-first incident. Lead the report with that split — it is what decides private-vs-public, not the total.
   - Also list `git ls-files -i -c --exclude-standard` (tracked despite a later ignore rule — archives, lock dirs) and tracked binaries (`.pkl`, `.db`); they are scrubbed by untracking, never by editing.
   - Working tree: `git ls-files | xargs grep -n -E '<banned patterns>'`
   - Full history: `git grep -E '<pattern>' $(git rev-list --all)` — every ref, not just HEAD
   - Committed secret files: `git log --all --diff-filter=A --name-only | grep -iE 'env|secret|credential|token|key|pass|\.db$'`
   - Check ALL remote refs: `git ls-remote origin` — branches AND `refs/pull/*`
2. **Triage gitleaks findings without ever printing secret values, then rotate only real+live ones BEFORE scrubbing** (a public secret is burned; scrubbing git doesn't un-leak it). Per finding, in a script on disk: (a) present in the upstream Hermes install (`grep -rIlF` under `/usr/local/lib/hermes-agent`) → already public, no action; (b) sha256 of the value vs sha256 of every live `.env` value → exact live match = rotate; (c) value is a prefix of a live key → partial leak, assess; (d) placeholder pattern (`your|example|xxx|<|${`) or checksum shape (manifest `name:<32hex>`) → false positive. Report RuleID, file:line, class — never the value. When a truncated/old key can't be matched to a live one, tell Andrew to check the provider console rather than guessing.
3. **Scrub the working tree** (delegate; verify yourself after). Partition the gitleaks file list into four lanes BEFORE launching anything — each lane has a different right tool (full briefs, env contract and worker prompts: `references/scrub-fanout-recipe.md`):
   - **Env contract first.** Before any script is converted, extract the current literals into `KEY=VALUE` lines with a script (print key names and value lengths only), append them to the live `.env` with a backup, and land a shared helper (`scripts/hermes_env.py` + `scripts/lib/hermes_env.sh`: env first, then parse `$HERMES_HOME/.env`, no sourcing) as a sequential job 0. Converting scripts before the values exist breaks the cron jobs that run them in the window before the gateway picks up the new env; the `.env` fallback in the helper closes that window.
   - **Code with infra literals** (scripts, plugins, scripts embedded in skills/) → one `claude -p` job per ≤6-file group, each told the exact file list, the env key names, the alias contract and the verification grep. Required values use `require(KEY)` / `hermes_require KEY` — never a hardcoded real address as fallback.
   - **Prose with infra literals** → one subagent per skill subtree with the same alias contract; tell them to add one `(real addresses: memory_search "Tailnet")` note per file where a placeholder replaced an operational value.
   - **Prose with only bare home-dir paths** → the orchestrator does it with one deterministic script, not a worker: `<root-home>/` → `~/`, then fix the forms where `~` does not expand — systemd unit keys → `%h/`, double-quoted shell assignments `X="$HOME/.."`  → `"$HOME/.."`, Python `open('~/..')`/`connect(`/`sys.path.insert(` → `os.path.expanduser(...)` (prepend `import os;` in `python3 -c` one-liners). A bare sed leaves copy-pasteable snippets that fail at runtime.
   - Hardcoded IPs/paths → env-var lookups with `os.path.expanduser("~/...")` fallbacks
   - Host-specific scripts that are LIVE cron jobs are env-var'd, not dropped — untracking a running script just leaves it unversioned. Drop only one-off tooling (migration backfills, topology probes); keep runbooks and reword.
   - Real config files → delete + commit `*.example.json` template + gitignore the real one
   - DB files → `git rm`, gitignore `data/*.db`, add `data/README.md` explaining
4. **History purge — severity-tiered:**
   - **Credentials/user-data leaked → delete + recreate, NOT filter-repo.** On an already-public GitHub repo, `filter-repo` + force-push is insufficient: old blobs stay fetchable via `refs/pull/*` and cached SHAs. Only repo deletion clears them.
   - **Prose-only leak (codenames/paths, no secrets) in a repo with NO PR refs → in-place squash is acceptable:** `NEWROOT=$(git commit-tree "origin/master^{tree}" -m "<msg>") && git push --force origin $NEWROOT:master`. Cheaper than recreate; used 2026-07-08 when the dispatcher's root commit still carried VPS-B/VPS-A in the pre-rewrite README. Cached-SHA exposure of prose is low-severity; for anything credential-shaped, recreate anyway.
   - **After ANY history change, immediately run `~/.hermes/scripts/public_repo_leak_audit.sh`** — it caught the codename leak in the "clean" root commit that eyeball verification missed.
   - Backup first: `git clone --mirror . ~/backups/<repo>-full-history-$(date +%Y%m%d).git`
   - **Prose-only leak in a repo that has NEVER been pushed (local-only history): keep the full history locally and publish an orphan branch.** `master` keeps every commit and is never given a remote; `git checkout --orphan public && git add -A && git commit` is what gets pushed as the remote's master. Refresh it later with `git checkout public && git checkout master -- <tracked dirs> && git commit` so the two branches share a tree but not a history. Offer this before `filter-repo --replace-text` across all commits: a content rewrite leaves old commits semantically wrong (scripts referencing paths that no longer exist) for no gain the user can see.
   - When the user picks "public" AND "keep history" together, say the conflict out loud before acting: a public history with banned literals fails the weekly audit on its first run. Offer orphan-root / rewrite / stay-private as one question.
   - Clean branch: `git checkout --orphan clean-main && git add -A && git commit`
   - Verify zero leaks in the new root: `git grep -E '<patterns>' HEAD`
   - **Deleting a branch is NOT a purge.** GitHub keeps serving its commits by SHA (commit page + `raw.githubusercontent.com/<o>/<r>/<sha>/<file>` both return 200 after `push --delete`). Verify with `curl -o /dev/null -w '%{http_code}'`; only repo delete+recreate (or a GitHub Support cache purge) makes them 404.
   - **Before Andrew deletes ANY repo, `git clone --mirror` every repo slated for deletion** into `~/reports/archive/github-mirrors-<date>/` and check which have another complete copy (`rev-list --all --count` vs fleet clones). GitHub is often the only full copy: fleet hosts carry stale clones or none.
   - **Inventory what recreate loses** and tell Andrew first: Actions run history, branch protection (re-apply after first push), webhooks + Actions secrets/variables (the stored token 403s on these — ask him to check Settings), issues/PRs/releases/Pages/collaborators (`/hooks`, `/keys`, `/actions/secrets`, `/environments`, `/releases`, `/issues?state=all`, `/collaborators`). Workflow files come back with the push.
   - Repo delete needs the web UI (Settings → Danger Zone) unless token has `delete_repo` scope — Andrew's stored token does NOT. Ask him to recreate it EMPTY (no README/.gitignore/license) so the first push is clean.
   - Recreate via API `POST /user/repos`, push `clean-main:main`
5. **Publish from the clean branch** (sequential, each gated call on its own): land CI files on the KEEP branch first, then cut the orphan branch, then push. Checklist in `references/scrub-fanout-recipe.md` §5.
   - CI files = `.github/workflows/gitleaks.yml` (branch filter = the REMOTE's default branch, not the local branch name), `.github/dependabot.yml` (only ecosystems with a tracked manifest — `pip` with no requirements file just logs errors), repo-level `.gitleaks.toml`. An allow-list `.gitignore` needs `!.github/`, `!.github/**`, `!.gitleaks.toml` or they never get tracked.
   - Run the repo-level config on the export exactly as CI will (`cd <export> && gitleaks dir . --config .gitleaks.toml`) BEFORE the orphan commit: a generic `root-deploy-path` rule fires on every prose path the strict suffix list missed, so sweep `/<home>/` → `~/` across the WHOLE tracked tree (same script, same `%h`/`$HOME`/`expanduser` post-fixes) first; documentation placeholders (`Bearer sk-xxxx…`) trip `custom-bearer-token` and get an allowlist regex, not an edit.
   - Run every test tree under the published dirs (`scripts/tests`, `scripts/knowledge/tests`, `plugins/*/tests`), not just the script suite: a global path sweep breaks tests that match `Path.home()` (fix the test to build its string from `Path.home()`, never from `~`).
   - The orphan cut is a gated git write: run `git checkout --orphan public && git add -A && git commit` as its own terminal call with nothing destructive in front of it. A defensive `git branch -D` prefix makes the approval prompt about the deletion, and an unanswered prompt is a refusal — the branch needn't exist to be "cleaned up".
6. **Verify LIVE** — raw.githubusercontent + `api.github.com/repos/.../contents/... -H 'Accept: application/vnd.github.raw'` (API is authoritative; raw CDN caches ~5 min). Confirm old commit SHAs 404.
7. **Independent read after publish.** The audit only catches literals already in the strict config, so a human-shaped review of the published tree (a reviewer or a `claude -p` with no access to the config, told to look for employer/person identifiers, credential-handling scripts and runbooks that describe access posture) is the only check for the categories nobody listed yet. Treat its findings as blockers, not suggestions: fix, add every real miss to the strict config, re-cut, re-audit, then keep `public-prev-<date>` as the rollback.

## Prevention stack (installed 2026-07-08)

| Layer | Where |
|---|---|
| gitleaks binary | `/usr/local/bin/gitleaks` |
| Strict config (real banned literals — NEVER commit this file) | `~/.config/gitleaks/gitleaks.toml` |
| Global pre-commit hook template | `~/.config/git-template/hooks/pre-commit` via `git config --global init.templateDir` |
| CI workflow + sanitized repo-level `.gitleaks.toml` | each repo's `.github/workflows/gitleaks.yml` |
| GitHub secret scanning + push protection | enabled via `PATCH /repos/{o}/{r}` `security_and_analysis` |
| Weekly audit cron (silent when clean) | job `2fca0b844232`, script `~/.hermes/scripts/public_repo_leak_audit.sh`, Mondays 08:00 → TG cron channel |

## Pitfalls

- **templateDir only applies to NEW clones/inits** — retrofit existing repos: `cp ~/.config/git-template/hooks/pre-commit <repo>/.git/hooks/`
- **Repo-level `.gitleaks.toml` must not contain the banned literals** (it's public!). Use generic regexes there (CGNAT range `\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.`, `[a-z0-9-]+\.[a-z0-9-]+\.ts\.net` with `<[a-z-]+>\.ts\.net` allowlisted, absolute home-dir path `/<home>/[A-Za-z0-9._-]+`); allowlist the manifest checksum by path; keep real IPs/domains only in the local config.
- **Hook bypass:** `SKIP_GITLEAKS=1 git commit ...` — deliberate use only.
- **False positives seen:** public Apify actor slugs trip `generic-api-key`; allowlist in the local config or annotate `// gitleaks:allow`.
- **uvicorn/dispatcher restart:** the hash file (`.dashboard_passwd_hash`) is read at import time — after rotating, kill ALL uvicorn PIDs (`pgrep -f 'uvicorn server:app'`), not just one; a surviving worker keeps the old hash in memory.
- **`gh` CLI is not installed on LH01** — use the token from `~/.git-credentials` with curl, extracted via a python3 heredoc (bash `$(python3 -c ...)` with regex parens breaks quoting; use `<<'EOF'` heredoc form in a script file).
- **Git identity:** Andrew wants published history under his own name — `andrewalhaj <77587948+andrewalhaj@users.noreply.github.com>` on repos he owns; `hermes-coder / coder@hermes` only for throwaway clones.
- New repos: born private, flipped public only after this workflow. `.gitignore` gets `.env*`, `*.db`, `*.bak*` at scaffold time.
- **Post-scrub README rewrites can re-introduce leaks:** a README worker documented the deleted `hermes-agent` branch (the password-leak branch) because the local clone still had it. After any scrub, re-run the leak audit on final pushed content, and check docs don't reference deleted branches/artifacts.
- **Untrack (`git rm --cached`) on the branch you will keep, never on a feature branch that gets merged.** Merging a commit that deletes tracked files deletes them from the working tree too — the "stays on disk" guarantee only holds for the commit where the untracking happened. Live cron scripts and telemetry vanished this way — and a delegated worker told to "untrack X" on its branch will do exactly this; brief it to add the gitignore line only and let the orchestrator `git rm --cached` on master. Recover with `git restore --source=<pre-untrack-commit> --worktree -- <paths>` or `git show <sha>:<path> > <path>` (ignored afterwards, so `git status` stays clean) — check the `test_<name>.py` twin too.
- **The first commit after a scrub is the proof the pre-commit gitleaks hook works again**: commit WITHOUT `SKIP_GITLEAKS=1`; if it passes, every later skills commit can drop the bypass.
- **Never alter legitimate data to pass a scan.** A zero-hit rule pushes workers to rewrite real values that merely contain a banned substring (a font name, a place name). Use word-boundary patterns, whitelist the specific hit with a reason, and diff data files against the source after any delegated scrub. Data files whose *content* is the leak (a KB taxonomy naming hosts, retrieval pickles) are untracked and gitignored, not aliased.
- **The scrub tooling is itself a leak vector.** The local leak-audit script carries the real banned literals (patterns live in the local gitleaks config, not in this skill); before the repo that contains it goes public, verify the script reads its patterns from the local config rather than embedding them inline. Untracking the strict config is not enough if the script re-embeds the literals.
- **Delegated scrub workers self-report clean — verify yourself.** Every fan-out this workflow uses ended with the parent re-running the banned-pattern grep; one worker missed a `~/.hermes` path literal (in its bare absolute form) in hooks.py and another wrote identical old/new patch strings (no-op flagged by the mutation verifier).
- **Hermes cron `script` param must be a bare filename** relative to `~/.hermes/scripts/` — absolute paths are rejected at creation.
- **The strict `root-deploy-path` rule only matches a fixed list of bare home-dir suffixes** — after the gitleaks pass, grep the tracked tree for any remaining absolute home paths (and `[0-9]{9,}` for chat IDs) too; guard path lists, default output paths and test fixtures hide behind unlisted suffixes.
- **Untrack generated telemetry before scrubbing** (`skills/.curator_ledger.jsonl`, `.curator_state`, `.usage.json`, `skills/.hub/` cache+lock+audit log): they re-embed absolute home paths on every regeneration, so aliasing them is wasted work.
- **`gitleaks dir` on an export skips symlinks, the pre-commit hook on the orphan commit does not.** Tracked symlinks (`git ls-files -s | grep ^120000`) with absolute targets leak the home path AND dangle for every cloner — untrack them. Likewise the commit author email: `hermes@<hostname>.local` publishes the machine name; set an `@users.noreply.github.com` identity in the repo before the orphan commit.
- **The orphan commit's pre-commit hook scans the WHOLE tree**, so the manifest-checksum false positive trips it: run `git diff --cached | gitleaks stdin --config <strict>` first, confirm the only hit is the allowlisted path, then `SKIP_GITLEAKS=1`. Add that path to the strict local config's `[allowlist].paths` too or the weekly leak-audit cron reports it forever.
- **Triage an external reviewer's leak list against the strict config before acting, and push back on category errors.** Approved public aliases (LH01/LH02, VPS-A/B) and project/OSS names (`govee`, `ha-fusion`, a deleted app's name) are not identifiers; real misses (lowercase codenames in code, a `HOST-\d+` regex, a work hostname) are. Fix the real third, say which two-thirds were aliases.
- **Keep the previous public root as a local branch (`public-prev-<date>`) before re-cutting** `git commit-tree 'master^{tree}'` + `git branch -f public` + `push --force public:main` — it is the one-command rollback (`push --force origin public-prev-<date>:main`) and costs nothing.
- **Prune deleted repos from the weekly audit list** (`public_repo_leak_audit.sh` REPOS line) — a 404 repo prints a FINDINGS header every week and trains everyone to ignore the alert.
- **Numeric chat/user ids are leaks for a public tree** (Telegram 10-digit user/chat ids, 18-digit Discord snowflakes) — the `[0-9]{9,}` grep finds them; replace with `<telegram-chat-id>`-style placeholders, leave obvious `1234567890` examples alone.
