# Memory Blocked by Identity Filter — Diagnosis & Fix

## Symptom

A profile "severely underperforms" on recall despite having identical config, memory files, skills, and model provider as a working profile. User reports: forgetting corrections between sessions, re-deriving known diagnoses, ignoring greenlight rules, not checking memory before troubleshooting.

All static file comparisons come back clean. Skills exist. MEMORY.md has 4K+ of well-structured content.

## Root cause

The Hermes identity/credential filter scans MEMORY.md before injecting it into the system prompt. If any line matches a threat pattern, the **entire MEMORY.md is replaced** with a `[BLOCKED]` stub. The model never sees its own memory.

The MEMORY.md file on disk is untouched — it looks fine to any file-level check. Only the LIVE system prompt reveals the block.

The filter is conservative: one match on one line blocks everything. A 4,108-byte file with one line containing `~/.ssh/id_ed25519` becomes 180 bytes of `[BLOCKED: MEMORY.md entry contained threat pattern(s): ssh_access...]`.

## Detection

### Method 1: Ask the user (fastest)

"When you start a new chat with this bot, what does the top of the system prompt say in the MEMORY section? Does it show real content (like 'Role: HAJarvis...') or does it show `[BLOCKED]`?"

### Method 2: Query state.db directly

```bash
python3 -c "
import sqlite3, os
db = os.path.expanduser('~/.hermes/profiles/<profile>/state.db')  # or ~/.hermes/state.db for default
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute('SELECT system_prompt FROM sessions WHERE message_count > 10 ORDER BY started_at DESC LIMIT 1')
sp = cur.fetchone()[0]
# Check memory section
idx = sp.find('MEMORY (your personal notes)')
if idx > 0:
    section = sp[idx:idx+500]
    if '[BLOCKED' in section:
        print('BLOCKED:', section[:200])
    else:
        print('OK — memory loaded')
else:
    print('No memory section found')
conn.close()
"
```

### Method 3: Save and grep system prompt

```bash
python3 -c "
import sqlite3, os
db = os.path.expanduser('~/.hermes/profiles/ha-bot/state.db')
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute('SELECT system_prompt FROM sessions WHERE message_count > 10 ORDER BY started_at DESC LIMIT 1')
with open('/tmp/sp_check.txt', 'w') as f:
    f.write(cur.fetchone()[0])
conn.close()
"
grep -c 'BLOCKED' /tmp/sp_check.txt
```

## Common filter triggers

| Pattern | What matches | Fix |
|---|---|---|
| `ssh_access` | `~/.ssh/id_ed25519`, `~/.ssh/id_rsa`, SSH key paths | Rephrase: "key-based access configured" or "SSH access set up" |
| Private key PEM | `-----BEGIN.*PRIVATE KEY-----` blocks | Remove raw keys from memory — they belong in files, not text |
| API keys inline | Long random-looking strings near `api_key` | Don't store raw keys in MEMORY.md |
| Bearer tokens | `Bearer eyJ...` | Reference token source, not the token itself |

## Fix procedure

1. **Back up**: `cp MEMORY.md MEMORY.md.prev-block-fix`
2. **Identify trigger**: binary-search by removing half the lines and checking if the block clears (tedious — better to scan for known patterns first)
3. **Scan for patterns**: search for `ssh`, `id_rsa`, `id_ed25519`, `-----BEGIN`, `api_key`, `Bearer` in the file
4. **Reword, don't delete**: the information is valuable — just remove the credential-shaped text while keeping the knowledge
5. **Verify clean**: re-run the detection method to confirm no `[BLOCKED]` in the next session's system prompt

## Example: HAJarvis (2026-06-05)

**Offending line:**
```
HA host = backup VPS <vps-ip> (access via key ~/.ssh/id_ed25519).
```

**Fixed line:**
```
HA host = backup VPS <vps-ip> (key-based access configured).
```

**Before**: Memory section showed `[8% — 180/2,200 chars] [BLOCKED: ssh_access]`
**After**: Memory section should show `[~90% — ~4,100/2,200 chars]` with full content

## Prevention

- Never include SSH key paths in MEMORY.md
- Never include raw API keys, tokens, or private key PEM blocks in any memory file
- After any memory write, imagine the filter scanning it: would any line trigger a credential pattern?
- The USER.md file is separately scanned — same rules apply
