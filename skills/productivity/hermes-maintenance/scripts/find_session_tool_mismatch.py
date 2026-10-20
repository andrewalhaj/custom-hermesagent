#!/usr/bin/env python3
"""Find tool_use<->tool_result contract violations in a Hermes session.

Detects the two shapes that wedge a session with HTTP 400 on every send:
  1. Orphaned tool_result: a role='tool' row whose tool_call_id is declared by
     no active assistant tool_calls.
  2. Dangling tool_use: an assistant row whose tool_calls ids are not all
     answered by following role='tool' rows.

Read-only. Prints the offending message ids; does NOT modify the DB.
After backing up state.db, fix with:  UPDATE messages SET active=0 WHERE id=<id>;

Usage:
  python3 find_session_tool_mismatch.py <session_id> [path/to/state.db]
Default DB: ~/.hermes/state.db
"""
import sqlite3, json, os, sys
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()

SESSION = sys.argv[1] if len(sys.argv) > 1 else None
DB = sys.argv[2] if len(sys.argv) > 2 else str(HERMES_HOME / "state.db")

if not SESSION:
    # No session given: list the top candidates (wedged sessions trend large).
    db = sqlite3.connect(DB); db.row_factory = sqlite3.Row
    print("No session id given. Top sessions by message_count:")
    for r in db.execute("SELECT id, source, title, message_count FROM sessions "
                        "ORDER BY message_count DESC LIMIT 10"):
        print(f"  {r['id']}  src={r['source']:6}  msgs={r['message_count']:4}  {r['title']}")
    db.close(); sys.exit(0)

db = sqlite3.connect(DB); db.row_factory = sqlite3.Row
msgs = db.execute(
    "SELECT id, role, tool_call_id, tool_calls FROM messages "
    "WHERE session_id=? AND active=1 ORDER BY id ASC", (SESSION,)).fetchall()

# All tool_call ids declared by active assistant messages.
declared = set()
for m in msgs:
    if m["role"] == "assistant" and m["tool_calls"]:
        try:
            for c in json.loads(m["tool_calls"]):
                if isinstance(c, dict) and c.get("id"):
                    declared.add(c["id"])
        except Exception as e:
            print(f"  parse error on assistant id={m['id']}: {e}")

# Shape 1: orphaned tool_result rows.
orphans = [m["id"] for m in msgs
           if m["role"] == "tool" and m["tool_call_id"] not in declared]

# Shape 2: dangling tool_use (assistant ids not answered by following tool rows).
dangling = []
for i, m in enumerate(msgs):
    if m["role"] == "assistant" and m["tool_calls"]:
        try:
            expected = {c["id"] for c in json.loads(m["tool_calls"])
                        if isinstance(c, dict) and c.get("id")}
        except Exception:
            continue
        found = set()
        j = i + 1
        while j < len(msgs) and msgs[j]["role"] == "tool":
            found.add(msgs[j]["tool_call_id"]); j += 1
        missing = expected - found
        if missing:
            dangling.append((m["id"], sorted(missing)))

print(f"Session {SESSION}: {len(msgs)} active messages")
print(f"Orphaned tool_result rows (deactivate these): {orphans or 'none'}")
if dangling:
    print("Dangling tool_use (assistant id -> unanswered call ids):")
    for mid, miss in dangling:
        print(f"  assistant id={mid} missing {miss}")
else:
    print("Dangling tool_use: none")

if not orphans and not dangling:
    print("CLEAN — session satisfies the tool_use/tool_result contract.")
db.close()
