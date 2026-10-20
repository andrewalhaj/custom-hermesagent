# Honcho injected-block drift: suppress at the formatter, protect with surgical self-heal

The definitive fix (June 2026) for the recurring dirty User Peer Card that scoped
`honcho_conclude` negations and curated-card overwrites could NOT stop.

## Root cause (verified in source)

The per-turn injected memory block is built by:
`plugins/memory/honcho/__init__.py :: _format_first_turn_context(ctx)` — appends five
fields when present:
- `## Session Summary`        ← `ctx["summary"]`        (useful)
- `## User Representation`     ← `ctx["representation"]` (DIRTY — observation dump)
- `## User Peer Card`          ← `ctx["card"]`           (DIRTY — dialectic-derived card)
- `## AI Self-Representation`  ← `ctx["ai_representation"]`
- `## AI Identity Card`        ← `ctx["ai_card"]`        (CLEAN — curated AI card)

In `session.py :: prefetch_context` → `_fetch_peer_context(session.user_peer_id, ...)`:
`session.user_peer_id` resolves to the **directional `root` peer** (`honcho.json` →
`"peerName": "root"`). Both `representation` and `card` come from that peer's dialectic
context — server-RE-DERIVED every turn from the observation log. There is NO
delete-observation API (Honcho v3), so `honcho_conclude` only adds counter-evidence and
the dialectic keeps regenerating the dirty card. The curated card you overwrite lives on
the OPERATOR peer (the Telegram-ID peer), which THIS injection path never reads — which is
exactly why the AI Identity Card shows clean while the User Peer Card stays dirty.

## Verified peer topology (via SDK, workspace `hermes`)

A bare `Honcho()` client defaults to workspace `default` and finds **0 peers** — wrong
workspace. Enumerate correctly:
```python
import os; from honcho import Honcho
key = open(os.path.expanduser('~/.hermes/.env')).read()  # grep HONCHO_API_KEY
h = Honcho(api_key=key)
for w in h.workspaces():
    hw = Honcho(api_key=key, workspace_id=w.id)
    for p in hw.peers():
        pr = hw.peer(p.id); card = pr.get_card()
        print(w.id, p.id, len(card) if isinstance(card, list) else card)
```
Result for this host:
- workspace **`hermes`** (the real one)
  - peer **`<telegram-user-id>`** (operator/Telegram ID) → ~22-26 facts  ← the real curated card
  - peer **`hermes`** (AI) → ~22 facts
  - peer **`root`** → empty   ← what `peer="user"` mis-resolves to in isolated/cron sessions
  - peer `<discord-channel-id>` (Discord) → ~10 facts; `ha-bot`, `<telegram-chat-id>` → empty
- workspace `hermes_swarm-verifier` — ephemeral `user-default-*` peers, unrelated.

**Lesson:** in any isolated/cron session, `peer="user"` is unreliable. Pin the explicit
operator peer ID. The drift-correction cron must (a) pin that ID and (b) FAIL-LOUD on an
empty read (empty ≠ clean — alert, don't conclude clean).

## The fix — comment out the two dirty appends

In `_format_first_turn_context`, replace the `rep`/`card` blocks with a marker-tagged
comment (keep summary + AI rep + AI card):
```python
# HERMES-PATCH drift-suppression: user-side representation/card are the server-side
# DIALECTIC objects (directional `root` peer), re-derived every turn from an undeletable
# observation log. Dropped from per-turn injection; clean curated card read on demand via
# honcho_profile(peer="<telegram-user-id>").
# rep = ctx.get("representation", "")   # DROPPED
# card = ctx.get("card", "")            # DROPPED
ai_rep = ctx.get("ai_representation", "")
```
Backup the file first; `ast.parse` to confirm; reload via the detached gateway-restart
pattern (see `references/gateway-restart-deadlock.md`). Verify next turn: the injected
block shows ONLY Session Summary + AI Self-Representation + AI Identity Card — no User
Representation, no dirty User Peer Card. On-demand `honcho_profile(peer="<telegram-user-id>")`
still returns the clean curated card when user facts are actually needed.

Why not `recall_mode: tools`: that config knob kills ALL injection (loses the clean
summary + AI card too). The formatter patch is surgical — drops only the dirty fields.

## Durability — surgical re-apply in patch-guard (NOT whole-file restore)

(patch_guard retired 2026-10-16; `_heal_honcho_format` was excised 2026-08-14 — historical.)
`honcho/__init__.py` is a ~61KB UPSTREAM module `hermes update` legitimately rewrites, so
a whole-file golden restore would clobber upstream changes. Added `_heal_honcho_format()`
to `scripts/patch_guard.py`:
- marker `HERMES-PATCH drift-suppression`; if present → silent/healthy.
- if marker missing AND the exact `_TARGET` block is present → `src.replace(_TARGET,
  _REPLACEMENT, 1)`, backup `.bak-<ts>-driftheal`, write, `ast.parse` validate, report.
- if marker missing AND `_TARGET` not found → upstream refactored; append a `problems[]`
  line telling the human to re-port MANUALLY. Never clobber.
Tested before trusting: ran the guard against live healthy state (silent, exit 0), then
simulated drift on a `/tmp` copy seeded from the pre-patch backup and confirmed it
re-applied, restored the marker, dropped the dirty append, and kept AST valid.
