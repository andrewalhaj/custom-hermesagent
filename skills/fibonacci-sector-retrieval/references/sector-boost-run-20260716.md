# Sector Boost Run — 2026-07-16

## Outcome snapshot

- Store: 218 → 198 facts (20 deleted; full-row backup w/ vectors at
  `~/backups/knowledge-store/sector-boost-removals-20260716.json`, 675 KB)
- CLI-garbage check: 0. Flags verified (SECTOR_BOOST + GRAPH_TRAVERSE) on
  hermes-gateway + hermes-kb-daemon.
- 17 unclassified remaining — all legitimate centroid abstentions, left alone per skill.
- Benchmark launched background+notify per skill mechanics.

## Junk found: heartbeat leakage POST-dating the 07-09 filter fix

12 of the 20 junk rows were stored AFTER the 2026-07-09 `is_junk_fact()` fix.
Root cause (verified by reading `session_capture.py`): the strong checks
(`_CONVERSATIONAL` narration, questions, role tags, dangling endings) lived only in
`_is_noise()` — the extract path — while the shared `is_junk_fact()` that guards the
heartbeat→store() path only had deictic/mid-thought/transient/narrow-secrets checks.
The file also had `_is_noise()` DEFINED TWICE (first definition dead code).

New junk classes the 07-09 patterns missed (all now covered, see below):
- Second-person offers: "Never mirrored, say the word if you want me to dig harder."
- Deictic beyond "These/It": "That tells you whether…", "So a clone won't…"
- First-person diagnostic narration: "I pulled the SRL-T source: …"
- Conditional user-guidance: "If the file *is* exactly at that path … then …"
- Ephemeral preview URLs: "Live for a flip-through: http://…"
- **Secrets as env-var assignments** — LINEAR_WEBHOOK_SECRET with value leaked 3×.
- 8 duplicate session-digest rows (`session-digest` + `active-session`/`compaction-flush`
  tags, near-identical text, "unknown-date" variants).

## Root fix (SHIPPED 2026-07-16 — no longer a standing candidate)

Delegated to Claude Code with a brief at `~/.hermes/run/junk-filter-brief.md` containing
a **must-reject corpus** (the 12 actual leaked rows) and a **must-pass corpus** (8 real
durable facts from the store, incl. tricky cases: fact starting with "The", SESSION_SECRET
mentioned without assignment, "password reset" in prose). Result:

- Duplicate `_is_noise()` removed; `is_junk_fact()` now also runs `_CONVERSATIONAL`,
  `_CONDITIONAL_GUIDANCE` (`^If the/that/it … then …`), `_LIVE_URL` (`^Live at/for … http`),
  extended deictic ("That tells", "So a/an"), and a generic secret-assignment pattern:
  `\w*(SECRET|TOKEN|PASSWD|PASSWORD|API_?KEY|PRIVATE_?KEY)\w* = <value>` (placeholder-safe:
  skips `***`/`<…>` values; variable NAME alone in prose still passes).
- Test suite: `~/.hermes/scripts/tests/test_junk_filter.py` — 20 parametrized cases,
  both corpora, all green. RE-RUN THIS after any future filter edit.
- Backup: `session_capture.py.bak-20260716`.
- Deployment: heartbeat is a cron job (fresh process per tick) → new module picked up
  automatically, no restart. The supabase plugin `_is_junk()` already imports the shared
  `is_junk_fact` — both write paths covered.
- Claude Code run exited `error_max_turns` exit 1 with ALL work complete (known pitfall:
  reconcile on-disk, don't re-dispatch).

**Corpus-driven brief pattern worked well:** pinning the fix to the exact leaked rows
(must-reject) + real store facts (must-pass) as a pytest contract prevents both
under-fixing and overcorrection. Reuse for any future filter hardening.

## Mechanics notes

- Backup script env keys: `.env` has `SUPABASE_URL` + **`SUPABASE_SERVICE_KEY`**
  (NOT `SUPABASE_SERVICE_ROLE_KEY` — assuming the standard name costs a failed run).
- Backup-then-delete via a small Python script reading `.env` directly
  (`~/backups/knowledge-store/backup_removals_20260716.py` — reusable) avoids the
  MCP tool-result cap on vector-inclusive row dumps.
- Dup session-digests: same session captured under both `active-session` and
  `compaction-flush` tags → keep at most the newest dated one, delete the rest.
