# Dependabot / dependency-bump PRs

How to "address the open Dependabot PRs" on a repo whose CI only runs a secret scan. CI green
proves nothing about the bump; the verification is local, and the deploy step is not optional.

## Procedure

1. Inventory: `GET /pulls?state=open` → number, head ref, title (old→new), ecosystem. Flag MAJOR bumps
   and anything whose first-level consumer is a security boundary (auth, crypto, error reporting).
2. Verify in a scratch COPY, never the live checkout (fan out per ecosystem with delegate_task):
   - pip: copy the repo to `cache/scratch/`, fresh venv, install `requirements.txt` with the bumped
     pins applied, run the full suite; baseline the suite on the original venv first.
   - npm: `rsync -a --exclude node_modules app/ cache/scratch/<copy>/`, `npm ci`, baseline build+lint,
     then `npm install <pkg>@<exact>` for each bump, build+lint again, compare counts.
   - Verdict per PR: safe / safe-with-fix / unsafe, plus the reason. Delegates self-report — re-run the
     one assertion that matters yourself (the build, the failing test, `npm ls`).
3. For every MAJOR bump read the vendor migration guide before merging — the breaking change is
   usually a DEFAULT behaviour change that compiles clean and shows up as 500s or data exposure
   (see table below), not a type error.
4. Peer-range check for npm majors: after the bump run `npm ls <pkg> 2>&1 | grep invalid`. A bumped
   package whose peer range excludes a locked sibling (plugin needs Vite 8, lint plugin stops at
   eslint 9) still builds on the existing `node_modules` but makes `npm ci` on a fresh clone fail
   with ERESOLVE. That is a revert/close, not a merge, plus a Dependabot `ignore` for that major.
5. Fixes the bumps need go in ONE `fix/` PR merged BEFORE the bumps (guards, config the new major
   needs). When the fix only exists in the new major's API (an init option), do the bump in that PR
   and close Dependabot's as superseded with a one-line comment saying why.
6. Scan each PR branch locally with the strict config — this is the evidence when CI is queued:
   `gitleaks git . --config ~/.config/gitleaks/gitleaks.toml --log-opts=origin/master..origin/<branch>`.
7. Merge order: your fix PR, then one Dependabot PR per ecosystem, then `@dependabot rebase` the rest
   (comment on the PR), poll `GET /pulls/N` until `mergeable_state` leaves `dirty`/`unknown`, merge
   the next. Lockfile and `requirements.txt` neighbours conflict pairwise; non-overlapping hunks merge
   clean even while `unstable` (checks pending).
8. Dependabot refills its `open-pull-requests-limit` within seconds of each merge with transitive
   lock-only bumps — handle the new ones the same way, then STOP and say the queue refills weekly;
   do not chase it indefinitely.
9. Deploy — merging `requirements.txt`/`package.json` installs nothing:
   `.venv/bin/pip install -r requirements.txt` (gated), `npm ci && npm run build` for an ignored
   `dist/`, restart the service, hit the health endpoint, and exercise the one path the bump touched
   (e.g. the login route after a bcrypt bump). Then `git pull --ff-only` the live checkout.
10. Report: merged list, closed/superseded with reasons, what was deployed, pre-existing debt noticed
    (lint baseline) as a separate item, and whether CI was green or local-scan-only.

## Authorisation

"Address all the PRs" is the same authorisation as "merge anything remaining": merge via the API for
that request only, including the small `fix/`/`chore/` PRs the bumps require, and say in the report
that those were self-merged with their rollback (revert the merge commit).

## Known default-behaviour changes in majors

| Package | Change | Fix |
|---|---|---|
| bcrypt 5.x | `checkpw`/`hashpw` raise `ValueError` on input > 72 bytes instead of truncating → a long password turns a 401 into a 500 | `len(pw.encode()) <= 72 and bcrypt.checkpw(...)` on the login path; add the 73-byte test |
| @sentry/react 11 | `sendDefaultPii` replaced by `dataCollection`; unset = collects cookies, headers, REQUEST BODIES, user info by default (v10 unset was restrictive) | if any POST body carries a credential (login form), pin the v10 baseline from the migration guide (`userInfo:false, cookies:false, httpBodies:[], ...`); the option only type-checks on v11, so bump and config go in the same commit |
| gitleaks-action 3 | Node 20 → 24 runtime only, no input/behaviour change | plain bump |
| actions/checkout 7 | Node 24 runtime | plain bump |
| @vitejs/plugin-react 6 | peer `vite ^8` | needs the Vite major first; ignore `version-update:semver-major` in dependabot.yml until then |
| eslint 10 | eslint-plugin-react-hooks 5.x peer range stops at 9 | same: ignore the major until the plugin supports it |

Dependabot ignore block (npm ecosystem entry):

```yaml
    ignore:
      - dependency-name: "@vitejs/plugin-react"
        update-types: ["version-update:semver-major"]
```
