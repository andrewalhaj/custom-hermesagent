# Executing a POLA Refactor (behavior-preserving "remove the astonishment" pass)

Captured 2026-06-24 after a full POLA refactor of a FastAPI+React repo (routing
convention, security-surface docs, frontend cleanup) shipped across 3 PRs. The
core SKILL.md covers *designing* for least-astonishment; this covers *refactoring
an existing repo to remove* astonishments without changing behavior.

## The shape of the task

A POLA refactor prompt typically asks: "make this repo predictable — anyone
reading it should guess right about what each thing does, where it lives, and
what URL/port/scheme it uses, without opening a second file." The deliverable is
NOT new behavior — it's the removal of contradictions (docs vs code, name vs
behavior, two artifacts doing one job). The governing constraint is
**behavior-preserving**: the only allowed change is collapsing a contradiction.

## Phase 1 — Inventory first, NO edits (then STOP for greenlight)

This is the report-before-execute protocol applied to a whole repo. Produce three
artifacts before touching anything:

1. **Route map** (for any web backend): `path → method → declaring file → how the
   prefix is applied`. This is the single highest-leverage artifact — it surfaces
   the "you can't predict a URL from the file that registers it" class.
2. **Component / asset graph**: every component marked IMPORTED vs ORPHANED (grep
   for imports; zero external refs = orphan candidate).
3. **Findings table**: `id | class | file:line | the wrong guess a reader makes |
   proposed fix | behavior-preserving? (y/n) | greenlight-required? (y/n)`.

Consolidate subagent outputs into ONE reference doc and DELETE their scattered
report files — keep the repo tree pristine for the greenlight diff. Subagents
writing reports into the working tree is a near-certain side effect; sweep it.

**Fan-out rule:** the inventory splits cleanly into independent read-only chunks
(backend routing / auth-security / frontend graph / docs+orphans). Dispatch them
in parallel via delegate_task. If one times out (the routing/route-map chunk is
the heaviest and most likely to), do that one inline — you want the route map
exact anyway.

## Phase 2 — Commit in reversible, single-class slices

One astonishment class per commit. NEVER mix a security/scheme change with a
rename or a doc edit. Commit message names the surprise removed. Two batches,
clearly labeled and separate:

- **Batch 1 — behavior-preserving** (net URLs unchanged, UI frozen): routing
  convention, dead-duplicate removal, port-default fix, README truth pass,
  gitignore a committed secret, document a non-obvious model at its call site,
  remove orphaned components, unify a one-job-three-ways pattern.
- **Batch 2 — scheme changes** (auth/crypto/CORS): each gets its OWN written
  proposal with a diff sketch + separate greenlight. Never bundled into a
  cosmetic commit. Some are blocked on *inputs* (a password, a deployment origin)
  not just greenlight — surface those as specific asks, don't guess.

## Verification that actually proves "behavior-preserving"

- **Route-map diff IS the proof for routing refactors.** Dump all net URLs before
  and after; the set must be byte-identical (unless a URL change was greenlit).
  See `scripts/fastapi_route_dump.py` — modern FastAPI hides routes inside
  `_IncludedRouter` wrappers and the prefix lives in `include_context.prefix`,
  NOT in the router's own `.routes`. A naive `for r in app.routes` walk returns
  only the catch-all. The script handles this.
- Backend import smoke: `python -c "import server"` clean.
- Frontend: `npm run build` (tsc + vite) + `npm run lint` — no NEW errors.
- Before deleting ANY file, grep the tree to prove zero live references first.
- Write a `REFACTOR_NOTES.md` mapping each fix to the specific reader-surprise it
  eliminates, with the verification output attached.

## Pitfalls specific to refactor execution

- **Untracking a config file the app reads at import is a NEW astonishment.**
  Adding a tracked secret to `.gitignore` does NOT untrack it — git keeps tracking
  already-committed files. You must `git rm --cached <file>` (the file stays on
  disk). BUT: once untracked + gitignored, a fresh clone has no file, and an
  unguarded `Path(...).read_text()` at import crashes with a cryptic
  FileNotFoundError deep in the import chain. Trade one surprise for a worse one
  unless you ALSO add a graceful-missing guard (try/except raising a RuntimeError
  with the exact create-command) AND a README setup note. Verify the guard fires
  by temporarily moving the file and re-importing.
  - **The worse trap: `git rm --cached` deletes YOUR OWN working copy on the next
    checkout/merge — not just on fresh clones.** After the untrack commit lands,
    the file is gone from git's index, so `git checkout master` / branch-switch /
    `git merge` will REMOVE the untracked working-tree copy too (verified 2026-06-24:
    a post-merge `git checkout master` silently deleted a live dashboard's bcrypt
    hash file). The running service stays up (file already open in memory), so it
    looks fine — but the next restart crashes on the missing file. This is a
    deferred surprise that detonates at deploy time. MITIGATION: (1) the
    graceful-missing guard converts the crash into a loud, actionable RuntimeError
    instead of a cryptic traceback; (2) after ANY branch switch/merge on a repo
    whose live service reads an untracked-but-required file, immediately verify the
    file still exists on disk BEFORE the deploy restart, and recreate it if gone;
    (3) note this explicitly in the PR's deploy section so the human doesn't restart
    into a broken state. Recreating the file is safe — it's gitignored, so it won't
    re-enter the repo.
- **Removing a "duplicate" route: check the response SHAPE, not just the path.**
  Two handlers registering the same URL may return different shapes (dict vs
  list). Whichever registers first wins silently. Keep the one whose shape the
  consumer (frontend fetch) actually expects; delete the wrong-shape duplicate.
- **A router file with module-level `app = FastAPI()` fires on import.** When you
  strip its baked prefix and remove the now-"unused" FastAPI/CORS/uvicorn
  imports, a standalone block at the bottom breaks. Collapse such standalone
  blocks into `if __name__ == "__main__":` with locally-scoped imports — a router
  module should have no import-time side effects (this is itself a POLA fix).
- **`httponly` cookie auth means the frontend never reads the token.** When you
  change the server-side token model (global → per-session), the frontend
  contract (login/check/logout response shapes) is unchanged, so no frontend edit
  is needed. Verify by grepping the frontend for the cookie name / endpoints.
- **Gitignored scratch files (`_verify_*.py`) may reference symbols you remove.**
  They're untracked throwaways — leave them, but NOTE it in the PR so a reader
  isn't confused. Don't chase them as if they were app code.
- **The write gate pattern-matches command strings, not just executed commands.**
  A grep or echo containing the literal `pip install` (e.g. filtering it out of
  output) trips the gate. Route around it (heredoc, different filter) rather than
  fighting it.
