# Honcho Injected Memory Block — Why Dirty Facts Persist (Source-Verified)

> **⚠️ HISTORICAL — Honcho was fully removed from Hermes (memory_provider=supabase, 2026-07-01).**
> This document describes the removed Honcho plugin's injection mechanism. Kept as a
> reference for debugging injected-memory-block issues generally; none of the paths below are live.

When the per-turn injected memory block keeps surfacing the SAME confabulated
user facts every turn — even AFTER you've overwritten the curated peer card and
planted `honcho_conclude` negations — it is NOT "stale lag that reconverges."
It is **active server-side re-derivation from an immutable observation log,
injected via a path that reads a DIFFERENT peer than the one you cleaned.**
This was verified by reading the hermes-agent source (June 2026), not theorized.

## The injected block has 5 separately-sourced fields

Builder: `plugins/memory/honcho/__init__.py::_format_first_turn_context` (~line 549).
It assembles the block from a `ctx` dict with these keys, each its own object:

1. `## Session Summary`        ← `ctx["summary"]`           (session-scoped, fine)
2. `## User Representation`    ← `ctx["representation"]`    **DIRTY** — dialectic dump
3. `## User Peer Card`         ← `ctx["card"]`              **DIRTY** — dialectic-derived card
4. `## AI Self-Representation` ← `ctx["ai_representation"]` (AI observations)
5. `## AI Identity Card`       ← `ctx["ai_card"]`           CLEAN (curated AI card)

## Why the AI card is clean but the User card stays dirty

`prefetch_context` (`plugins/memory/honcho/session.py` ~line 732):

```python
user_ctx = self._fetch_peer_context(session.user_peer_id, ...)   # user_peer_id = "root"
result["representation"] = user_ctx["representation"]            # dialectic dump
result["card"]           = user_ctx["card"]                      # dialectic-DERIVED card
ai_ctx = self._fetch_peer_context(session.assistant_peer_id, ...)
result["ai_card"]        = ai_ctx["card"]                        # curated AI card → clean
```

- `session.user_peer_id` resolves to **`root`** (from `honcho.json` host block:
  `"peerName": "root"`). BOTH the user `representation` AND the user `card` are
  pulled from the `root`-targeted dialectic context — server-DERIVED from the
  observation log, not your curated card.
- The clean curated user card lives on peer **`<telegram-user-id>`** (the operator's
  Telegram ID). **This injection path never reads it.** `honcho_profile(peer="<telegram-user-id>")`
  (interactive) reads it fine — which is why on-demand reads are clean while the
  injected block is dirty. Two different objects, two different peers.
- The AI card reads clean because `assistant_peer_id` ("hermes") IS the peer whose
  curated card you overwrote.

## Why card-overwrite + honcho_conclude don't stop it

The dirty `representation` and `card` are RE-COMPUTED every turn from deductive/
inductive **observations** (e.g. 2026-06: "seeking RTX 5080 for 3ds Max" →
"3D-visualization professional"; smart-home entities → "kids Ellie/Jasper").
Honcho v3 has **NO delete-observation and NO delete-message API** (verified against
`GET https://api.honcho.dev/openapi.json`). `honcho_conclude` only ADDS
counter-evidence; it cannot remove the source premises. So the generator keeps
producing the dirty derivation regardless of how clean the curated card is.
You have been fixing DOWNSTREAM of the generator.

## The real fixes (in order of cleanliness)

- **Option A — `recall_mode: hybrid → tools`** (config, gated, instant rollback).
  `recall_mode` ∈ {`context`, `tools`, `hybrid`} (default `hybrid`).
  `tools` = NO auto-injection at all; fetch user context on demand via
  `honcho_profile`/`honcho_search` (which read the CLEAN curated card at <telegram-user-id>).
  Least invasive real fix. Cost: loses the clean summary + AI card from auto-inject too.
  Key likely under `memory.recall_mode` — VERIFY the exact path before `hermes config set`.

- **Option B — surgical source patch** to `_format_first_turn_context`: skip BOTH
  dirty user-side fields (`representation` AND `card`), keep `summary` + `ai_card`.
  IMPORTANT: dropping ONLY `representation` is INSUFFICIENT — the `## User Peer Card`
  is ALSO dialectic-derived/dirty (verified above). Must drop both. This is a
  CORE-FILE edit → reverted by `hermes update`; re-apply by hand after each update
  (patch_guard retired 2026-10-16, no self-heal).
  Alternative deeper B: re-point the user fetch from `root` to peer `<telegram-user-id>`
  so the CLEAN curated card injects instead of nothing.

- **Option C — kill the dialectic at source** via `honcho.json` `dialecticReasoningLevel`
  / cadence. Least certain: a server-side dashboard override can win on session init,
  so a local JSON edit may not stick. Read the file first; the lever may be app.honcho.dev.

## Standing guardrail that works regardless of fix

The "Honcho Drift Correction" cron keeps the CURATED card clean (it does not touch
the injected representation). Pin the operator peer EXPLICITLY (`<telegram-user-id>`), never
the `peer="user"` alias — in an isolated/cron session the alias mis-resolves to the
EMPTY `root` peer (see honcho-peer-topology.md). Blocklist of known-false terms +
known-TRUE exclusions lives at `~/.hermes/references/honcho-confabulation-blocklist.md`.
