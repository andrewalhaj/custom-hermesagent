# KB Warm-Search Daemon — Build Detail & Operation

Built 2026-06-17 to kill the `knowledge.py search` cold-start (7.9s → 0.5s).
This file is the build-detail companion to the cold-start pitfall in SKILL.md.
**Operate it; don't rebuild it.**

## What was actually wrong

Profiling `knowledge.py search` (7.9s) showed the vector search itself is ~70ms;
the rest is per-process cold-start: `import sentence_transformers` (torch) ~3.6s,
model load ~1.9s, `import lancedb` ~1.2s. The hot path (B-full per-turn RAG)
already keeps the model warm — only **cold callers** (CLI, crons that shell out:
the hourly Memory Offload verify, Daily Knowledge Capture, dedup) paid the 7.9s.

## The fix is TWO parts — both required

### Part 1 — lazy imports in knowledge.py (the bigger, easily-missed half)
`lancedb` and `sentence_transformers` were imported at module top. Even with a
perfect daemon, running `knowledge.py search` *at all* paid ~4.8s just importing
those before reaching the daemon — the daemon alone only got it to 5.9s.
Moving each import inside its lazy wrapper got it to 0.5s:
- `import lancedb` → inside `get_db()`
- `from sentence_transformers import SentenceTransformer` → inside `get_model()`

Both are referenced ONLY inside those wrappers (verify with grep before moving).
`numpy` stays at top (cheap, ~0.1s, used widely).

**Regression watch:** if `knowledge.py search` is slow again, FIRST check these two
imports are still lazy. A refactor or merge that hoists them back to module top
silently kills the fast path even with the daemon healthy.

### Part 2 — daemon + thin client
- `scripts/kb_daemon.py` — Unix-socket server at `~/.hermes/run/kb.sock`
  (dir 0700, socket 0600). On startup: `importlib`-loads knowledge.py, calls
  `get_model()` + one warmup `search()` to force the load, then serves.
  Protocol: newline-delimited JSON, one request+response per connection.
  Robustness baked in: one bad request never crashes it (per-connection
  try/except); SIGTERM/SIGINT unlink the socket; a stale socket (connection
  refused) is reclaimed at startup; a live socket → exit 0 "already running".
- `scripts/kb_client.py` — pure-stdlib (NO torch/lancedb import — that's the
  point) client. `daemon_search(...)` returns hits or raises `DaemonUnavailable`
  on ANY failure so the caller falls back. Also a CLI for testing.
- `knowledge.py search` CLI block tries `kb_client.daemon_search` first, falls
  back to in-process `search()` on `DaemonUnavailable`. `KB_NO_DAEMON=1` forces
  in-process (for benchmarking the fallback path).

## Supervision
- Unit: `~/.config/systemd/user/hermes-kb-daemon.service`, `Restart=always`,
  `enable --now`, `WantedBy=default.target`. Pays the 6.7s cold-start ONCE at
  start, then every search is warm.
- `infra_watchdog.py` (§2b) checks `systemctl --user is-active`; if down,
  restarts and re-checks. SILENT on clean heal (pure-acceleration service —
  in-process fallback means a dead daemon degrades, not breaks). Escalates to P1
  ONLY if the restart fails. This matches the "alert only if broken" doctrine —
  do NOT make a successful auto-restart fire an alarm.

## Verification recipe (proven)
```bash
# daemon up?
systemctl --user is-active hermes-kb-daemon.service        # active
journalctl --user -u hermes-kb-daemon.service -n3 | grep ready   # "loaded in ~6.7s"
ls -la ~/.hermes/run/kb.sock                                # srw------- present

# fast path (~0.5s) vs forced fallback (~8s)
time python3 ~/.hermes/scripts/knowledge.py search "anthropic oauth"
time KB_NO_DAEMON=1 python3 ~/.hermes/scripts/knowledge.py search "anthropic oauth"

# other commands still work (they lazy-load the heavy libs correctly)
python3 ~/.hermes/scripts/knowledge.py status        # fact count
python3 ~/.hermes/scripts/kbench.py eval          # P@5=100%

# auto-restart works: stop it, run watchdog, confirm it heals silently
systemctl --user stop hermes-kb-daemon.service
XDG_RUNTIME_DIR=/run/user/0 python3 ~/.hermes/scripts/infra_watchdog.py   # no KB P1
systemctl --user is-active hermes-kb-daemon.service   # active again
```

## Build-process lesson: verify delegated code, don't trust the summary
The two daemon files were first drafted by a delegate_task subagent (32b local).
The subagent's own summary contained the bugs in plain sight: `kb_client.py`
referenced `RUN_DIR` and `sys` without defining/importing them; `kb_daemon.py`
used `module` and `server_socket` outside the scopes they were defined in
(NameError), had inverted stale-socket logic, a no-op `except A or B`, and a
single `recv(4096)` that truncates large results. **AST `ast.parse()` passed on
all of it** — syntax-valid, runtime-broken. The orchestrator read both files in
full and rewrote them (correctness fixes are the orchestrator's job; generating
the bulk draft is the subagent's). Takeaway: for delegated code, READ the actual
files and run a real exercise (start it, hit it) — an AST check and a confident
summary prove nothing about runtime correctness.
