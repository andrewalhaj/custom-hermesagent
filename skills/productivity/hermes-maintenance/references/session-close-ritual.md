# Session Close Ritual

When the user signals end-of-session (or explicitly asks for a changelog), produce three artifacts:

## 1. Changelog

Path: `Obsidian Vault/hermes-memories/changelogs/YYYY-MM-DD.md`

**Scope rule — changelogs are limited to changes made to Hermes itself**, not session narrative. The user does not want a transcript of what was discussed. Include only:

- Config changes (memory provider, model routing, limits, tool toggles)
- Skills created, patched, or deleted
- Cron jobs created, removed, paused, or modified
- Scripts added or changed under `~/.hermes/scripts/`
- Memory store changes (entries added, replaced, removed, compacted)
- Profile changes (created, deleted, renamed)
- Gateway/platform changes (new platforms connected, tokens added)

**Do NOT include:**
- "We discussed X, then I explained Y, then the user asked Z"
- Research findings, outage investigations, or information lookups
- Troubleshooting narratives unless they resulted in a permanent change to Hermes
- "User asked about Firecrawl → I explained how it works" — that's a conversation, not a change

Format:
- **Overview table** with total sessions, user, model
- **Changes by category** (Config, Skills, Cron, Scripts, Memory, Profiles, Gateway)
- Each change: what was modified, why, and the resulting state
- One-line summary at bottom

## 2. State Snapshot

Path: `Obsidian Vault/hermes-memories/snapshots/YYYY-MM-DD-state.md`

Match the format of existing snapshots. Sections:
- Memory System (provider, hot store contents with char count)
- Honcho Configuration (if active)
- Cron Jobs (table with ID, name, schedule, profile, skills)
- Profiles (table with name, model, gateway, purpose)
- Model Routing (provider, tiers, fallbacks)
- Gateway Platforms (status)
- Vault Structure (tree diagram)
- Installed Skills (custom only)
- Scripts (table)
- Key Config Values (table)
- Session Summary (bullet points)

## 3. Backup

Path: `Obsidian Vault/hermes-memories/backups/hermes-backup-YYYY-MM-DD.tar.gz`

Contents: `config.yaml`, provider config (honcho.json, etc.), `memories/MEMORY.md`, `skills/` directory, and any custom scripts from `~/.hermes/scripts/`.

Command:
```bash
tar -czf "$BACKUP_FILE" -C ~/.hermes config.yaml honcho.json memories/MEMORY.md skills/ scripts/
```

## Trigger

User phrases like "create a changelog", "take a snapshot", "create a backup", or "summarize today's sessions" at session end. All three artifacts should be produced together — they're interdependent (changelog references snapshot structure, backup captures the state the snapshot describes).
