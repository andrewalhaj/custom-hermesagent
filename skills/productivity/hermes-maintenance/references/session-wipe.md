# Session Wipe Procedure

Full-clean-slate wipe of all sessions and messages from `state.db`. Use when the infrastructure stabilizes
after a migration and old troubleshooting/debugging sessions are noise, not value.

## Pre-flight

- All durable knowledge MUST be in Supabase, references/, skills, and hot memory BEFORE wiping.
  Sessions are conversation flow; facts extracted from them should already be in durable storage.
- Verify no cron job `context_from` references depend on session IDs (list all jobs, check the field).
  Jobs that use `session_search` as a tool will just find an empty DB — not broken, just quiet.

## Procedure

```bash
# 1. Create final backup
cp ~/.hermes/state.db ~/.hermes/state_final_YYYYMMDD.db

# 2. Wipe via python3 (sqlite3 binary not guaranteed on all hosts)
python3 << 'PYEOF'
import sqlite3
db = "~/.hermes/state.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

# Count before
cur.execute("SELECT COUNT(*) FROM sessions")
cur.execute("SELECT COUNT(*) FROM messages")
print(f"Before: {cur.fetchone()[0]} sessions, {cur.fetchone()[0]} messages")

# Wipe
cur.execute("DELETE FROM messages")
cur.execute("DELETE FROM sessions")
if "session_events" in [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
    cur.execute("DELETE FROM session_events")
if "message_fts" in [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
    cur.execute("DELETE FROM message_fts")

conn.commit()
cur.execute("VACUUM")

# Verify
cur.execute("SELECT COUNT(*) FROM sessions")
cur.execute("SELECT COUNT(*) FROM messages")
print(f"After: {cur.fetchone()[0]} sessions, {cur.fetchone()[0]} messages")
cur.execute("PRAGMA integrity_check")
print(f"Integrity: {cur.fetchone()[0]}")
conn.close()
PYEOF

# 3. Verify file shrunk
ls -lh ~/.hermes/state.db ~/.hermes/state_final_YYYYMMDD.db
```

## What survives

| Layer | Survives? |
|-------|-----------|
| Hot memory (MEMORY.md) | ✅ |
| Supabase knowledge base | ✅ |
| Honcho (cloud) | ✅ |
| References/ | ✅ |
| Config + .env | ✅ |
| Cron jobs | ✅ |
| Skills | ✅ |

## What's lost

- All session transcripts (FTS5 search returns nothing)
- Session history in gateway UI
- The ability to `session_search` for past troubleshooting flow

## Risks

- **Low** if durable knowledge is up to date. The sessions being purged should be troubleshooting artifacts,
  not irreplaceable reference material. If in doubt, keep the backup — it's a 37MB SQLite file.
- New sessions begin accumulating immediately as the conversation continues — the wipe only clears history,
  it doesn't stop future recording.

## Multi-profile wipe (all bots/agents)

When cleaning sessions across ALL profiles (ha-bot, voice-changer, etc.), kill the gateways first — they hold open connections to their state.db files. The active profile (current session) cannot be wiped.

```bash
# 1. Kill other gateway processes (skip the one you're in)
ps aux | grep 'hermes.*gateway run' | grep -v grep | grep -v 'profile default' | awk '{print $2}' | xargs kill 2>/dev/null

# 2. Wipe each profile's state.db (all FTS tables)
for db in ~/.hermes/profiles/*/state.db; do
  [ -f "$db" ] || continue
  python3 -c "
import sqlite3
c = sqlite3.connect('$db')
tables = [r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%messages%' OR name LIKE '%sessions%' OR name='sqlite_sequence'\")]
for t in tables:
    c.execute(f'DELETE FROM [{t}]')
c.commit()
s = c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
m = c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
print(f'$db: {s} sessions, {m} messages')
c.close()
"
done

# 3. Clear sessions.json files
for sf in ~/.hermes/profiles/*/sessions/sessions.json; do
  [ -f "$sf" ] && echo '[]' > "$sf" && echo "cleared $sf"
done
```

**Pitfall — active profile can't be wiped:** The profile running your current session holds its own state.db open. Wiping it mid-session may crash the gateway. Restart the gateway later to get a clean session slate.

## Alternative: CLI Bulk Deletion (no direct SQLite)

When you need to delete ALL sessions via the supported CLI path rather than raw SQLite:

```bash
# List all session IDs, pipe to delete
hermes sessions list 2>&1 | grep -oP '2026\d{4}_\d{6}_[a-f0-9]+' | while read id; do
  echo "Deleting: $id"
  hermes sessions delete --yes "$id" 2>&1
done

# Then optimize to reclaim disk space
hermes sessions optimize
hermes sessions stats
```

**Note:** The currently active session cannot be deleted — it will persist after the wipe. The DB file shrinks dramatically after optimize (8.1MB → 0.1MB typical).
