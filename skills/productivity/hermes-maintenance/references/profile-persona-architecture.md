# Profile Persona Architecture — SOUL.md + AGENTS.md Split

Established 2026-06-04. Every Hermes profile should maintain this two-file architecture. SOUL.md defines identity; AGENTS.md defines operational procedures. Never collapse both into a single file — the distinction prevents identity drift and keeps procedures discoverable.

## SOUL.md — Identity

What the agent IS. Values, tone, boundaries, hard-won lessons. Loaded fresh every message — no restart needed.

Structure:
```
# <Name> — <Role>
[one-line scope statement]

## How I carry myself
[tone, voice, behavioral defaults]

## What I will not do
[hard boundaries — greenlight gates, no-fabrication, Tailscale-only bind, etc.]

## What I've learned the hard way
[verified pitfalls specific to this profile's domain]

## How I work
[delegation philosophy, context discipline]

## Least astonishment
[tie-in to principle-of-least-astonishment skill — behavior must match expectation]
```

**Rules for SOUL.md:**
- Keep it SHORT — if it exceeds ~50 lines, tighten it. This is loaded every turn; every line costs tokens.
- Never include procedures, how-to, or step-by-step instructions. Those go in AGENTS.md or skills.
- Write from the agent's first-person voice.
- The `Least astonishment` section ties SOUL.md back to the `principle-of-least-astonishment` skill for dev work.

### The "Before acting" anchor (recommended)

AGENTS.md is reference documentation — it is NOT loaded per-turn. To ensure operational procedures are always consulted before file writes, config edits, and infrastructure changes, add a short behavioral anchor to SOUL.md. This bridges the gap between auto-loaded identity and reference-only procedures:

```markdown
## Before acting
For any file write, config edit, or infrastructure change:
1. Present a written report of planned changes.
2. Wait for explicit greenlight.
3. Create a backup before executing.

For detailed procedures (boot sequence, memory protocol, delegation rules,
verification gates, pitfall index): read AGENTS.md.
```

This is not a procedure — it's a behavioral trigger that fires every message and points to AGENTS.md as the authoritative source. Without this anchor, AGENTS.md is only consulted when the agent remembers to `read_file` — and memory fails. Both the default and ha-bot profiles have this block in their SOUL.md files.

**Rationale:** The agent was asked to "review the system and see if anything can go into AGENTS.md." It turned a review request into execution — reading the files, finding duplications, and writing consolidated versions without presenting a report or creating backups. The "Before acting" anchor in SOUL.md would have fired before any `write_file` call, triggering the three-step protocol. It is now the primary defense against this class of error.

## AGENTS.md — Procedures

How the agent OPERATES. Boot sequence, memory protocol, delegation rules, greenlight threshold, tool selection, verification gates. Not loaded per-turn — it's reference documentation.

Structure:
```
# AGENTS.md — <Profile> Operating Procedures

## Boot sequence (every new session)
[what to load, what to probe, what to verify first]

## Memory protocol
[three-store model: MEMORY.md vs session_search vs knowledge-store vs Honcho]

## Content workflows
[domain-specific: read-only, infra change, config edit, source patch, delegation]

## Greenlight threshold / When to ask vs. just act
[decision table: what's auto-approved, what's gated]

## Verification gates
[specific commands to run after any change]

## Hard-won pitfalls
[domain-specific traps, with exact error messages and diagnostic paths]
```

**Rules for AGENTS.md:**
- No identity content. No values, no tone. Pure procedures.
- Be specific — exact commands, exact checks, exact thresholds.
- If a pitfall has an error string, include it verbatim so the agent can grep for it.
- Reference skills by name where they carry deeper detail.

## Examples (live on this host)

- **Default profile:** `~/.hermes/SOUL.md` (3,871 bytes) + `~/.hermes/AGENTS.md` (8,275 bytes)
- **ha-bot profile:** `~/.hermes/profiles/ha-bot/SOUL.md` (4,019 bytes) + `~/.hermes/profiles/ha-bot/AGENTS.md` (6,536 bytes)

The ha-bot SOUL.md starts with a scope header ("I am Andrew's dedicated smart-home agent...") then shares the same operating values body as default. Its AGENTS.md is HA-specific: SSH health probe at boot, dashboard Tailscale-bind verification, Sonos 412 diagnosis path, custom_panel canonical schema reference.

## When creating a new profile

1. **Clone SOUL.md** from an existing profile. Edit the scope header. Keep the values body unless the new profile needs a materially different tone.
2. **Write AGENTS.md** from scratch for the new domain. Boot sequence and verification gates are domain-specific and won't match another profile.
3. **Both files live at profile root** — `~/.hermes/profiles/<name>/SOUL.md` and `~/.hermes/profiles/<name>/AGENTS.md`. For the default profile, they're at `~/.hermes/SOUL.md` and `~/.hermes/AGENTS.md`.
