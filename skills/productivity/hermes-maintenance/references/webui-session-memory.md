# WebUI "seems dumber" — session-start memory snapshot model

> MAINTENANCE NOTE (2026-06-18): this skill's SKILL.md is AT the 100k-char limit,
> so this reference (and `references/memory-file-sanitizer.md`) could NOT be wired
> into `load_when:` yet. Discover them via the references/ listing. Curator: trim
> the SKILL.md body, then add load_when pointers:
>   - "WebUI instance seems dumber / amnesia on restart → references/webui-session-memory.md"
>   - "MEMORY.md/USER.md corrupted with N| line prefixes or stale HONCHO_DUP tags → references/memory-file-sanitizer.md"

## Symptom
The Hermes instance running in the WebUI feels noticeably less sharp than the
same model on Telegram/CLI: forgets delegation targets, host facts, behavioral
rules, doctrine it clearly "should" know. Same model (`claude-sonnet-4-6`), same
config — but worse. Also reported as "the you on the webui gets amnesia when it
gets restarted."

## Root cause: memory is SNAPSHOT-FROZEN at session start
`MemoryStore.load_from_disk()` (`tools/memory_tool.py`) reads `MEMORY.md` +
`USER.md` ONCE, at agent construction, and freezes a `_system_prompt_snapshot`.
That snapshot is what enters the system prompt for the WHOLE session — it does
NOT re-read disk mid-session (deliberate: keeps the prefix cache stable).

Consequences:
1. **A session reflects whatever MEMORY.md looked like at the moment it
   started.** If the offload/dedup cron trimmed MEMORY.md between two sessions,
   the older session keeps the fuller memory and the newer one runs lean. This
   is why two sessions on the same host can have visibly different knowledge —
   it's not the model, it's the frozen snapshot age.
2. **WebUI restart ≠ amnesia of files, but DOES start fresh sessions.** The
   WebUI keeps live `AIAgent` objects in an in-process LRU cache
   (`SESSION_AGENT_CACHE` in `api/streaming.py`). `systemctl restart
   hermes-webui` wipes that cache; a new session is built from scratch. Old
   sessions still exist in `state.db` (clickable in the sidebar) but nothing
   auto-resumes them, so conversation continuity is lost. MEMORY/USER/SOUL/
   AGENTS all reload fine — the lost thing is the prior *conversation context*.

## The amplifier: offload cron over-trims behavioral entries
The `Memory Offload (default)` cron replaces "offloadable" MEMORY.md entries
with one-line `knowledge.py search "..."` POINTERS. Pointers are inert as
passive context — they yield facts only when the agent actively runs the search.
So when the cron offloads a BEHAVIORAL/CONFIG entry (e.g.
`Mac Studio: 32b-64k=DEFAULT DELEGATION TARGET ...`, SOUL.md edit rules, the
memory doctrine), the next session loses that as standing context and only
recovers it if something happens to trigger a lookup. Net effect: a freshly
offloaded MEMORY.md makes the next session "dumber" until queried.

The cron's own SAFETY clause already says *NEVER offload entries with specific
technical config that must fire unprompted every turn* — over-trimming means the
classifier is being too aggressive. Distinguish:
- **HOT (never offload):** per-turn behavioral rules, delegation targets +
  model strings + IPs, greenlight/gate doctrine, memory doctrine. These shape
  every decision.
- **COLD-OK (pointer is fine):** project-specific facts needed only when that
  project is in play (Mealio details, voice-board, web-stack ports).

Fix when you see depletion: restore the HOT entries to MEMORY.md verbatim (not
as pointers), keep the genuinely-cold ones as pointers. Watch the cap — measure
`wc -m memories/MEMORY.md` vs the LIVE cap in config.yaml and trim cold pointers
to fit if restoring pushes over.

## Durable fix: session-continuity prefill
`webui_prefill_messages_script` (config.yaml key) runs a script at every new
WebUI session start; its stdout becomes a prefill message. Use it to inject a
summary of the last meaningful session so a restart no longer loses thread.
- Script: `scripts/webui_startup_context.py` (this skill's scripts/ dir).
- Output contract: JSON `{"messages":[{"role":"user","content":"..."}]}` OR
  plain text (becomes one user message). Empty stdout = silent (no prefill).
- Must `chmod +x` the script — the WebUI runs it via `subprocess.run([path])`,
  so a non-executable file raises `PermissionError` and the prefill is dropped.
- REDACT credentials in the output. A naive "last N messages" dump leaked the
  WebUI password into the prefill on first draft. Strip api-key/token/password/
  base64-blob shapes before emitting.
- Pick the session by recency × message-weight, not just "most recent" — else a
  trivial 2-message "what's the password" session wins over the real 200-message
  work session. Filter `message_count >= 4`.

## How to diagnose depletion (compare two snapshots)
Pull the frozen system prompts straight from state.db and diff the MEMORY block:
`SELECT system_prompt FROM sessions WHERE source='webui' ORDER BY started_at
DESC LIMIT 1` vs the same for `source='telegram'`. Compare the
`MEMORY (your personal notes) [N% — x/3000]` header and entry count. A lower %
/ fewer `§`-delimited entries on one surface = that session started against a
leaner MEMORY.md. The model and all the GATE/doctrine sections are usually
identical — the delta is almost always in the MEMORY/USER blocks.

## Pitfalls
- Don't "fix" a wedged/old session's memory — its snapshot is frozen and
  unfixable; only NEW sessions pick up an edited MEMORY.md. Tell the user to
  open a fresh tab/session after you restore MEMORY.md.
- Editing MEMORY.md mid-session does NOT change the current session's context.
  Verify fixes by inspecting the NEXT new session's system prompt, not the one
  you're talking through.
- The WebUI reads config.yaml per-request, so a `webui_prefill_messages_script`
  change takes effect on the next new session with NO service restart.
