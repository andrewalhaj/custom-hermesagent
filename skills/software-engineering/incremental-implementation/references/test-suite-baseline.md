# Test-suite baseline and order-dependence

Use before reviewing/refactoring/pushing a repo, and whenever failures move around between runs.

## 1. Baseline honestly

- Run the full suite with the repo's own venv, and the typecheck (`npx tsc -b --noEmit` for TS apps), BEFORE changing anything. Run without piping into `tail`, or capture `$?` first — a pipe reports the exit code of `tail`, not the tests.
- Read the **skipped** count, not just failed. A missing test dependency often turns tests into skips/errors; installing it unskips them and new failures appear. Those were always broken — they are part of the baseline, not caused by your fix.
- Put test-only deps in `requirements-dev.txt` (pinned), separate from runtime `requirements.txt`, so the gap can't recur.
- Unset telemetry for test runs (`SENTRY_DSN=`) so the suite doesn't try to ship events or hang on flush.

## 2. Prove order-independence

A green forward run proves nothing about isolation. Run every order before calling it green:

```bash
run() { out=$(.venv/bin/python -m pytest -q -p no:cacheprovider --no-header "$@" 2>&1); echo "exit=$? $(echo "$out" | grep -E '[0-9]+ (passed|failed)' | tail -1)"; echo "$out" | grep -E '^(FAILED|ERROR)'; }
run tests                                   # forward
run $(ls tests/test_*.py | sort -r)         # reverse
for s in 1 2 3 4 5 6 7 8; do run $(ls tests/test_*.py | shuf --random-source=<(yes $s)); done
for f in tests/test_*.py; do run "$f"; done # each file alone
```

Keep it as a script file in scratch; re-run it after each fix (fixing one order often exposes the next).

## 3. Common causes and fixes

| Symptom | Mechanism | Fix |
|---|---|---|
| Test passes alone, fails in the suite (`no such table`, wrong path, 503 "token not configured") | Module reads env (`KANBAN_DB`, `*_TOKEN`) into a module-level constant at FIRST import; another test module imported it first with different env, so `os.environ` set at the top of this file never reaches it | In `setUp`, save and pin the module attribute (`mod.KANBAN_DB = _TMP_DB`), restore in `tearDown`/`addCleanup`. Pin it on EVERY module the code path touches (e.g. both linear and sentry closers), not just the one under test |
| `RuntimeError: There is no current event loop in thread 'MainThread'` depending on order | `asyncio.get_event_loop().run_until_complete()` fails once another test's `asyncio.run()` closed the default loop | Use `asyncio.run(coro)` in the test helper |
| `importlib.reload(mod)` in one test breaks others | reload re-reads env and replaces module globals other tests patched | Pin attributes in setUp instead of reloading where possible |

Grep for the pattern across the test tree once one instance is found (`grep -n "environ\[" tests/*.py`, `get_event_loop`) — the same bug is usually in sibling suites.

## 4. Commit

Test-only fixes go on their own `fix/` branch, separate from feature/refactor work, with the order-matrix result in the commit body.
