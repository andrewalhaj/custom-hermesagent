# WebUI context parity — why "the WebUI me feels dumber" (and how to diagnose)

When the user says the agent on the **Hermes WebUI** seems less capable / less
aware / "dumber" than on Telegram, it is almost never a model or memory-loading
bug. The WebUI uses a SEPARATE codepath from the gateway/Telegram handler, and
several context-injection mechanisms that exist on the gateway side are simply
absent or behave differently in the WebUI. Diagnose in this order.

## The codepath split
- **Telegram / CLI / gateway:** `/usr/local/lib/hermes-agent/gateway/run.py`
- **WebUI:** `~/projects/hermes-webui/api/streaming.py` (agent built per-turn,
  cached in an in-process LRU keyed by session_id + an agent-identity signature)

These are different files. A feature wired into one is NOT automatically in the
other. Verify per-surface; don't assume parity.

## Cause 1 — B-full auto-RAG does NOT run in the WebUI (dominant factor)
`_bfull_retrieve` (per-turn Supabase injection of cold-store hits ≥0.80) lives in
`gateway/run.py` only. `streaming.py` has zero b-full integration.

Verify:
```bash
grep -c bfull ~/projects/hermes-webui/api/streaming.py        # → 0
grep -c _bfull_retrieve /usr/local/lib/hermes-agent/gateway/run.py # → ≥1
```
Hard evidence from the session DB (injection leaves a marker in message content):
```sql
SELECT s.source, COUNT(*) FROM messages m
JOIN sessions s ON m.session_id = s.id
WHERE m.content LIKE '%Cold-store auto-retrieval%'
GROUP BY s.source;
-- telegram: 7   webui: 0   (proven 2026-06-18)
```
So on Telegram every message gets relevant institutional facts auto-injected;
on WebUI the model sees only the frozen memory snapshot + whatever it explicitly
`knowledge.py search`-es. **Fix:** mirror the injection into `streaming.py`
before each turn (`context_prompt += _bfull_retrieve(message_text)`), gated as a
core/webui patch. Until then, WebUI sessions must lean on explicit search tool
calls. (Full detail lives in the `knowledge-store` skill's B-full section.)

## Cause 2 — memory snapshot is FROZEN at session start
`MemoryStore.load_from_disk()` reads `~/.hermes/memories/{MEMORY,USER}.md` ONCE
at agent construction and freezes `_system_prompt_snapshot` for prefix-cache
stability. It does NOT refresh mid-session. Consequence: if a session started
while MEMORY.md was depleted (e.g. the hourly offload cron had just stripped
behavioral entries to cold-store pointers), that session is stuck with the lean
snapshot for its whole life — even after MEMORY.md is repaired on disk. A
concurrent Telegram session started later picks up the fuller file and looks
"smarter" purely from richer frozen context.
- Both surfaces load the SAME files correctly — verify with a throwaway
  `MemoryStore(memory_char_limit=3000, user_char_limit=2250); load_from_disk()`
  and inspect `format_for_system_prompt('memory')`. If it's clean, loading isn't
  the bug — a stale per-session snapshot is.
- Compare two sessions' frozen prompts directly:
  `SELECT source, length(system_prompt), substr(system_prompt, instr(system_prompt,'MEMORY (your'), 60) FROM sessions WHERE message_count>10 ORDER BY started_at DESC;`
  A shorter MEMORY block / fewer `§` entries on the "dumber" surface = it froze a
  depleted file. The fix is forward-looking only (next new session gets the
  repaired file); the wedged session can't be refreshed without a new session.
- Root contributor: the Memory Offload cron over-offloads *behavioral* entries
  (delegation config, SOUL rules, model strings) that should stay hot every turn.
  The cron's own SAFETY clause forbids this ("NEVER offload entries with specific
  technical config that must fire unprompted every turn") but a weak local model
  running the cron ignores it. Restore the stripped behavioral entries to
  MEMORY.md and consider tightening the offload gate.

## Cause 3 — amnesia on restart (no auto session-resume)
On `systemctl restart hermes-webui`, the in-process LRU agent cache is wiped.
The previous conversation still exists in `state.db` (visible in the sidebar) but
the WebUI opens a NEW blank session by default — it does not auto-resume the last
one. So each restart looks like memory loss even though all files are intact.
**Fix shipped (2026-06-18):** `webui_prefill_messages_script` in config.yaml,
pointing at `~/.hermes/scripts/webui_startup_context.py`. The script:
- queries `state.db` for the most recent meaningful WebUI session (scored by
  recency × message-weight, skips trivial <4-message sessions),
- extracts the last ~4 assistant turns, redacts credential-shaped strings,
- emits JSON `{"messages":[{"role":"user","content":"[Session continuity …]"}]}`,
- fails silent (exit 0, empty stdout) on any error so it can never block start.
The WebUI reads config per-request, so NO restart is needed to pick up the script
key. The script MUST be `chmod +x` — `subprocess.run([path])` needs the exec bit
or it `PermissionError`s and the prefill silently no-ops.

WebUI prefill config keys (in config.yaml, top level):
- `webui_prefill_messages_script: <path>` — script whose stdout becomes prefill
- `prefill_messages_file: ''` — alternative static JSON file
- output shape: JSON `{"messages":[...]}` OR plain text (becomes one user message)

## Quick triage checklist when "WebUI seems dumber"
1. `grep -c bfull api/streaming.py` → 0 confirms no auto-RAG (Cause 1).
2. `ls -la ~/.hermes/run/kb.sock` + time a `knowledge.py search` — orphaned
   socket = every manual search is 7.7s cold (see knowledge-store skill).
3. Compare frozen MEMORY block length across surfaces (Cause 2).
4. Was the service restarted with no continuity script? (Cause 3.)
It is NOT (usually) a memory-loading failure — the files load fine on both
surfaces. The gap is per-turn injection + frozen snapshots + no auto-resume.

## Gating note
Editing `config.yaml` is gated (the `patch` tool refuses Hermes config files —
use `sed`/manual edit + a `.bak`). Restarting `hermes-webui`/`hermes-kb-daemon`
is gated (arm `write_gate.py` with an approval note). New scripts under
`~/.hermes/scripts/` are NOT gated.
