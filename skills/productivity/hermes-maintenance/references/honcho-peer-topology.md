# Honcho Peer / Workspace Topology + the Cron Peer-Binding Bug

Verified live via the Honcho SDK (June 2026). Use this whenever a cron job,
detached session, or isolated process needs to read/write the operator's Honcho
card — the `peer="user"` alias is NOT reliable outside an interactive session.

## Live topology (workspace `hermes`)

- **Workspace:** `hermes` — NOT `default`. An SDK client that defaults to
  `default` finds **0 peers** and looks broken. Always pass `workspace_id="hermes"`.
- **Operator/user peer:** `<telegram-user-id>` (the operator's Telegram ID) — holds the
  REAL curated card (~22 facts / 26 conclusions). Pin THIS explicitly.
- **AI peer:** `hermes` — holds the AI Identity Card.
- **`root` peer:** EMPTY. The `peer="user"` alias mis-resolves to `root` in
  isolated/cron sessions (the `honcho.json` host block sets `"peerName": "root"`),
  so a cron reads an empty card and — if naive — concludes "clean" when it simply
  read the WRONG peer.
- Other peers: a Discord peer (`<discord-channel-id>`, ~10 facts), `ha-bot`,
  `<telegram-chat-id>` (cron channel) — all empty/decoy.
- Separate workspace `hermes_swarm-verifier` has its own ephemeral
  `user-default-*` peers — unrelated to the operator card.

## SDK enumeration recipe (read-only)

```python
import os
from honcho import Honcho
# Key from ~/.hermes/.env (HONCHO_API_KEY). Source it: `set -a; source ~/.hermes/.env; set +a`
h = Honcho(api_key=os.environ["HONCHO_API_KEY"])
for w in h.workspaces():                       # h.workspaces(), h.peers(), h.peer(id) are the real API
    hw = Honcho(api_key=os.environ["HONCHO_API_KEY"], workspace_id=w.id)
    for p in hw.peers():
        pr = hw.peer(p.id)
        card = pr.get_card()                   # get_card() — legacy .card() also exists
        print(w.id, p.id, len(card) if isinstance(card, list) else card)
```
Gotchas: `Honcho()` raises "Missing API key" if env not sourced. `h.get_peers()`
does NOT exist — it is `h.peers()`. `~/.honcho/config.json` may not exist; the
workspace lives in `honcho.json` host blocks, not that file.

## The cron peer-binding fix

In any cron/isolated prompt that inspects the operator card, address the peer by
the EXPLICIT id, never the alias:

- WRONG: `honcho_profile(peer="user")`  → resolves to empty `root` in cron
- RIGHT: `honcho_profile(peer="<telegram-user-id>")` → the real curated card

## Fail-loud rule (empty ≠ clean)

A drift/audit cron that reads a card MUST distinguish "card is populated and clean"
from "card read returned empty/None." An empty read is a READ FAILURE (wrong peer,
auth, resolution bug), NOT a clean card. On empty: alert the Cron channel
("could not read a populated card — manual check needed") and STOP; do not write,
do not report CLEAN. The known operator baseline is ~22 facts — anything near 0 is
anomalous. This caught a real silent false-negative this session.
