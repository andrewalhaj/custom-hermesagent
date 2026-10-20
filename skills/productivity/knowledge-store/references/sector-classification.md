# Fibonacci Sector Classification — Full Tree

Deployed 2026-06-25. 152 facts classified into a 4-level Fibonacci-weighted tree.
Column: `knowledge.sector_path` (jsonb array).

## Sector Tree

```
infrastructure (F5=+5)
  ├── hosting
  │   ├── deploy (47 facts) — hermes-dispatcher, dashboard, docker, systemd
  │   ├── configure (15 facts) — config.yaml, gateway, provider, profiles
  │   └── monitor (13 facts) — LH01, LH01, VPS-B, servers
  ├── networking
  │   ├── tailscale (0 facts — merged into monitor)
  │   └── tunnel (2 facts) — Cloudflare, cloudflared
  └── container
      └── docker (0 facts — merged into deploy)

development (F5=+5)
  ├── coder-fleet
  │   └── dispatch (7 facts) — coder, coder-b, coding agents
  ├── webui
  │   └── build (3 facts) — WebUI, React/Vite, TypeScript SPA
  └── knowledge
      ├── search (15 facts) — pgvector, embedding, knowledge store
      ├── graph (0 facts — merged into search via Neo4j tags)
      └── store (1 fact) — offload, chunk, body_hash

monitoring (F5=+5)
  ├── sentry
  │   ├── alert (19 facts) — Sentry error reports
  │   └── pipeline (1 fact) — Sentry→Linear, HMAC, traces_sample
  ├── linear
  │   └── issue (5 facts) — Linear, Hermesjarvis, 38a0c106
  └── webhooks
      └── dispatch (3 facts) — webhooks, hooks.py, pg_net

home-automation (F5=+5)
  ├── dashboard
  │   └── display (0 facts — merged into infrastructure/hosting/deploy)
  └── ha (0 facts — merged)

user (F5=+5)
  ├── preferences
  │   └── workflow (1 fact) — communication, tone, style
  └── hardware (0 facts — merged)
```

## Fibonacci Weights

| Level | Fibonacci | Weight at query time |
|---|---|---|
| Domain (F5) | 5 | +0.75 (×0.15 scaling) |
| Capability (F4) | 3 | +0.45 |
| Process (F3) | 2 | +0.30 |
| Document (F2) | 1 | +0.15 |
| **Full match** | **18** | **×3.70** |
| Domain only | 5 | ×1.75 |
| No match | 0 | ×1.00 (neutral) |

Scaling factor 0.15 chosen so full match = 3.70× (weights [8,5,3,2]; significant but not overwhelming). Empirically verified: score magnitude jumps from 0.015 (RRF mush) to 0.20–0.32 (sector-weighted).

## Query Detection

`_detect_query_sector()` uses keyword rules matching the classification vocabulary. Priority: more specific matches first (e.g. "sentry"+"integration" → pipeline, not alert). Returns `[]` for unclassifiable queries (gets 1.0× neutral weight on all facts).

## Classification Rules

Rule-based (tag + text pattern) deployed via SQL UPDATE batches. Tags take priority over text patterns. Facts matched by the first rule that fires — order matters: specific patterns first, broad patterns last. Unclassified facts (20/152, mostly session transcripts) remain `[]` and get 1.0× neutral weight.

## Pitfalls

- **Classification order matters in SQL UPDATEs.** A broad rule like `text ILIKE '%infrastructure%'` will capture facts that should be in monitoring/sentry if it runs first. Always run specific-domain rules before generic ones.
- **Alias-enriched facts need reclassification.** After DELETE+RE-STORE with enriched text, the new fact has no sector_path. Re-run classification.
- **Query detection is string-based, not semantic.** A query like "how many coding agents" → development/coder-fleet. But "how many workers" → unclassified. Keep detection keywords in sync with stored text vocabulary.
