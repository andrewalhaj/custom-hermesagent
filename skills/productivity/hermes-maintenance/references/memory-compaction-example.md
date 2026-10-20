# Memory Compaction Example

Real example from a session audit (2026-06-01).

## Before

```
Manifest model router configured at http://localhost:2099 (admin@example.com / manifest-admin-2026). Agent "Hermes Default" with API key mnfst_k5PBp1vUl1p3jOgv_midR8EM-xlwtolxmsJhdn_eYIk. Providers: DeepSeek + Anthropic. All complexity tiers route to deepseek-v4-pro. Reasoning tier has Opus fallback (claude-opus-4-8). Custom header route x-manifest-tier:opus triggers Opus directly. Delegation goes direct to DeepSeek (bypasses Manifest). Executor profile at ~/.hermes/profiles/executor/ for cheap background work. Daily delegation audit cron job 840045b799b8 at 9AM UTC.
```
486 chars.

Problems:
- Admin credentials (`admin@example.com / manifest-admin-2026`) already in manifest-router skill
- Full API key (`mnfst_...`) already in config.yaml and manifest-router skill
- "Daily delegation audit cron job 840045b799b8" — this is an ID that will be stale within days

## After

```
Manifest at localhost:2099. All tiers → deepseek-v4-pro. Reasoning fallback → claude-opus-4-8. Header route x-manifest-tier:opus triggers Opus. Delegation bypasses Manifest → direct DeepSeek. Executor profile at ~/.hermes/profiles/executor/.
```
~245 chars. Same information density, no credentials, no stale IDs.

## Result

486 → 245 chars (50% reduction). Combined with bumping `memory_char_limit` from 2200 → 4000, total utilization dropped from 51% to ~22%.

## Compaction Rules

1. Strip credentials that exist elsewhere (skills, config, .env)
2. Drop IDs that will be stale in a week (cron job IDs, PR numbers, commit SHAs)
3. Use arrows (→) for routing relationships — more compact than prose
4. Remove redundant qualifiers ("model router", "configured at", "Agent \"Hermes Default\"")
5. Keep the operational facts: what routes where, what falls back to what
