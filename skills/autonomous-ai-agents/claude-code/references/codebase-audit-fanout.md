# Codebase audit fan-out → consolidate → verify → remediate

Use when the user asks to review/audit several codebases (or one large repo + ops dirs) with a
ruleset and then fix what it finds. Four phases; the orchestrator never grinds the audit or the
fixes itself.

## 1. Audit (read-only, one `claude -p` per repo)

- **Read SKILL.md pitfall 17.6 before launching anything.** One wrapper script, one job per
  target — a wrapper that forks all `claude -p` children with `&`+`wait` dies with SIGTERM ~10 s
  after the launching turn ends, every time, with 0-byte reports. Launch each as a transient
  unit, not a Hermes background process: `systemd-run --user --unit=job-<name>-$(date +%H%M%S)
  --collect --quiet -p MemoryMax=4G /bin/bash run_one.sh <name>`; add `--strict-mcp-config
  --mcp-config '{"mcpServers":{}}'` to the claude call (≈300 MB RSS/job instead of 6–10 GB).
  Keep prompts in `$OUT/prompts/<name>.txt` so the launch line never carries gated words.
  Progress: `systemctl --user list-units 'job-*' --all`, `cat $OUT/_status.log`. Shape:
  `bash run_one.sh <name> <dir> <audit|review|scripts|plugins>` →
  `cd $dir && claude -p "$PROMPT" --allowedTools "$TOOLS" > $OUT/$name.md 2> $OUT/$name.err;
  echo "exit=$? $name $(date +%T)" >> $OUT/_status.log`.
- Read-only tool set that still lets it reason over history:
  `Skill,Read,Glob,Grep,LS,Bash(git log:*),Bash(git diff:*),Bash(git status:*),Bash(git show:*),Bash(wc:*),Bash(ls:*),Bash(find:*)`.
  No Edit/Write/Bash(*) — an audit that can write will "fix" things mid-audit.
- Prompt must name the ruleset skill explicitly (`Use the Skill tool to load <plugin>:<audit-skill>
  and follow it exactly`), the ignore list (`.venv, venv, node_modules, dist, __pycache__, *.bak*,
  *.pyc`), and the output contract: ranked fix-first, each finding = what the code does / what goes
  wrong / the fix / what happens if skipped.
- Vary the prompt by target kind: a fork gets a **diff review** (`git diff origin/master` + named
  untracked files, "read the code the diff touches, not just the diff"); a flat ops-script dir is
  told "not a packaged project; treat each script as an entry point"; plugin dirs get one line on
  the hook contract. A generic "audit this repo" prompt on a 1,400-file fork reviews upstream code
  you don't own.
- Progress check without waiting: `cat _status.log; wc -c *.md; pgrep -c -f 'claude -p'`.

## 1b. Trim pass ("what do we not use?") — orchestrator greps, auditor claims are the appendix

When the user asks to cut fat, produce a tiered `TRIM.md` next to the reports. Every row carries
the grep that proves it; nothing is deleted until the tier is greenlit, and each tier becomes one
`feat/trim-N` branch + one commit. Tiers: (1) zero-reference files/deps/modules, (2) dead code
inside live files, (3) routes with no caller, (4) mock-data panels (user decision, not a bug),
(5) heavy deps with one importer. Probes that produce the evidence:

- **Top-level files:** `git grep -l "<name>" -- . ':!*.md' ':!app/dist' ':!*lock.json'` — a file
  named only in docs and in its own loader strings is dead. Confirm the server has no static
  mount for it (grep `StaticFiles`/`FileResponse`); a bundle the server never serves is dead even
  if a README says how to rebuild it.
- **Routes vs frontend:** dump the FastAPI route map (SKILL.md pitfall 19 walker), strip `{params}`,
  substring-match each path against the concatenated `app/src/**/*.ts*`. Then split the
  unreferenced set: `/hooks/*` and `/health` are inbound — **grep `~/.hermes/skills`,
  `scripts`, `cron`, `plugins` for external callers before calling a hook route dead.** Routes
  for a panel the shell never mounts (imports `Placeholder`) are dead with their module.
- **Fixtures and deps:** per `data/*.ts`, count importers excluding itself; per dependency,
  `grep -rl "from '<dep>" app/src | wc -l`. Zero importers = Tier 1; one importer for a large dep
  = Tier 5 question, not a cut.
- **On-disk junk:** `git status --porcelain --ignored` minus `node_modules|.venv|dist|__pycache__`
  — `.bak-*`, `.env.bak-*`, editor dirs. Purge so a future `git add -A` can't pick them up.
- **Stale comments/docstrings** contradicted by the fixes you just merged go in the same tier as
  the code they describe.

## 1c. Restructure audit of an un-versioned ops layer (`~/.hermes` patches/scripts/plugins)

When the target is a home-dir customization layer rather than a repo:

- **Baseline commit first, or there is no rollback.** `git init` the layer with a whitelist
  `.gitignore` (`*` then `!patches/ !plugins/ !scripts/ !skills/ …`; exclude `.venv`, `*.bak*`,
  `*.golden*`, `*.REMOVED*`, `.archive/`, `.env*`). Scan the staged diff for live credential
  shapes before committing; a doc/skill with a plaintext password is a real finding — scrub it to a
  pointer (`<in ~/.hermes/.env>`) and note it for Andrew.
- **The global gitleaks pre-commit hook carries publication rules** (private IPs, tailnet names,
  `<root-home>/…` paths). On a private local-only baseline those are noise: dump findings to JSON
  (`gitleaks git --staged --pre-commit --config <global.toml> --report-format json`), triage by
  `RuleID` — only `generic-api-key` / `personal-secrets`-class hits are real — fix those, then
  commit with `SKIP_GITLEAKS=1`. Don't weaken the global config; it is right for public repos.
- **Partition by directory, one unit each** (guards layer / flat top-level scripts / the live
  pipeline + plugins). The flat scripts dir is the slow one; give it the longest timeout.
- **Callers live outside the layer** — the prompt must list where: `config.yaml`, `cron/jobs.json`,
  `systemctl --user cat`, and the agent core (`/usr/local/lib/hermes-agent`, read-only). Allow
  `Bash(systemctl --user cat:*)` and `Bash(systemctl --user list-units:*)` or "dead" is a guess.
- **Restructure prompt contract** (differs from a trim audit): (1) inventory — file, lines, purpose,
  callers with the grep; (2) dead; (3) bugs/risks file:line; (4) TARGET STRUCTURE — what merges
  into what, packages, shared helpers, projected lines per target file and total before/after;
  (5) MIGRATION PLAN — ordered steps each small enough for one commit + test + review, risk and
  live check per step; (6) TRIM table by tier. A plain "audit" prompt returns findings, not a shape.
- `claude -p` units cannot be steered mid-run — if the ask changes (trim → restructure), stop the
  units, wipe the half-written reports, relaunch with the new contract.
- **Sizing before/after:** count per git ref, not the working tree —
  `git ls-tree -r --name-only <ref> | grep -vE 'dist/|lock|\.(png|svg|woff2?)$'` then
  `git show <ref>:<file> | wc -l` summed by extension; exclude build output and lockfiles or
  the "removed" number is mostly bundle churn.

## 1d. Andrew's refactor standard (apply to every proposed step)

- Short files (~150 lines) in the Ponytail sense — shortest working code; when short and clear
  collide, show both versions and let him pick, don't decide.
- One file, one job, named for the job. Guards register through the public plugin hook API
  (`pre_tool_call` etc.) only — monkeypatching private agent methods breaks silently on every core
  upgrade. One-screen README per package (what, how it loads, how to add one, how to test).
  Tests named for the behavior they prove.
- Delete what hasn't been used; don't abstract until the third copy. Archived ≠ removed: an
  "archived" module still on disk with `.bak` siblings and stale "X chains into this" comments in
  neighbours is a Tier 1 full-excision item (file, backups, comments, tests that import it).
- Scripts vs skills: fixed parameterless steps → a script under `scripts/ops/` (<100 lines,
  named in exactly one skill); anything needing judgment → a skill with one-line commands and
  the pitfall next to the command. Procedure pointers leave MEMORY.md for the owning skill.
- Flag any migration step that renames a file other skills/memory point at; the step includes the
  pointer updates or keeps the name.

## 2. Consolidate

Write `SUMMARY.md` next to the per-repo reports: P0 (exposed now) / P1 (regressions) / Cut (dead
code) / cross-cutting pattern / not audited. Mark each line `[verified]` only after phase 3. The
user reads the ranked list; the per-repo files are the appendix.

## 3. Verify before relaying (the audit is a self-report)

- **Grep the installed dependency for any "X imports a function that doesn't exist" claim** — the
  test suite can pass because a test stub mirrors the missing helper. A stub that exists "so the
  import works in the test env" is a test that cannot detect the helper being absent; flag that
  pattern as its own finding.
- **Cross-check "dead code" and "live cron" claims against the real scheduler** (`cron/jobs.json`,
  systemd units) before ranking. Auditors flag real bugs in scripts nothing runs — those go to
  *Cut* (move to a recoverable dir), never to the fix queue.
- **Confirm exposure for security findings yourself**: read the two functions involved and
  `ss -ltnp` the bind address. Only then say "exposed" and raise the rotate-secrets question.
- Read the top 2–3 findings' code; sample the rest. Don't re-audit.

## 4. Remediate (parallel `delegate_task`, one child per repo)

- Arm the write gate **once** with a note listing every target, long TTL, before dispatch.
- The ruleset hook is usually scoped to coder profiles, not the orchestrator's `delegate_task`
  children — so extract the ruleset body (frontmatter stripped) to a scratch file and open every
  child's context with "FIRST read <rules file> and follow it for every edit."
- Every child context carries: repo path + git state, live/not-live (service unit, port), backup
  dir convention (`~/.hermes/backups/<fix-tag>/`), commit identity, **no push**, verified facts
  from phase 3 ("VERIFIED: helper X is absent in /path"), and the verification it must run
  itself (pytest, build, curl with status codes, `tar -tzf`).
- Gateway-process plugins: child edits + unit-tests with mocks only; **the orchestrator restarts
  the gateway** afterward and proves the hook loaded from the journal.
- For a live-exposed finding, make the child grep access logs for past exploitation and report
  counts — that answers "rotate secrets?" without a second round-trip.
- `output_schema` with `summary, files_changed, tests, live_verification, unverified` so
  self-reports are structured and the unverified list is explicit.

## 5. Unattended multi-step program ("run it to the end, I won't supervise")

When the user hands over a 20+ step migration plan with full autonomy, don't babysit it from the
chat session — context compacts, turns end, and the gateway restart that a step performs kills
this profile. Build a tiny runner and let systemd own it.

- **Measure before deleting what "might not matter".** For a retrieval/graph/cache layer the user
  doubts, run the benchmark with the layer on and off BEFORE removal (same query set, bootstrap
  CI), write the prediction, remove, re-run. Identical MRR within the CI is the proof the user
  accepts; a hunch is not. Do this step by hand — it is the one the rest of the program rests on.
- **Plan file first, then state.** `PROGRAM.md` holds every step with the test it must pass and
  every tier-4 decision you made on the user's behalf (numbered D1…, each reversible by git).
  `state.json` is the machine copy: `{id, status: todo|running|done|failed|retry, attempts}`.
  Lock the order; a failed step gets 3 attempts and then halts the program as BLOCKED — never
  skip ahead and build on a step that didn't land.
- **Runner shape (~200 lines, stdlib only, tested):** load state → gauge check → pick first
  todo/retry → dispatch one `claude -p "[refactor-step <id>] …" --model opus --allowedTools
  <full list> --disallowedTools WebFetch,WebSearch` → capture stdout to `steps/<id>.md` → update
  state → rewrite `STATUS.md` (one table) and append one line to `LOG.md` (duration + gauge).
  Overlap guard: `pgrep -af "claude -p"` filtered for the step marker, with `pgrep` itself
  excluded or the test harness matches its own process.
- **Plan-usage gauge gate.** Before each step, read the 5h and 7d utilization headers from a
  1-token Haiku request (token from `~/.claude/.credentials.json`, never logged). At the user's
  ceiling, sleep until the reset timestamp in the same headers, then resume. The check is between
  steps, not during — say so; a long step can overshoot by a few points. Crossing the weekly cap
  blocks the user's own sessions too, which is why the ceiling is hard.
- **Install as a oneshot `systemd --user` unit** (`ExecStart=<venv python> runner.py`, no
  `Restart=always`, no EnvironmentFile). Stop = a `STOP` file in the report dir or
  `systemctl --user stop`. Record the stop procedure in memory the moment the program starts.
- **Step prompt contract:** implement → real tests (never mocked) → self-review against the
  ruleset → commit on `prog/<id>` → merge → restart the affected service only if the step says so
  → verify live (curl/journal) → report. Code style for every step: ~150-line files, header
  comment (purpose / loaded by / how to test), comments say WHY, no narrative docstrings.
- **Result parsing must tolerate a forgotten terminator.** A step that merged, tested and
  restarted will still sometimes omit its `STEP_RESULT:` line; marking it failed re-runs a
  finished step on top of itself. Fall back to the last ~15 lines of stdout (`merged …` /
  `DONE`) and log `inferred DONE`; only a missing merge is a failure. If it already happened,
  flip the step to `done` in `state.json` by hand after reading its report and the git log.
- **The review is the runner's job, not the user's.** The step's own self-review is not an
  independent review (same context, same blind spots). After each DONE: record HEAD before the
  step, run `/ponytail:ponytail-review` on `before..HEAD` as a read-only `claude -p` ("end with
  exactly one line `REVIEW_VERDICT: SHIP | FIX FIRST: <n>`"), on FIX FIRST dispatch a fix job
  pointing at the review file, re-review, cap at 2 rounds, and tag residue
  `orchestrator to close` in STATUS — never "ask Andrew". Out-of-scope findings a step reports
  are routed the same way; only facts the agent cannot know (a hostname, a secret) go to him.
- **Hot-swap the runner between steps, never under one.** Commit the new runner, `touch STOP`,
  wait for the unit to exit (it finishes the in-flight `claude -p` first), `rm STOP`, `restart`
  — and run that sequence itself as a `systemd-run --user` unit, because a chat turn ends before
  a 3-minute step does. Prove the swap by comparing the unit's MainPID start time with the
  runner file's mtime. Tests that mock `subprocess.run` must also answer the `git rev-parse`
  calls the review loop adds, or the pgrep assertion trips on the first `git` argv.
- **Commit hygiene inside the program:** the global gitleaks hook flags absolute home-dir (`<root-home>/…`) paths in the
  report dir; commit with `SKIP_GITLEAKS=1` and queue "review the rule" as a late step rather than
  weakening the global config mid-program. Set `git user.name/email` in every repo the program
  touches before step 1 — a missing identity fails the merge, not the edit.
- **Hand-off message to the user:** what is live (unit, step running, gauge now), what was done
  by hand, the decisions made for them, where to look (STATUS.md / LOG.md / steps/), how to stop,
  and the honest caveats (gauge granularity, restarts will kill the chat profile).

## 6. Close-out (the runner has exited)

- **Read the program's own record first:** `systemctl --user status <unit>` (exit code, duration),
  `STATUS.md`, `LOG.md`, the final step report. Its "for the user" list is the close-out spec.
- **Size it from git, not from the report.** `git diff --shortstat <baseline>..HEAD` per repo.
  Give deletions, additions and net separately, name the window, and say which commits were
  trim vs fix-first — "how many lines dropped" means gross deletions, and a repo whose root
  commit is the baseline has nothing countable before it. Say so.
- **Verify every open item live before relisting it** (grep the file, `systemctl --user cat`,
  `wc -c`) — a parallel session may have taken some. Then split the list: mechanical items that
  need one greenlight (stale doc lines, a drop-in env var, a memory trim, unstaged commits) vs
  items that need a direction (keep-or-revert, behaviour changes with no benchmark gate, product
  calls). "Go ahead with all" closes the first class; the second still needs its direction — ask
  for it in the close-out report, don't pick.
- **One gate arm, one backup dir** for the whole batch, then run the edits as a script file in
  `cache/scratch/` — the terminal gate scans command text, and skill/memory prose you are
  deleting carries blocklist words (`reboot`, `restart`) that kill a heredoc.
- **Excising a retired script:** residual mentions inside dated HISTORICAL paragraphs stay;
  live pointers get an existence check — for an architecture doc's file/cron tables, drop each
  row whose path or cron name no longer exists (`Path.exists()`, names from `cron/jobs.json`)
  instead of hand-picking.
- **Commits:** parallel-session skill edits first (their own commit), generated ledgers
  (`skills/.curator_ledger.jsonl`, `.usage.json`) alone with `SKIP_GITLEAKS=1` after the JSON
  report shows every hit is `root-deploy-path` in that file, then the close-out commit. Never
  bypass a commit that mixes generated noise with prose.
- **Env drop-in changes** need `daemon-reload` + gateway restart; prove it from
  `/proc/<MainPID>/environ`, not from `systemctl cat`. A CLI session is the "outside" the
  restart rule asks for.
- **Backfilling a watermarked cron script** (distill/ingest with `last_processed_ts` and a
  per-run cap): back up the watermark file into the batch backup dir, set the ts to 0, and drain
  it in a `systemd-run --user` loop (`-p EnvironmentFile=-~/.hermes/.env`, same venv python the
  cron uses). The loop must tolerate transient errors — count failures, `sleep 30`, stop only
  after ~5 — not `break` on the first non-zero rc; one TLS hiccup against the store otherwise
  ends the drain after one batch while the watermark sits mid-way. Done = a run that prints
  nothing.
- `memory_add` one fact at the end: what was excised, what moved where, backup dir. The
  extractors miss this class of decision.

## 7. "Go ahead with all" on the decision items → follow-up program, not inline work

When the user greenlights the second class (keep-or-revert, behaviour changes without a gate,
product calls) in one word, don't start editing: reuse the runner from §5 with a new report dir.

- Make `REPORT_DIR` overridable (`os.environ.get("REFACTOR_REPORT_DIR", default)`), write a
  short `PROGRAM.md` + `state.json` in `<report-dir>b/`, and launch as a transient unit:
  `systemd-run --user --unit=<prog>-b --collect -p Environment=HOME=/root
  -p Environment=REFACTOR_REPORT_DIR=<dir> -p WorkingDirectory=~/.hermes <venv python> runner.py`.
  Dry-run `load_state()` + `step_prompt()` against the new dir before launching.
- **Keep-or-revert items are decided in PROGRAM.md, not executed** — one `Dn … KEEP/REVERT` line
  so no step undoes the commit — and `memory_add` the decision at launch time.
- **Every behaviour-change step gets its gate built first, on untouched code, and the baseline
  recorded before any fix.** Writing the benchmark is where the biggest bug shows up: a wrapper
  that catches exceptions and returns `[]` reads as "no hit", not as a crash — the ranking metric
  on the real call path is the only thing that exposes it. State the gate as numbers (eval MRR
  within CI, new bench not below its own baseline, live factbench ≥ threshold after restart).
- **Research-first steps carry an explicit BLOCKED exit:** "if the hook/API cannot do X, end with
  `STEP_RESULT: BLOCKED: <file:line>` and a one-paragraph design note — do NOT emulate it with a
  side channel." Without that line the step builds the workaround you did not want.
- Reply with the unit name, spec/status paths, the stop command, and how many service bounces to
  expect; then read `STATUS.md`/`LOG.md` + `grep -iE 'before|after|MRR|deferred' step-*.md` on
  "status?" — one batched call, numbers side by side.

One message per completed audit while others run: headline findings, note which you verified
("grepped — confirmed"), name the cross-cutting pattern when it appears (this run: *silent false
success* — guards failing open, `|| true`, errors cached as results, stubs masking imports).
End with the proposed order and the single question the user must answer (rotation, deletion).