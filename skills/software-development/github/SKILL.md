---
name: github
description: "Use when working GitHub via gh: PRs, issues, repos, auth"
version: 2.0.0
author: Ben Barclay (benbarclay), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [github, gh, git, pull-requests, issues, code-review, repos, auth, ci]
    category: software-development
    related_skills: [codebase-inspection, requesting-code-review]
---

# GitHub

Work GitHub end to end with the `gh` CLI (REST fallback where noted): auth,
issues, the PR lifecycle, issue-to-PR delivery, code review, and repo
management. This skill consolidates six former skills; each workflow lives
complete in its reference file — ALWAYS read the matching reference before
starting that workflow, the body below only routes.

## Routing

| Task | Read first |
|---|---|
| Auth broken / new machine / token or SSH setup / gh login | `references/auth.md` |
| Create, triage, label, assign, close issues | `references/issues.md` |
| Branch, commit, open PR, watch CI, merge | `references/pr-workflow.md` |
| Carry an ISSUE to a verified PR (full delivery loop) | `references/issue-to-pr.md` |
| Review someone's PR: diffs, inline comments, verdict | `references/code-review.md` |
| Clone/create/fork repos, remotes, releases, free-plan hardening, rewording history | `references/repo-management.md` |
| Dependabot / dependency-bump PRs: verify, merge order, deploy after merge | `references/dependency-bumps.md` |

Supporting assets: `scripts/gh-env.sh` + `scripts/git-credential-token.py`
(auth helpers), `templates/` (PR bodies, bug report, feature request),
`references/ci-troubleshooting.md`, `references/conventional-commits.md`,
`references/github-api-cheatsheet.md`, `references/review-output-template.md`.

## Core discipline (applies to every workflow)

- Preflight once per session: `gh auth status` — if it fails, go to
  `references/auth.md` before anything else.
- Prefer `gh` over raw REST; drop to `gh api` only for endpoints the
  porcelain lacks (the cheatsheet lists them).
- Never report CI green without checking `gh pr checks` yourself; never
  claim merged without verifying `state,mergedAt`.
- Read full context before writing: `gh issue view --comments` /
  `gh pr view --comments` — decisions live in threads, not titles.
- Sweep for duplicates before creating anything:
  `gh pr list --search` / `gh issue list --search`.

## Andrew's merge policy (decided 2026-10-15)

- Push freely to feature branches (`feat/`, `fix/`, `chore/`, `docs/`); never push to `main`/`master` (branch protection + write gate block it).
- Every change to `master` goes through a PR. **Andrew reviews and merges code PRs.** When one is ready, Telegram him: PR link, what/why, test results, risk level, rollback.
- An explicit "merge anything remaining / clean up the branches" authorises merging via the API for that request only: bring stale branches up to date with master first (merge master in, resolve, run the suite), then `PUT /pulls/N/merge` with `merge_method: merge`, `commit_title` = the PR title and empty `commit_message` so the merge commit follows the message convention. Poll `GET /pulls/N` until `mergeable` is non-null before the PUT.
- **Exception — docs-only PRs** (only `*.md`/docs files changed, no code/config/workflow files) may be self-merged once CI (gitleaks) is green; mention them in the next update.
- Still an honour system: pushes use Andrew's token, so GitHub cannot tell us apart (0 required approvals). A separate bot account with Write + CODEOWNERS would make it enforced — offered, deferred.
- Kanban coder cards must push a branch and open a PR, not merge locally into master and push.
- **Merge only PR numbers you verified, never "everything open".** Dependabot refills its 5-slot queue within seconds of each merge, so a script that lists open PRs and merges them will merge majors nobody tested (this merged Vite 8 + framer-motion 14 + react-hooks 7 unverified once). Pass explicit numbers; re-list after merging only to see what is NEW.
- **Dependabot bumps: verify in a scratch copy before merging** — CI only runs gitleaks. pip: install the bumped pins in a fresh venv + full pytest. npm: `rsync` app/ without node_modules, `npm ci`, apply bumps, then `tsc -b`, `vite build`, `npm run lint` and a second clean `npm ci` (peer-range breaks show up only there: plugin-react↔Vite, eslint-plugin-react-hooks↔eslint). A major with a peer range the tree cannot satisfy gets closed with a comment + a `dependabot.yml` ignore for `version-update:semver-major`, not `--legacy-peer-deps`. Read the library's migration page for every major (Sentry 11 silently widened PII collection; bcrypt 5 raises on >72-byte input).
- After merging dependency PRs the service still runs the old tree: `.venv/bin/pip install -r requirements.txt`, `npm ci && npm run build` in app/, restart, then confirm the served asset hash matches `app/dist/assets`. **Then load the page in the real browser** (browser tool: `#root` has children, `window` error trap empty) — a react/react-dom version split builds and serves fine and throws React #527 on load; tsc, pytest and the hash check all passed on a blank dashboard once. Dependabot bumps `react` and `react-dom` in separate PRs: merging one without the other is the trigger.
- Dependabot rebases a `dirty` PR itself within ~1 min of an `@dependabot rebase` comment; merge same-file PRs one at a time and poll `mergeable_state`.
- Private-repo CI with the default **restricted** workflow token (`GET /actions/permissions/workflow` → `read`): gitleaks-action's pull_request runs fail with `Resource not accessible by integration` because it lists PR commits via the API. Give the job `permissions: {contents: read, pull-requests: read}`; push runs never needed it.
- **Baseline the test suite before any review, refactor, push or repo re-publish.** Failures found in the baseline take priority over the requested work: stop the rest, fix them on a `fix/` branch, then resume. Andrew treats a red baseline as the first task, not a footnote.
- **Write-gated git/pip actions:** arm the gate (`cd ~/.hermes && python3 plugins/local-guards/guards/write_gate.py arm "<note>"` — the script lives under `plugins/local-guards/guards/`, not `scripts/`) in its OWN call, then run the gated command in the NEXT call. Parallel tool calls are gate-checked before the arm lands, so a same-turn arm+command is always blocked. The arm lasts 600s: arm right before the gated batch, not before a long verification loop, and keep gated batches short — a prompt that times out counts as a refusal and the rule is to stop and report, not re-arm and retry.
- **CI stalled by a GitHub incident is not a reason to wait or to skip evidence.** CI here only runs gitleaks, so scan the branch locally with the strict config (`gitleaks git . --log-opts=origin/master..origin/<branch>`) and say "local scan, CI queued" in the report instead of claiming green.
- **Secret masking vs patch anchors:** tool output masks secret-shaped values (`abc123...xyz`); using such a line as a `patch` `old_string` fuzzy-matches and WRITES the masked text into the file. Anchor on neighbouring lines and check `git diff` for the secret line before committing.
- Branch cleanup / deleting merged branches / unrelated-history branches: see `references/repo-management.md` § Branch cleanup. Repo delete+recreate or public-history purge: load `repo-publication-scrub`.

## Commit messages (Andrew's standard)

- When Andrew says he will write the message, stage and stop; propose one in the format below only if he then asks for it. Never commit on his behalf with a placeholder.
- Existing history that violates the format gets reworded only on his explicit ask ("get the other commits updated" counts) — it is a force-push; recipe in `references/repo-management.md` § Rewording history.
- Format: `type: plain-English sentence a human would say` — types `fix`, `feat`, `cleanup`, `docs`, `chore`, `test`. Scope in parentheses only when it adds meaning (`fix(write_gate): ...`).
- The subject says what changed and why in one readable line, like you were telling a teammate. Not a diff summary, no symbol dumps, no `;`-joined clauses, no step/branch ids (`merge prog/10.1 — PDFs inline` and `fix(write_gate): approval rule_key hashes the exact args; prompt shows the target` are the kind he called unreadable).
- Good: `fix: stop the write gate approving a different command than the one shown`, `cleanup: drop the merged prog/* branches`, `chore: let Dependabot watch pip, npm and Actions deps weekly`.
- Merge commits for a PR get the PR's title, not `merge <branch>`.

## This box: no gh, fine-grained PAT, free plan (verified 2026-10-19)

- `gh` is not installed. Use `curl -H "Authorization: Bearer $(cat ~/.hermes/.github-pat)"` so the token never appears in command text; git push uses the same token via `~/.git-credentials` (helper `store`). Both files chmod 600; the GitHub MCP (`scripts/github-mcp.sh`) reads `.github-pat` at spawn, so a rotated token only reaches the MCP after the gateway/CLI restarts.
- Fine-grained PAT permissions are per-capability; `admin:true` on the repo proves nothing about push, and a token scoped to "selected repositories" does not see a repo created after it. Ask Andrew for the full set in ONE message instead of discovering them one 403 at a time: **Contents RW** (push; else 403 "Write access to repository not granted", even `ls-remote` fails), **Workflows RW** (any push containing `.github/workflows/*`; else "refusing to allow a Personal Access Token to create or update workflow"), **Pull requests RW** (open/list PRs), **Actions Read** (list runs), **Checks Read** (`/commits/<sha>/check-runs`), **Administration** (create repo, settings, Dependabot, protection). Editing permissions keeps the token string; only regenerating changes it. Diagnose with `GET /repos/O/R/contents/` → 403 = missing Contents, 404 = OK but repo empty. A 403 "Resource not accessible by personal access token" on any endpoint means a missing permission, not a bug.
- Before asking for permissions, run `bash skills/software-development/github/scripts/pat-permission-probe.sh OWNER REPO` — one table of what the token can do, so the request to Andrew lists every missing permission at once.
- Commit identity on hermes-dispatcher is Andrew himself (`andrewalhaj <77587948+andrewalhaj@users.noreply.github.com>`, set in that repo's `git config`); he wants history under his own name, not a bot. For a new repo ask once which identity to use before the first commit.
- A token pasted into chat lands in the transcript (ingested into memory): install it, verify with `/user`, then tell Andrew it is exposed and that next time he should drop it straight into `~/.hermes/.github-pat` himself.
- Free-plan private repos cannot have branch protection, rulesets, or secret-scanning push protection (403/422 "Upgrade to GitHub Pro or make this repository public"). Available for free: Dependabot alerts (`PUT /vulnerability-alerts`), automated security fixes, merge policy, topics, gitleaks workflow in CI. Say so instead of looping on the API.
- hermes-dispatcher was deleted from GitHub 2026-10-15 and **recreated private 2026-10-19**; master + feat/* pushed, the `hermes-agent` branch (old ~/.hermes snapshot with memory files/IPs) stays local-only on purpose. Scan history before any re-publish: `gitleaks git --log-opts=master` for history, `gitleaks dir` for the tree, then intersect with `git ls-files` — hits in .gitignored `.env*`/`data/` files don't ship.
- A gitleaks hit on a *fake* secret in mock/demo data (dummy `AKIA…`/`sk-…` values in a settings mock) is fixed by deleting the mock, never by allowlisting the pattern in `.gitleaks.toml` — an allowlist teaches the scanner to ignore real keys of that shape. If the value is already in history, purge it (`references/repo-management.md` § 6b) or a full-history CI run fails forever.
- When printing gitleaks findings, print only `RuleID`/`File`/`StartLine` — the `Match` field contains the secret value even when `Secret` is redacted, and the transcript is ingested into memory.
- When the pre-commit hook rejects a commit, see what it matched before bypassing: `git diff --cached | gitleaks stdin --no-banner --config ~/.config/gitleaks/gitleaks.toml --report-format json --report-path cache/scratch/gl.json`, then print `RuleID|StartLine` from the JSON. Only `root-deploy-path` hits (skill prose naming `~/.hermes` paths, generated ledgers) → `SKIP_GITLEAKS=1`; anything else is a real finding. Without `--config` gitleaks loads its default rules and reports nothing, which looks like a clean diff.
- Probes leave state: a throwaway branch/PR opened to test CI is closed and deleted in the same session (`PATCH /pulls/N {state: closed}`, `git push --delete origin <branch>`, `git branch -D`), and an empty "test" commit never goes on master — otherwise the next session inherits an open PR and a diverged local master. CI-trigger questions: `references/ci-troubleshooting.md` § "Workflows never run on push/PR" before any probe.

## Verification

- The workflow's own reference file defines done for that task.
- Cross-cutting: every claim about remote state (CI, merge, release,
  issue state) is backed by a fresh `gh` read, never memory.
