# Honcho CLI Wrappers for Cron Jobs

When a Hermes cron job needs to call `honcho_profile` or `honcho_conclude` from a shell script, use these wrapper scripts. Honcho is a Python library, not a CLI binary — the scripts bridge the gap.

## The wrappers

### `/usr/local/bin/honcho_profile`

Reads a peer's card (curated facts) from Honcho. Uses honcho.json for workspace/peer config.

```bash
honcho_profile                  # reads the user peer card (from honcho.json peerName)
honcho_profile --peer ai        # reads the ai peer card
honcho_profile --raw            # JSON output for script consumption
```

### `/usr/local/bin/honcho_conclude`

Writes a conclusion to Honcho. Uses honcho.json for workspace/peer config.

```bash
honcho_conclude "fact text here"
honcho_conclude --peer ai "fact text here"
honcho_conclude --delete <conclusion-id>
echo "fact" | honcho_conclude   # reads from stdin
```

## Key API details (reverse-engineered from Honcho SDK v3)

- **Base URL:** `https://api.honcho.dev` (production default, override with `HONCHO_URL` env var)
- **Config:** Read from `~/.hermes/honcho.json` — `apiKey`, `hosts.<profile>.workspace`, `hosts.<profile>.peerName`, `hosts.<profile>.aiPeer`
- **Constructor:** `Honcho(api_key=key, workspace_id=workspace)` — workspace_id is how Honcho scopes peer lookups
- **Peer access:** `honcho.peer("peer-name")` returns a Peer object
- **Card read:** `peer.get_card()` (not the deprecated `card()`) — returns a list of conclusion content strings
- **Conclusion write:** `peer.conclusions.create([{"content": "text"}])` — takes a LIST of dicts with `content` key, NOT keyword args
- **Conclusion delete:** `peer.conclusions.delete(conclusion_id)`
- **Available peers are profile-specific:** default profile uses peerName "root" (user) and aiPeer "hermes" (ai)

## Pitfalls

- **Missing workspace:** `Honcho(api_key=...)` without `workspace_id` defaults to "default" workspace — peers won't be found. Always pass `workspace_id` from honcho.json.
- **Wrong peer name:** The peer names are "root" and "hermes" in the Hermes profile, NOT "user" and "ai". Read from `honcho.json` → `hosts.<profile>.peerName` / `aiPeer`.
- **`create()` takes a list, not keywords:** `conclusions.create(text="x")` fails. Correct: `conclusions.create([{"content": "x"}])`.
- **`card()` is deprecated:** Use `get_card()`.
