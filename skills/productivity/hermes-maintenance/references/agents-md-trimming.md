# AGENTS.md Trimming — Principles & Safe Cuts

AGENTS.md is an operational config file — it governs behavior, so trimming it safely means distinguishing **load-bearing gates** from **reference cruft** that duplicates what's already in the system prompt, memory, or skills.

## Goal

Reduce token load if AGENTS.md is ever auto-loaded, without degrading behavioral guardrails.

## What's safe to collapse or drop

### 1. Duplicated content
Two greenlight tables, two "when to ask" sections, two memory protocols — pick the better version, keep it once, drop the other.

### 2. Tool-selection instructions
Agents at this level know when to use `read_file` vs `cat`, `search_files` vs `grep`, `patch` vs `sed`. The training data covers this. Drop the tool-selection section entirely.

### 3. Cron job tables
Already in memory and in `cronjob(action='list')`. Listing them in AGENTS.md duplicates state that drifts.

### 4. Integration troubleshooting prose
Long diagnostic narratives ("if X then Y then Z" chains) can compress to a template: one-line symptom, pointer to the skill that handles it.

### 5. Reference material already in system prompt
The system prompt already tells the agent what tools it has, what its identity is, and what skills are available. AGENTS.md should not repeat any of that.

## What MUST be preserved (unedited)

| Section | Why |
|---|---|
| PRE-TASK RECALL GATE | Prevents re-deriving known diagnoses across sessions |
| WRITE GATE | Enforces report→greenlight→execute for config mutations |
| Delegation triggers | Hard rules for when to hand off (no model has these baked in) |
| Verification protocol | "Run the command that proves it" — uniquely this agent's voice |
| Boot sequence | MEMORY.md block check is not in training data |
| Any behavioral gate with `⚠️` or "hard rule" | These are surgical corrections, not general knowledge |

## What to compress (keep, but shorter)

- Content workflows table: merge with greenlight thresholds into one compact table
- Memory protocol: 5 lines max — store names, what goes where
- "When to ask vs act" prose: collapse to the table, drop the narrative

## Verification after trim

```bash
# Byte/line counts
wc -lc AGENTS.md AGENTS.md.prev-trim

# Confirm gates present
grep -c 'PRE-TASK RECALL GATE\|WRITE GATE\|compliance-check\|Delegation rules\|Verification protocol' AGENTS.md
# Expect: 4 (PRE-TASK + WRITE GATE + Delegation + Verification)
```

## Example: Default + HA-Bot (2026-06-05)

| | Before | After | Reduction |
|---|---|---|---|
| Default | 159 lines / 8,559 B | 76 lines / 3,742 B | 56% |
| HA-Bot | 139 lines / 9,320 B | 115 lines / 5,873 B | 37% |
| Combined | 17,879 B | 9,615 B | 46% |

HA-Bot kept more because its domain-specific pitfalls (integration troubleshooting template, HA-specific verification rules) are genuinely unique and don't exist elsewhere in the system prompt. The trim was asymmetric by design — don't force equal reduction across profiles.

## Pitfall

**Never trim skills to move content into AGENTS.md.** The architecture is: SOUL.md (identity, per-message) → AGENTS.md (procedures, on-demand) → skills/ (domain expertise, on-demand). Each layer does its job. Collapsing domain expertise into a catch-all AGENTS.md breaks the on-demand loading model and guarantees drift. Trim AGENTS.md by removing duplicates of what's already in memory/system prompt, NOT by absorbing skills.
