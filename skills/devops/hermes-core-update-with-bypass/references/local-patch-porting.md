# Porting git-tracked core patches across an update jump

> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

Applies when `/usr/local/lib/hermes-agent` carries ` M` tracked-file edits (§0b
UNPROTECTED files) and `hermes update` moves HEAD by hundreds of commits. The
update autostashes those edits (`hermes-update-autostash-<ts>`); on a large jump
the stash pop conflicts on every file. The failure that must never recur: a runner
that restarts the gateway on a tree containing conflict markers → `SyntaxError`
crash loop in `hermes_cli/main.py` until the next `git reset`.

## Hard rules (every port)

1. **Never restart the gateway on a tree you have not compile-gated.** Gate in this
   order, non-zero exit stops BEFORE `systemctl`: `git diff --check` (markers) →
   `python -m py_compile` on every changed file → import smoke of the entry modules
   → targeted pytest. On failure revert to the known-good tag WITHOUT restarting —
   the running gateway is still healthy; only a restart would kill it.
1a. **Run the compile/import gate on the interpreter the gateway unit actually
   executes — never on `/usr/local/lib/hermes-agent/venv` by default.** Since the
   payload-launcher layout, the unit's `ExecStart` is the `.hermes/bin/hermes` shim,
   which runs a pinned `~/.hermes/tools/python-3.x/` interpreter against a
   lock-managed venv under `~/.hermes/installs/<hash>/environments/<hash>/venv`;
   the repo-root `venv/` is the old dev env (has pytest and whatever was hand-installed
   there). A module that imports clean on the dev venv can be a `ModuleNotFoundError`
   crash loop on the runtime env. Resolve the runtime interpreter from the live
   process, not from a path you remember:
   `grep -oE '/[^ ]*site-packages' /proc/$(systemctl --user show -p MainPID --value hermes-gateway.service)/maps | sort -u`
   → `<…>/venv/bin/python3`. Use it for py_compile + import smoke; keep pytest on the
   dev venv (runtime env ships no pytest). Point the runner's `hermes` checks at the
   shim, not `venv/bin/hermes`. The shim inserts the repo root on `sys.path`, so new
   top-level modules import without an editable reinstall.
1b. **Every runtime-venv probe: `export HERMES_DISABLE_LAZY_INSTALLS=1`, `cd` into
   the repo, pass code with `-c` (never a stdin heredoc).** Three independent traps
   on the venv interpreter: (a) its `.pth` bootstrap calls `prepare_launch`, which
   re-execs any non-store interpreter into the bare store python with `-I` and no
   dependency env → `ModuleNotFoundError: ruamel` on a tree that is actually fine;
   the flag makes it return early and run in-process. (b) that re-exec replays a
   stdin `-` argv as `run_path('<repo>/-')` → ENOENT. (c) from any cwd outside the
   repo, `import pm`/`hermes_cli` resolve to the env generation's build-time
   snapshot at `environments/<gen>/workspace/` — stale source from the last lock
   sync, so probes pass/fail against the wrong tree. The gateway itself is immune
   (its launcher does `sys.path.insert(0, <repo>)`), only out-of-tree probes drift.
   Also fix the runner's `VENV_PY` fallback: under a system-scope `systemd-run`,
   `systemctl --user` fails, the pid lookup returns empty, and the interpreter
   silently collapses to `/bin/python3`; require `-c 'import sys, ruamel.yaml;
   assert sys.version_info[:2]==(3,14)'` before accepting any candidate.
1c. **The only apply operation is `git merge --ff-only <branch>`; `git apply --3way`
   is banned as a runner step.** `--check` failing then falling back to `--3way`
   is exactly how the 2026-10-13 crash loop was manufactured: 3-way "succeeds"
   by writing conflict markers, and markers at the top of a file silently turn the
   module docstring into code. If ff-only refuses, the answer is a new port job in a
   worktree, never a resolution in the live tree.
1d. **ff-only also refuses on untracked files the branch introduces.** Leftovers from
   a crash-loop recovery (e.g. a new module copied in by hand) make `git merge`
   abort with "untracked working tree files would be overwritten". Before the
   switch: `git status --short` must be empty INCLUDING `??` lines for any path the
   branch adds; `git clean -n` to list, then remove them (they exist in the branch).
2. **Pin the known-good commit as a tag first** (`rollback/known-good-<ts>`), plus a
   tag for every reflog-only commit you depend on — reflog entries expire; tags don't.
3. **Live tree stays untouched until a reviewed merged branch exists.** Port in
   worktrees; the user reviews the merged diff before any checkout of the live tree.
4. **Tree switch + gateway restart happen from an outside shell.** The gate blocks
   even `bash -n` on a script that mentions a gateway restart; hand the user the
   launch command (`systemd-run --unit=hermes-retry-$(date +%s) --collect /bin/bash <runner>`).
   **Before handing over (or re-handing) that command, `ls -t` the runner log dir and
   tail the newest log** — the user often launches the moment the branch is ready,
   so a "here is the relaunch command" reply after the run already completed reads
   as not having checked. Report the run's result instead.

## Procedure

1. **Extract per-file diffs from the stash** (`git stash show -p stash@{0} -- <f>`)
   into `patches/<name>.diff` so each port job has an exact spec, not a memory.
2. **One worktree per independent chunk** from the known-good commit:
   `git worktree add run/retry-<ts>/wt-<x> -b local/retry-<x> <known-good>`. Group
   by file ownership so jobs never edit the same file. Put a `BRIEF.md` in each
   worktree (hard rules: stay in the worktree, no `git checkout main`/`reset`/`stash`/
   `worktree`/`tag -d`, no installs, no service control) and add it to
   `.git/info/exclude` so it never lands in a commit.
3. **Check upstream equivalence BEFORE porting each hunk** — grep `origin/main` for the
   behavior. A hunk upstream already absorbed is OBSOLETE-SKIPPED (example class:
   `pyproject.toml` `py-modules` lists once setup auto-discovers top-level modules).
   Porting it anyway creates a conflict on the next update for nothing.
4. **One `claude -p` job per worktree**, launched as a transient `systemd-run --user`
   unit with MCP disabled and a memory cap (see claude-code skill, pitfalls 17 and
   17.6) — gateway background processes die at turn end, and Claude Code's
   `codebase-memory` MCP server can OOM the box. Poll with `systemctl --user
   list-units 'retry-job-*'` + per-worktree `git log <known-good>..HEAD` /
   `git status --short`; the job's JSON result file is 0 B until it exits.
5. **Run the worktree's tests with a wrapper that strips the live editable finder**
   (filter `sys.path` / `.pth` entries by the exact live path only) so imports
   resolve to the worktree, not `/usr/local/lib`. **Put the run dir (worktrees,
   manifests, logs) OUTSIDE `~/.hermes`** (e.g. `~/run/retry-<ts>/`): the
   upstream `home_io_guard` test fixture treats ANY path under the real hermes home
   as forbidden I/O, so it fails not only unrelated upstream tests but also the
   NEW tests for your own patches (anything touching a manifest/state file in the
   worktree) — and those are exactly the tests the differential cannot excuse,
   forcing a second run from a relocated copy.

   **The pytest gate is a DIFFERENTIAL, never a raw pass/fail.** Hundreds of upstream
   tests fail in this environment regardless of the port (path guard, missing
   out-of-tree packages, no gateway env), so a failure count tells you nothing.
   Recipe:
   - Add a vanilla worktree at the same known-good commit
     (`git worktree add <run>/wt-base <known-good> --detach`) beside the merged one.
   - Run the SAME chunks on both with named-failure output:
     `pytest -q -p no:cacheprovider --tb=no -rfE <chunk>` → one log per
     (worktree, chunk), one `exit=` line per run appended to a summary log, and a
     `DONE` sentinel at the end so polling is a `tail`, not a guess.
   - Chunk the run (tests touched by the port first, then the owning packages — e.g.
     `tests/gateway/ --ignore=tests/gateway/relay`), never the whole suite in one
     process: the full suite under a cgroup memory cap gets OOM-killed mid-run at
     ~50% and leaves no verdict. Launch as `systemd-run --user --collect -p MemoryMax=12G`
     so a runaway chunk dies instead of the box.
   - Verdict = set difference of `FAILED` lines: present only in the patched
     worktree → ours, fix before merge review; present in both → environment, ignore.
     Score it in code per batch, not by eyeballing counts (equal totals can hide a
     swap of one failure for another):
     `comm -23 <(grep '^FAILED' merge-N.log|sort) <(grep '^FAILED' base-N.log|sort) | grep -c .`
     → `newfail=0` for every batch is the merge verdict; report progress to the user
     as `done/total batches, newfail so far` while the unit is still `active`.
   - `-p no:cacheprovider` is mandatory — the `.pytest_cache` write otherwise dirties
     the worktree and shows up in `git status` as a phantom port change.
6. **Merge** `local/retry-*` → `local/patches-<ts>`, run the full gate (rule 1), show
   the user `git diff --stat <known-good>..local/patches-<ts>`, then hand off the
   runner (rule 4).

## Port-specific pitfalls

- **Any third-party import a patch adds (Sentry SDK etc.) must be import-guarded**
  (`try: import x / except ImportError: x = None` + skip the init with one logged
  warning). The runtime venv is rebuilt from `pyproject` extras + `uv.lock`; a package
  hand-installed into it (`uv pip install --python <runtime-py> pkg`, a gated
  install) is gone on the next environment generation — `expected_stamp()` hashes
  only pyproject+lock+extras and never sees it. Guard first so a missing package
  degrades the feature instead of killing startup; make the dependency durable
  afterwards as a separate, smaller change (recipe below) — never in the same
  commit as the first risky apply, because a lock change forces a venv rebuild
  on top of everything else.

## Making a patch's dependency durable (pyproject extra + lock)

1. In the branch worktree: add `name = ["pkg==X.Y.Z"]` under
   `[project.optional-dependencies]` with a comment saying why it is not
   lazy-installed (module-level init runs before the agent loop exists), and add
   `"hermes-agent[name]"` to the `all` extra — `enabled_extras()` on this host
   includes `all`, so that is what puts it in the runtime venv.
2. Relock without touching any env: `PYTHONPATH=$WT <runtime-py> -m pm.build_env
   --source $WT --lock-only` (flag + cwd rules from 1b). `pm lock` is the same
   path with the live repo hard-wired as source, so use the module on the worktree.
3. **Verify the lock delta is ONLY the intended package** before committing —
   `git diff uv.lock | grep '^[+-]' | grep -v '^[+-][+-]'`; expect the new
   `[[package]]` block, its `provides-extras` entry, and the two `all`/extra
   references. Anything else means the relock pulled unrelated upgrades: revert and
   investigate (`exclude-newer` in pyproject governs what the resolver may see).
4. Confirm the stamp mechanism sees the change: `pm.venv_is_current(project_root=
   Path(live))` → True, `project_root=Path($WT)` → False. If both are True the extra
   is not in the enabled set and the package will not land. **Always pass
   `project_root` explicitly** — the default `repo_root()` resolves to the env
   generation's `workspace/` snapshot (rule 1b-c), so a bare call returns False
   against the wrong tree and looks like a stale lock.
5. Apply through the normal gate/runner, but the runner's "pyproject changed" step
   must run **`$HERMES pm install`** — not `uv pip install --no-deps -e`, which only
   refreshes the editable stub and cannot carry a lock change. Explicit `pm install`
   bypasses the lazy-install policy (`explicit=True`) and `pm` argv skips
   `prepare_launch`, so it works under the flag from 1b. Trigger it on
   `git diff --quiet <known-good> HEAD -- pyproject.toml uv.lock` (both files).
6. The sync publishes a NEW environment generation, so the interpreter path you
   resolved at runner start is the old one. Re-resolve after the sync:
   `venv_python(selected_venv(Path(repo)))` from `pm.environments`, re-run the 3.14
   /ruamel acceptance, assert `pm.venv_is_current(project_root=repo)`, then import
   the entry modules **plus the new package** before the restart step.
7. If a later gate aborts after the sync, the tree reverts to known-good but the
   venv stays rebuilt — harmless (gateway untouched; next launch resyncs), so do
   not add an "undo the venv" step.

## Verifying a module-level SDK actually initialised in the live gateway

`/proc/<pid>/maps` only lists mapped `.so` files — pure-Python packages never
appear, so `grep sentry_sdk /proc/<pid>/maps` = 0 proves nothing. Python 3.14
pushes thread names to the OS; an SDK that spawns a transport worker at `init()`
is visible without touching the process:
`for t in /proc/$GW/task/*; do cat $t/comm; done | sort | uniq -c` → e.g.
`sentry-sdk.Back`, `sentry.monitor`. Absent → init did not run (check the guard's
fallback warning in the journal). Do not confuse the SDK with a same-named MCP
server entry in `mcp_servers` — a parked `MCP server 'sentry'` is the npx child
failing, unrelated to error reporting.
- **Read each port job's report for "I did X instead of the original"** before
  merging. Jobs re-interpret intent (e.g. a validation that rejected on ANY missing
  item became reject-only-if-ALL-missing). Decide against the original patch's
  purpose — a safety check that silently passes partial failure is a regression even
  if its tests are green — and send a follow-up job on the merged branch rather than
  editing by hand.

- **Module-level init blocks (Sentry etc.) go AFTER the module docstring.** Inserting
  code before the docstring turns the docstring into a bare expression statement —
  `__doc__` becomes None and any em-dash/non-ASCII in it becomes a `SyntaxError`
  under the wrong encoding path. This was the exact crash-loop trigger.
- **Hooks move between files across big jumps** — the function you patched may have
  been extracted (e.g. the retry-exhausted path moving from `conversation_loop.py`
  to `turn_recovery.py`; gateway startup moving into `run_startup.py`). Grep for
  the behavior, not the old filename, and port to where it lives now.
- **Files under a size limit only shrink** — if a target file is at its pre-commit
  line cap, put the new body in a new module and import it; the hook stays a one-liner.
- **Prefer a new module over editing hot files for whole features** (e.g. a bridge
  server) — it survives the next jump as an untouched file instead of a stash conflict.
- **Half-finished cherry-picks are the usual leftover from a killed job.** On relaunch:
  `git cherry-pick --abort` (or `git checkout -q -- . && git clean -qfd`), then
  relaunch — the new job otherwise inherits staged new files without the matching
  edits to existing files and "finishes" a broken port.
