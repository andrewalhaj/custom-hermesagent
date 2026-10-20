# Silent-failure patterns: green surface, dead pipeline

Three independent Hermes pipelines were each found broken for 6+ days, all by the
same failure class: **an exception (or error envelope) swallowed such that the
telemetry read healthy.** None fired an alert; none showed an error; all were
found only by manually tracing "why hasn't X updated lately?" These are the
canonical worked examples for section 8 of the parent skill.

## Case 1 — TypeError counted as a dedup skip

**Pipeline:** `session_heartbeat.py`, a 15-min cron capturing facts to a vector store.

**Symptom:** audit log showed `processed 1 session(s): 0 stored, N skipped` every
run for 6 days. Looked exactly like healthy semantic dedup ("nothing new to store").

**Root cause:** a package refactor changed `store()`'s signature; the cron kept
passing a now-removed `context_prefix=` kwarg. Every call raised `TypeError`, caught by:

```python
try:
    store(fact, ..., context_prefix=session_id)   # TypeError: unexpected kwarg
    stored += 1
except Exception:
    skipped += 1        # broken write is indistinguishable from a dedup skip
```

**How it was found:** wrapped `is_duplicate()` and `store()` with tracers and replayed
a real session — every fact printed `is_duplicate=False` yet `stored` stayed 0, exposing
the swallowed raise. **Fastest possible probe:** feed a guaranteed-novel string; if it's
"skipped" too, the skip path is a lie.

**Fix:** correct the kwarg AND make failures a distinct signal — never `skipped += 1` on except.

## Case 2 — GraphQL 200 with `data:null`

**Pipeline:** Sentry webhook → Linear `issueCreate` → kanban card.

**Symptom:** Sentry alerts arrived and were logged; zero Linear issues created; the
dedup map file was empty `{}`; board had zero cards. No exception, no error log.

**Root cause:** Linear (like all GraphQL) returns **HTTP 200** with the failure inside
the body: `{"errors":[{"code":"USAGE_LIMIT_EXCEEDED"}], "data":null}` (workspace was over
the free-plan 250-active-issue cap). The handler read only `result["data"]["issueCreate"]["issue"]`,
so on `data:null` it got `{}`, wrote nothing, and raised nothing — the surrounding
`except` never even ran.

**Fix:** inspect the GraphQL `errors` array and `success` flag explicitly; `logger.error`
on failure. Immediate unblock: archive stale issues to get back under the cap. (Envelope
protocols — GraphQL, JSON-RPC, batch REST — always carry failure in the body, not the status.)

## Case 3 — logger with no handler under uvicorn

**Pipeline:** same Sentry handler. Secondary bug that hid Case 2.

**Symptom:** `journalctl -u <service>` showed ZERO `sentry webhook:` lines despite the
handler provably running (it was writing to a data file each call).

**Root cause:** `logging.basicConfig(...)` lived only in `server.py`'s `if __name__ ==
"__main__":` block. Production runs `uvicorn server:app`, which imports the module but
never executes `__main__` — so the root logger had no handler and every `routes.*`
`logger.info/warning/error` was silently dropped. Even if Case 2 had logged its error,
no one would have seen it.

**Fix:** configure logging at *import* time (module top level), overridable via env var.
Verify by inducing a `routes.*` ERROR and confirming it lands in journald.

## The meta-lesson

When multiple unrelated systems break in the same window, the prior should be **one
shared swallow pattern**, not N coincidences. All three here reduce to: *the failure
path was made to look like the success path.* The cheapest defense is a distinct,
loud failure signal on every `except` and every error-envelope check — and proving,
once, that that signal actually reaches a human-readable sink.

## Fast triage checklist for "why hasn't X updated?"

1. Is the job/handler even running? (last-run timestamp, PID, cron audit log)
2. Is it reporting 100% skip / empty / no-op? → treat as outage, probe with novel input.
3. Does it call an envelope API (GraphQL/JSON-RPC)? → check the errors array, not HTTP status.
4. Do its error logs actually appear anywhere? → induce one error and grep the sink.
5. Only after 1–4: suspect the input side (upstream empty, watermark, auth).
