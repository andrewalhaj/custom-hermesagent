# Session Audit Query

Run this against the Hermes session database to find token-waste patterns.

## Database Schema

The session DB is at `~/.hermes/state.db` (SQLite + FTS5).

```
sessions:
  id, title, source, model, started_at, ended_at,
  message_count, tool_call_count,
  input_tokens, output_tokens,
  cache_read_tokens, cache_write_tokens, reasoning_tokens,
  estimated_cost_usd, actual_cost_usd

messages:
  id, session_id, role (user|assistant|tool),
  content, tool_calls (JSON string for assistant), tool_name,
  timestamp, token_count
```

## Full Audit Query

```python
import sqlite3, json

db = sqlite3.connect(os.path.expanduser("~/.hermes/state.db"))
db.row_factory = sqlite3.Row

# All sessions in range
sessions = db.execute("""
    SELECT id, title, model, started_at, message_count,
           tool_call_count, input_tokens, output_tokens,
           estimated_cost_usd
    FROM sessions
    WHERE started_at > ?
    ORDER BY started_at DESC
""", (cutoff_timestamp,)).fetchall()

for s in sessions:
    # Count tool patterns per session
    msgs = db.execute("""
        SELECT id, role, tool_calls, tool_name
        FROM messages
        WHERE session_id = ?
        ORDER BY id
    """, (s['id'],)).fetchall()

    delegate_count = 0
    web_search_count = 0
    terminal_count = 0
    browser_count = 0
    vision_count = 0

    for m in msgs:
        if m['role'] == 'assistant' and m['tool_calls']:
            tc = m['tool_calls']
            if 'delegate_task' in (tc or ''):
                delegate_count += 1
            if 'web_search' in (tc or ''):
                web_search_count += 1
            if '"name": "terminal"' in (tc or ''):
                terminal_count += 1
            if 'browser_' in (tc or ''):
                browser_count += 1
            if 'vision_' in (tc or ''):
                vision_count += 1

    # Flag violations
    if delegate_count == 0 and (s['input_tokens'] or 0) > 100_000:
        print(f"VIOLATION: {s['title']} — {s['input_tokens']:,} input tokens, ZERO delegation")
    # CRITICAL: output-token grind is the most expensive pattern and input thresholds miss it.
    # Sessions with tiny input (<2K) but huge output (>200K) are in-process builds on the
    # orchestrator (Sonnet output ~5x input price). Always check output, not just input.
    if delegate_count == 0 and (s['output_tokens'] or 0) > 200_000:
        print(f"VIOLATION: {s['title']} — {s['output_tokens']:,} OUTPUT tokens, ZERO delegation (in-process build — should be a subagent)")
    if web_search_count > 8 and delegate_count == 0:
        print(f"VIOLATION: {s['title']} — {web_search_count} web_search, zero delegation")
    if terminal_count > 15:
        print(f"VIOLATION: {s['title']} — {terminal_count} terminal calls")
```

## Always sort by cost AND output, never input alone

```python
# The headline ranking — this is what reveals the real money.
sessions = db.execute("""
    SELECT id, title, source, model, input_tokens, output_tokens,
           cache_read_tokens, estimated_cost_usd
    FROM sessions WHERE started_at > ?
    ORDER BY estimated_cost_usd DESC
""", (cutoff,)).fetchall()
# Then group by source+model to separate interactive spend from cron spend:
#   SELECT source, model, COUNT(*), SUM(estimated_cost_usd) ... GROUP BY source, model
# Interactive (source='telegram'/'cli') Sonnet sessions are typically 85-90% of total;
# the entire cron fleet is usually a few dollars. Don't tune crons to save pennies while
# the orchestrator burns $100/session on in-process builds.
```

## cache_read ratio sanity check

```python
# A high cache_read_tokens / (cache_read + input) ratio (99%+) is GOOD — caching works.
# It also means input cost is negligible and ALL real spend is in output. Confirm this
# before proposing "trim the system prompt" fixes — the lever is output (delegation),
# not input (prompt size). Prompt trims still help (they cut the cached base re-read on
# every turn) but they are second-order next to moving output generation to a subagent.

## Toolset Schema Overhead

Estimate per-toolset token cost. Browser alone has 13 tools with complex parameter schemas (~800-1000 tokens/turn). Disabling it is the single highest-impact cut.

Toolsets loaded per session (from `toolsets.py` `_HERMES_CORE_TOOLS`):
- web (2 tools), browser (13), terminal (2), file (4)
- vision (1), image_gen (1), tts (1), skills (3)
- todo (1), memory (1), session_search (1), clarify (1)
- delegation (1), cronjob (1), messaging (1), code_execution (1)
- homeassistant (4, gated), kanban (9, gated), computer_use (1, gated)

Total enabled: ~34 tools. After removing browser: ~21 tools (~25-30% savings per turn).
