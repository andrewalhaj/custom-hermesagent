# MEMORY.md / USER.md corruption: read_file line-prefixes + stale HONCHO_DUP tags

## Two corruption shapes (both seen 2026-06-18)

1. **`N|` line-number prefixes** — entries like `4|Mac Studio: ...`,
   `9|Wall-dash: ...`. Source: a model (usually the local Studio aux model
   running the hourly offload/dedup cron) read MEMORY.md with the `read_file`
   tool — which returns `LINE_NUM|content` — then wrote that numbered output
   BACK to disk via `write_file`. The line-number formatting leaks into the
   file. Periodic + line-number-shaped = this bug. This is the "memory gets
   fucked on every restart" complaint: the next session snapshots the polluted
   file.

2. **Stale `[HONCHO_DUP: YYYY-MM-DD]` tags** — these are BY DESIGN during the
   3-day grace window (the Memory Honcho Dedup cron tags a duplicate, then
   removes it only after ≥3 days). They are corruption only once the tag is
   ≥3 days old and the dedup cron failed to remove it.

## Fix: mechanical sanitizer (defense in depth, no core patch)
`~/.hermes/scripts/memory_sanitize.py` strips `^\d+\|` prefixes and expired
`[HONCHO_DUP]` tags from MEMORY.md + USER.md. Idempotent, silent when clean,
prints `[FIXED]` + backup path when it acts. Flags: `--verbose` (always print),
`--check` (exit 1 if corruption found, no write). Creates `.bak-sanitize-<ts>`
before any write.

Deployed two ways:
- **30-min watchdog cron** (`no_agent=true`, deliver=local) running the script
  directly — silent on the happy path, surfaces `[FIXED]` only when it cleans
  something. Caps corruption lifetime at ≤30 min regardless of which model edits
  the file.
- **In-prompt integrity check** added to the offload + dedup cron prompts:
  after any write to MEMORY.md, run `memory_sanitize.py --check`; non-zero →
  restore from the `.bak` just made + report failure instead of success. Both
  prompts also gained an explicit "DO NOT use read_file on MEMORY.md — it adds
  N| prefixes; use `cat` (terminal) or the patch tool for targeted edits."

## Why not patch the core read path
`MemoryStore.load_from_disk()` → `_read_file()` does a raw `read_text()` with no
sanitization, and `_sanitize_entries_for_snapshot()` only scans for prompt-
injection threat patterns, not formatting corruption. The natural hook is that
core file — but it's gated (core hermes-agent code). The cron + standalone
watchpoint is the update-proof route: it survives core updates and needs no
greenlight to maintain. (Pattern mirrors the other guard scripts under
~/.hermes/scripts/.)

## Pitfall
There is no memory-write hook to bolt sanitization onto (the per-write pressure nudge was
removed 2026-10-16); sanitize in the reader.
