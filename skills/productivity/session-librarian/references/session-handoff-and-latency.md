# Session handoff after a model switch / new session, and "why was that reply slow"

Both procedures read the gateway's own logs and message store. Load when the user
says "continue where we left off" in a fresh session (model switch, /new, gateway
restart) or asks why a reply lagged.

## A. "Continue where we left off" — reconstruct, then check for a live collision

A model switch spawns a NEW session id; the old session is NOT killed and may still
be executing tool calls against the same repo. Reconstruct, check for a live
collision, then ask which agent owns the work before writing anything.

1. Find the prior session id and its last real turns. Skip `[IMPORTANT: Background
   process…]` and `origin=side_question` entries — they are notifications, not
   user intent:
   ```bash
   grep -h "agent.turn_context" ~/.hermes/logs/agent.log | tail -40 | cut -c1-300
   ```
2. Pull the prior session's last assistant messages + tool calls. Front door is
   `session_search(session_id=...)` / `chat_history_lookup`. Fallback is the
   sqlite store — open READ-ONLY with a timeout, because the gateway holds the
   write side and a plain `sqlite3.connect` can block indefinitely (surfaces as
   an "Orphan recovery" tool error):
   ```bash
   timeout 60 python3 - <<'EOF'
   import sqlite3
   con=sqlite3.connect('file:~/.hermes/state.db?mode=ro',uri=True)
   rows=con.execute("select role,substr(content,1,600),substr(tool_calls,1,250) from messages where session_id=? order by id desc limit 40",('<SESSION_ID>',)).fetchall()
   for r,c,tc in reversed(rows):
       if r in('user','assistant') and (c or tc): print(f'[{r}]',c or '','| TC:',tc or '','\n')
   EOF
   ```
   Session files are not under `sessions/` by id — the message store is
   `state.db`; `ls sessions/*<id>*` returns nothing and proves nothing.
3. Verify against the world, not the transcript: `git status --short`,
   `git log -1 --format='%h %ci %s'`, and re-run the prior session's verification
   script (`ls -t ~/.hermes/cache/scratch/*.sh | head`). Report what the rerun
   returned, not what the old session claimed.
4. **Collision check (before any write):** is the prior session still making API
   calls?
   ```bash
   grep -h "<OLD_SESSION_ID>" ~/.hermes/logs/agent.log | tail -3 | cut -c1-200
   ```
   A recent `conversation_loop: API call #N`, or a commit dated after the switch,
   means two agents share one working tree. Stop and give the user the choice:
   they stop the old session and you continue from its last commit, or you stand
   down until it reports. Never patch the same repo in parallel with it.

Pitfalls
- A commit timestamp later than the model switch is the cheapest proof the old
  session is alive — check `git log -1 --format='%ci'` before reading logs.
- `git diff` empty right after `git status` showed modified files means the other
  session committed in between — re-run status; don't assume a tooling bug.
- Don't pipe `git diff` through `grep -vE '^(\+\+\+|---)'` — it also strips real
  `++`/`--` content lines; dump to a scratch file and read that.

## B. "Why was your response slow" — answer from errors.log, not from a story

```bash
cd ~/.hermes && tail -25 logs/errors.log | cut -c1-200
```
Read the window around the slow turn and attribute in this order:
1. `agent.stream_diag: Stream drop on attempt 1/3 — retrying … error_type=ReadError`
   → first streaming attempt to the provider died and was retried; this alone
   explains a 30–90 s lag. Say "provider-side/transient, retried" with the
   timestamp; if it recurs every turn, suspect egress/tailnet instead.
2. `relay_runtime: Skipping Relay instrumentation for concurrent Hermes turn …` plus
   long `tool_executor` durations in ANOTHER session id → contention from a
   concurrent session (secondary cause; name it as such).
3. `[Telegram] polling heartbeat: N update(s) queued but not consumed (stuck probe)`
   → the user's message waited behind a busy turn; the model was not slow.
4. `conversation_compression` warnings on the turn → a compaction ran first.

Report: cause, timestamp, confidence, and the one condition under which the
diagnosis would change. Never attribute slowness to "thinking" or to the new
model without a log line.

## Glossary the user may ask about
- **Starlette** — Python ASGI framework FastAPI is built on; the gateway's HTTP/
  webhook server runs on it. Appears in update-runner logs as a dependency bump and
  in `requirements-dev.txt` (its TestClient needs `httpx2`). Not a person, not a
  profile.
