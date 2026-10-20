# Repairing a corrupt session in state.db (HTTP 400 tool_use / tool_result mismatch)

## Symptom
A Hermes session (TUI, WebUI, or gateway) stops responding. Every user message
silently produces no reply. The surface (especially WebUI) shows nothing — the
messages just pile up. Logs (`journalctl -u hermes-webui` / gateway logs) show:

```
HTTP 400: messages.<N>: `tool_use` ids were found without `tool_result` blocks
immediately after: toolu_XXXX. Each `tool_use` block must have a corresponding
`tool_result` block in the next message.
```

…and the fallback provider rejects it too with the mirror error:

```
HTTP 400: An assistant message with 'tool_calls' must be followed by tool
messages responding to each 'tool_call_id'. (insufficient tool messages...)
```

Both Anthropic and OpenAI-shaped providers enforce the tool_use↔tool_result
contract, so the corrupt history is rejected on EVERY send — the session is
wedged until the DB is repaired. Look for the log line
`Skipping session persistence for large failed session to prevent growth loop` —
that confirms the session is stuck in a failed-resend loop.

## Root cause
Two shapes, both from an interrupted/parallel tool call that wrote the message
rows inconsistently:

1. **Orphaned tool_result** — a `role='tool'` message whose `tool_call_id` does
   NOT appear in any active assistant message's `tool_calls` JSON. (Seen this
   session: assistant fired two parallel `patch` calls but only ONE id was saved
   into its `tool_calls` field; the second result row became an orphan.)
2. **Dangling tool_use** — an assistant message with `tool_calls` ids that have
   NO following `role='tool'` rows answering them (e.g. a tool call interrupted
   by `/stop` or a crash before the result was written).

NOTE: The `messages.<N>` index in the API error counts the provider's context
window (active, non-system messages), NOT DB row ids and NOT including the
system prompt. Don't try to map N directly to a row id — scan for the contract
violation instead (script below).

## Schema facts (state.db, verified 2026-06-18)
- `sessions.id` is the session id (NOT `session_id`). Other cols: `source`
  (`webui`/`cli`/...), `title`, `message_count`, `started_at` (epoch float).
- `messages` cols that matter: `id`, `session_id`, `role`
  (`user`/`assistant`/`tool`), `tool_call_id` (set on `role='tool'` rows),
  `tool_calls` (JSON array on `role='assistant'` rows, each `{id, function:{...}}`),
  `content`, `active` (1=in context, 0=excluded from the API send).
- The fix is to set `active=0` on the offending row — this EXCLUDES it from the
  context window WITHOUT deleting history. Non-destructive, reversible.

## Repair procedure
1. **Identify the session id** from the log `remote`/path or:
   `SELECT id, source, message_count FROM sessions ORDER BY message_count DESC LIMIT 5;`
   (Wedged sessions are usually near the top — they keep failing to trim.)
2. **Back up state.db FIRST** (this gate is non-negotiable — it's a write to a
   core data file): `cp state.db state.db.bak-$(date +%Y%m%d-%H%M%S)`.
3. **Run the diagnostic** (scripts/find_session_tool_mismatch.py) to list orphaned
   tool_results AND dangling tool_uses among ACTIVE messages.
4. **Deactivate the offending row(s)**: `UPDATE messages SET active=0 WHERE id=<id>;`
   - For an orphaned tool_result: deactivate the orphan tool row.
   - For a dangling tool_use: deactivate the assistant row (and any partial tool
     rows tied to it) — or, if other ids in that assistant message DID get
     answered, you must instead synthesize a stub tool_result; prefer
     deactivation when the whole turn is droppable.
5. **Re-run the diagnostic** — confirm zero orphans/dangles remain.
6. **Tell the user to retry a message** in the same surface. No restart needed;
   the next send rebuilds context from `active=1` rows only.

## Pitfalls
- Don't DELETE rows — set `active=0`. Deletion breaks FTS triggers
  (`messages_fts*`) and loses audit history. The active flag is the designed
  exclusion mechanism.
- The recorded side-effect (file edit, command) of an orphaned tool_result was
  usually ALREADY applied to disk — deactivating the row only removes it from
  the LLM's context, it does not undo the action. Verify the real-world state
  separately if it matters.
- After fixing, also sanity-check `memories/MEMORY.md` and `USER.md` — a session
  that wedged mid-edit may have left a half-written or duplicated memory entry
  (saw a duplicated entry + a stale delegation line this session). Round-trip
  them and dedup if needed.
- WebUI gives NO visible error on this — diagnose from gateway/webui logs, never
  from the UI alone.
