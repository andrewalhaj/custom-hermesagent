<!-- Copy to ~/.hermes/cache/scratch/card-<topic>.md, fill the <slots>, pass via --body-file.
     Order matters: boundaries first (the worker reads top-down and starts acting early). -->

<One-paragraph scope: what this card fixes/builds, where the spec lives (absolute path to the review/plan file), what is OUT of scope. Name items already done elsewhere so the worker skips them.>

## Repo / workspace (IMPORTANT)
- Repo: `<abs path>` (branch `<base>`, remote `<origin/name>`). <If this is a LIVE config/service dir: say so and forbid editing it in place.>
- Work in a git worktree on a feature branch (`feat/<topic>`), same pattern as `git -C <repo> worktree list`:
  `git -C <repo> worktree add -b feat/<topic> <repo>/cache/scratch/wt-<topic> <base>` — run the coding tool with `workdir=` that path.
- Pre-existing dirty files in the live tree that are NOT yours: `<list from git status>`. Leave them alone; stage only your files by path.
- Commit on `feat/<topic>` ONLY. <Do NOT merge / do NOT push / do NOT touch `<protected branch>`> — or, if pushing is wanted: push the branch, `gh pr create --base <base>`, never merge.
- Never print or copy secrets: `.env`, `config.yaml`, `*credentials*` are out of scope. Tests mock external clients; never run against the live store.

## The changes
1. `<file>` (~line N) — <problem in one sentence: mechanism, not symptom>. Fix: <concrete change>. Test: <new/extended test file + what it asserts>.
2. …

## Verification (must actually run — paste the real output in your completion)
```
<exact test command(s) with the right interpreter; note known quirks, e.g. run files one at a time if a combined run exits silently>
```
Baseline before this card: <N passing>. All must stay green plus the new tests. Compile-check every touched file.

## Done =
- One commit on `feat/<topic>`, conventional message, e.g. `fix(<area>): <summary>`.
- `kanban_complete` metadata: `commit_sha`, `branch`, `changed_files`, `tests_run`, `pushed: true|false` (+ `pr_url` only if real).
- Anything ambiguous or a test that cannot go green → `kanban_block` with the exact error. Do not widen scope, do not fabricate output.
