# Empirically verifying prompt-cache warmth (Anthropic usage counters)

When a claim rests on "the system prompt is frozen / the prefix cache stays warm across
turns," DO NOT ship on code comments asserting byte-stability — comments drift from code.
Every Anthropic API response carries cache token counters; the meter is the proof.

## The two counters

On `response.usage`:
- `cache_creation_input_tokens` — tokens WRITTEN to cache this call (a cache miss/write)
- `cache_read_input_tokens` — tokens READ from cache this call (a cache hit)

Interpretation across turns of one session (same system prompt):
- **Turn 1:** creation large, read 0 → cache primed (expected COLD).
- **Turns 2+:** read ≈ system-prompt-sized, creation ≈ 0 → WARM (prefix stable).
- **If creation stays large every turn → CACHE BUST** (something is mutating the prefix).

Hit rate = `read / (read + creation)`. >80% on turns 2+ = stable prefix.

## The fast, authoritative path: read PRODUCTION stats from the session DB

Hermes records per-session cache totals in `state.db` (NOT sessions.db, which is empty;
NOT a `sqlite3` CLI — it isn't installed, use the venv's Python sqlite3 module). This is
the most trustworthy source because it's the REAL model, real auth, real prompts:

```bash
cd /usr/local/lib/hermes-agent && venv/bin/python -c "
import sqlite3
db = sqlite3.connect(os.path.expanduser('~/.hermes/state.db')); db.row_factory = sqlite3.Row
for r in db.execute('''SELECT model, message_count,
        cache_write_tokens, cache_read_tokens, input_tokens
    FROM sessions WHERE cache_read_tokens > 0
    ORDER BY started_at DESC LIMIT 10'''):
    tot = (r['cache_write_tokens'] or 0)+(r['cache_read_tokens'] or 0)
    hit = round(100*(r['cache_read_tokens'] or 0)/tot) if tot else 0
    print(f\"{r['model']:22} msgs={r['message_count']:3} write={r['cache_write_tokens']:>9} read={r['cache_read_tokens']:>10} hit={hit}%\")
"
```

Verified 2026-06-19: long real sessions (199–338 msgs) on `claude-sonnet-4-6` show
**92–96% cache-read rates** → the system-prompt prefix IS byte-stable across turns. Short
sessions (2–14 msgs) show low rates because the cache only warms after turn 1 — that's
expected, not a regression. Read the LONG sessions for the steady-state verdict.

## Direct-probe gotchas (if you script live API calls instead)

A hand-rolled `anthropic.Anthropic(...).messages.create(...)` cache probe is fiddly under
the OAuth bypass — documented failures this session:
- Credentials file `~/.claude/.credentials.json` uses key `accessToken` (camelCase), NOT
  `access_token`. Wrong key → empty token → `httpx LocalProtocolError: Illegal header
  value b'Bearer '`.
- OAuth uses **`auth_token=<accessToken>`** (Bearer), not `api_key=`. Pass `api_key` with an
  OAuth token and the SDK 400s or mis-routes.
- Do NOT add `context-1m-2025-08-07` to the beta header on a Max-plan OAuth token → 400
  `"The long context beta is not yet available for this subscription."`
- On some model/subscription combos a direct probe shows `creation=0, read=0` (cache_control
  silently dropped) even when production caching works. When the direct probe disagrees with
  the production DB stats, **trust the DB** — it's the real path. Prefer the DB query above
  over a hand-rolled probe.

## Why this matters for the Honcho/memory architecture

The frozen-prompt + `system_and_3` caching design (system prompt built once per session,
replayed byte-identical, one cache breakpoint at its end + last 3 messages) means dynamic
memory/Honcho context must land in the USER-MESSAGE TAIL, never the system prompt, or it
busts the prefix. This DB check is how you confirm a memory-injection change didn't
accidentally start mutating the cached prefix. See the honcho skill
`references/config-source-resolution.md` for the injection-path trace.
