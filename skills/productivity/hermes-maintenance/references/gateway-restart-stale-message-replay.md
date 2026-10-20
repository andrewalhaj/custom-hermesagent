# Gateway restart → stale-message replay / acting on the wrong task

## Symptom

After a gateway restart, the agent resumes work on a task the user thought
was already finished, or acts on an old message as if it were new. The user
says some variant of: "Why did you bring this up randomly?", "I didn't send
that", "you have amnesia", or "something breaks on restarts."

The agent typically cannot explain it from its own context because the
triggering history was compacted away.

## Do NOT trust the obvious first hypothesis

The tempting (and in our 2026-06-18 incident, WRONG) root cause is Telegram
update replay via `drop_pending_updates=False`. That is a *real* secondary
bug (see "Secondary bug" below) but it was NOT the cause of acting on a stale
message. Verify against `state.db` before blaming the transport layer.

## Real root cause: restart → auto-resume → compaction collapses task state

Chain of events (each link confirmed from `state.db` message rows + agent.log):

1. User legitimately sends message M (e.g. an image + instructions). It is
   processed correctly at the time and persisted to `state.db`.
2. Gateway restarts (SIGTERM under systemd, or a manual restart). On startup
   `gateway.run._schedule_resume_pending_sessions()` logs
   `Scheduled auto-resume for N restart-interrupted session(s)` and injects a
   synthetic **empty-text** inbound event (`msg=''`, `internal=True`). The
   `_is_resume_pending` branch in `_handle_message_with_agent` prepends a
   `[System note: ... previous turn was interrupted by a gateway restart ...]`
   wrapper.
3. The auto-resume turn (or the next real turn) trips session hygiene
   compression: `messages=140 -> 12`. The compaction summary describes the
   prior session as "mid-way through <task>" — it does NOT distinguish
   **task already completed earlier** from **task still in progress now**.
4. The agent reads that summary + the still-present old message M in history,
   and resumes M as if it were the current request — even though the user's
   actual most-recent message was answering something else entirely (e.g. a
   "yes" greenlighting a *different* patch).

The compaction summary quality is the culprit: it lost the done-vs-pending
distinction and surfaced a finished task as the active one.

## How to diagnose (reconstruct the true message sequence from state.db)

Logs alone mislead (truncated at 80 chars, interleaved cron noise). Read the
actual persisted messages with the venv python — the system `sqlite3` binary
is often absent on the Mac host, and `created_at` does not exist (the column
is `timestamp`):

```python
/usr/local/lib/hermes-agent/venv/bin/python3 - <<'PY'
import sqlite3
conn = sqlite3.connect(os.path.expanduser('~/.hermes/state.db'))
print([c[1] for c in conn.execute('PRAGMA table_info(messages)')])
rows = conn.execute(
    "SELECT role, substr(content,1,300), id FROM messages "
    "WHERE session_id=? ORDER BY id ASC", ('<SESSION_ID>',)
).fetchall()
print('total', len(rows))
for i,(role,content,mid) in enumerate(rows):
    print(f'\n[{i}] id={mid} {role}'); print(repr(content[:250]))
PY
```

Look for: a `[CONTEXT COMPACTION ...]` assistant message early in the
surviving rows, immediately followed by old user messages that predate the
user's real current ask. That ordering is the fingerprint of this bug.

Cross-reference `agent.log`:
- `Scheduled auto-resume for N restart-interrupted session(s)` = restart fired
  auto-resume.
- `inbound message: ... msg=''` right after startup = the synthetic resume event.
- `context compression done: ... messages=140->12` = the summary that
  collapsed task state.
- A user row with `reply_to_text='<agent's resume response>'` = the user
  reacting to the agent surfacing a stale task ("Why did you bring this up?").

## Secondary bug (real, but not the replay cause): drop_pending_updates

`gateway/platforms/telegram.py` had `drop_pending_updates=False` on the two
in-process reconnect paths (network-error reconnect ~line 1468, conflict-retry
~line 1595) while the clean initial start used `True`. In-process reconnects
happen seconds apart; an unacknowledged update in that window gets
re-delivered as a "new" message (observed as the same message arriving twice,
~30s apart). Fix: set both reconnect paths to `True` to match initial start.
This was committed (do not re-derive — `git log --grep "drop pending updates"`).

## Durable fix direction (the actual cause)

The compaction summary must preserve done-vs-pending task state. The summary
should explicitly mark completed work as completed and name the single most
recent OUTSTANDING user request, so an auto-resume turn cannot resurrect a
finished task. The current `[CONTEXT COMPACTION — REFERENCE ONLY]` wrapper
already tells the agent to treat the summary as background and answer only the
latest message — but when the latest real message is a bare "yes"/"test" and
the summary frames an old task as active, the agent still mis-binds it.
When this recurs, tighten the compaction prompt (search
`agent/conversation_compression`), not the transport layer.

## Pitfalls

- Do NOT change `drop_pending_updates` and declare victory — it does not fix
  the resume/compaction mis-binding. Verify against state.db.
- `sqlite3` CLI may be missing on the host; use the venv python's `sqlite3`
  module. The messages table column is `timestamp`, not `created_at`.
- Background skill-review and compaction passes are restricted to
  memory/skill tools — you cannot read files or run sqlite during them.
  Do the state.db forensics in a normal turn.
