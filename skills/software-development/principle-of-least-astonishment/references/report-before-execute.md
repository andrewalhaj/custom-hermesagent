# Report-Before-Execute Protocol

Captured 2026-06-04 after AGENTS.md consolidation was executed without presenting a report first. The user rolled back and enforced: "Report of changes should be listed to me prior to any changes being done."

## The Protocol

For any file write, config edit, or infrastructure change:

1. **Investigate** — read live state, understand what exists
2. **Write the report** — exact format:

```
Planned: <what changes, where, why>
Default: <line count / change summary>
HAJarvis: <line count / change summary>
Backups: <file paths of .prev copies>
Rollback: <exact revert command>
```

3. **Present the report** — do NOT execute
4. **Wait for explicit greenlight** — "proceed" or equivalent
5. **Create backup** — `cp <path> <path>.prev-$(date +%s)`
6. **Execute** — write/patch the files
7. **Verify** — `grep`/`wc -l`/`diff` to confirm the change

## Profile-Specific Verification

When making changes that affect multiple profiles:

- Never copy-paste the same text block between profiles without adapting paths
- "read AGENTS.md" in ha-bot SOUL.md must reference `~/.hermes/profiles/ha-bot/AGENTS.md`, not the default `~/.hermes/AGENTS.md`
- After writing, verify each profile's file against its own environment

## Failure Mode

The AGENTS.md consolidation was written directly to both files without:
- Presenting a report (step 2-4 skipped)
- Creating backups (step 5 skipped)
- The user discovered the changes post-hoc and had to roll back
- No pre-change backups existed — reconstruction was based on the consolidation plan, not byte-identical restore

## Integration

This protocol is reinforced in three layers:
- **SOUL.md "Before acting" section** — fires every message
- **POLA skill** — governs the "don't surprise" principle
- **AGENTS.md greenlight threshold** — operational detail
