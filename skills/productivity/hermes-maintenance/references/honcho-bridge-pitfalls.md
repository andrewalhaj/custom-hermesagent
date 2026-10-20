# Honcho-to-Obsidian Bridge — Pitfalls (2026-06-10)

## The two bugs that silenced the bridge since creation

The bridge script (`~/.hermes/scripts/honcho-bridge.sh`) was writing `{"detail":"Not Found"}` to both Obsidian vault files every day since it was created. Two stacked bugs:

### Bug 1: Wrong API version (`/v1` → `/v3`)

The script had `HONCHO_API="https://api.honcho.dev/v1"`. The Honcho SDK uses `/v3`. The workspace endpoint at `/v1/workspaces/hermes` returns 404; at `/v3/workspaces` (POST) it returns 200.

**How to verify the correct version:** load the SDK and monkey-patch `httpx.Client.send` to log request URLs:
```python
import sys; sys.path.insert(0,'/usr/local/lib/hermes-agent')
from plugins.memory.honcho.client import HonchoClientConfig, get_honcho_client
import httpx
orig = httpx.Client.send
def cap(self, req, *a, **kw):
    print(req.method, req.url)
    return orig(self, req, *a, **kw)
httpx.Client.send = cap
cfg = HonchoClientConfig.from_global_config()
client = get_honcho_client(cfg)
peer = client.peer('<telegram-user-id>')
card = peer.get_card()
```
The logged URLs show the actual version the SDK uses. Don't guess — always probe.

### Bug 2: Wrong peer ID (`root` → `<telegram-user-id>`)

The script had `USER_PEER="root"`. The SDK config (`honcho.json`) has `peerName: "root"` but that's an alias the SDK resolves internally. The actual peer ID in the Honcho backend is `<telegram-user-id>` (Andrew's Telegram ID).

Proof: `client.peer('root').get_card()` returns `None`; `client.peer('<telegram-user-id>').get_card()` returns the full 24-fact card.

**General rule:** the `peerName` in `honcho.json` is the SDK's local alias for constructing session keys. The Honcho REST API uses the actual peer ID. When scripting direct REST calls (curl or Python outside the SDK), always use the numeric Telegram ID, not the alias.

## Why raw curl against the Honcho REST API is fragile

The bridge was originally written as a bash script using curl. Problems:
1. API version baked into URL — breaks whenever Honcho bumps version
2. peer ID alias vs real ID confusion
3. The `representation` endpoint has a different call signature than a simple GET (it accepts query params for filtering)

**The fix:** rewrite the bridge as a Python script that uses the SDK directly (`exec python3 - <<'PYEOF' ... PYEOF`). The SDK handles version routing and peer resolution automatically.

## Current working implementation

```python
from plugins.memory.honcho.client import HonchoClientConfig, get_honcho_client
cfg = HonchoClientConfig.from_global_config()
client = get_honcho_client(cfg)
peer = client.peer('<telegram-user-id>')

card = peer.get_card()           # list of IDENTITY/ATTRIBUTE/INSTRUCTION strings
rep  = peer.representation()     # full dialectic synthesis as a string
```

The `representation()` method signature (as of 2026-06-10):
```
peer.representation(session=None, target=None, search_query=None,
                    search_top_k=None, search_max_distance=None,
                    include_most_frequent=None, max_conclusions=None) -> str
```
Calling it with no args returns the full user model synthesis.

## The Obsidian vault path

`~/Documents/Obsidian Vault/hermes-memories/honcho/`
- `peer-card.md` — structured JSON of the 24-fact peer card
- `user-model.md` — full dialectic synthesis (133+ lines)

Synced daily at 08:00 UTC by the "Honcho-to-Obsidian Bridge" cron (`no_agent=true`, `deliver=local`).
