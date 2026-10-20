# OAuth token: who refreshes it, and how to share it safely

> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

Verified by reading `/usr/local/lib/hermes-agent/agent/anthropic_adapter.py`
(2026-06-07). This answers two questions that recur whenever the bypass token
is involved: **what keeps it alive**, and **how to let another process use it
without breaking refresh.**

## Who refreshes the bypass token (authoritative)

There is **NO standalone refresher daemon/cron/systemd-timer**. If you go
hunting for one you will find nothing — that's expected, not a bug. Refresh is
**lazy and request-triggered, owned by Hermes itself**:

- `resolve_anthropic_token()` → `_resolve_claude_code_token_from_credentials()`
  runs before every Anthropic request.
- It reads `~/.claude/.credentials.json` (`claudeAiOauth.{accessToken,
  refreshToken,expiresAt}`; `expiresAt` is **epoch ms**), checks validity with a
  **60-second expiry buffer** (`is_claude_code_token_valid`).
- If expired/near-expiry, `_refresh_oauth_token()` POSTs
  `grant_type=refresh_token` (client_id `9d1c250a-e61b-44d9-88ed-5944d1962f5e`)
  to `platform.claude.com/v1/oauth/token` (fallback `console.anthropic.com`).
- On success `_write_claude_code_credentials()` **atomically writes the new
  token back** to `~/.claude/.credentials.json` (temp file at 0600 +
  `os.replace`; preserves `scopes`, which Claude Code >=2.1.81 gates on —
  `user:inference` must be present).

Consequence: whichever Hermes process makes an Anthropic call owns refresh.
The token lives ~50 min; normal Hermes activity keeps it fresh.

## Reusable functions (don't reimplement refresh)

```python
import sys; sys.path.insert(0, "/usr/local/lib/hermes-agent")
from agent.anthropic_adapter import (
    refresh_anthropic_oauth_pure,        # (refresh_token, use_json=False) -> {access_token, refresh_token, expires_at_ms}
    _write_claude_code_credentials,      # (access_token, refresh_token, expires_at_ms) -> writes ~/.claude/.credentials.json
)
```
`refresh_anthropic_oauth_pure` is **pure** (doesn't touch local files);
`_write_claude_code_credentials` persists. Use both to refresh on demand.

## The dual-refresh RACE (the trap when sharing the token)

OAuth refresh tokens are commonly **single-use**: a successful refresh ROTATES
the refresh_token and invalidates the prior one. So if **two independent
refreshers** share one OAuth session (e.g. host Hermes + a container's own
`claude` CLI both reading a shared `~/.claude`), one refresh silently
invalidates the other's refresh_token → the loser is logged out → **silent auth
death mid-task**. This is the classic "fails at 3am" footgun.

## SINGLE-WRITER pattern (the safe way to share the token)

Make exactly one process the writer; everyone else reads.

1. **Host = sole refresher** (it already is — see above). Don't add a second.
2. **Sidecar copy, read-only to the consumer.** Mirror
   `~/.claude/.credentials.json` → a sidecar dir (chown to the consumer's uid,
   0600) and mount it **read-only** into the container at the CLI's expected
   `$HOME/.claude`. Read-only is the enforcement: the consumer **physically
   cannot rotate** the token (`Read-only file system` on write/delete), so the
   race is impossible — not merely discouraged. Verify with a write+delete
   attempt; both must fail.
3. **Keep-warm + mirror cron** (because read-only means the consumer can't
   refresh): a host `no_agent` cron, every 15 min, that:
   - refreshes the canonical token via the reusable functions above if within a
     ~20-min buffer (token life ~50 min → always fresh before any 5-30 min
     consumer task could outlive it),
   - atomically mirrors canonical → sidecar (temp+`os.replace`, chown, 0600),
   - **silent when healthy**, errors → Cron Jobs channel.
   Reference implementation lives at `~/.hermes/scripts/od_token_keepwarm.py`.
   This cron becomes **load-bearing** for long consumer tasks — alert on failure.

Trade-off to state plainly when proposing: the token now exists in a second
on-disk location (root-created, 0600, mounted RO). Flag the expanded footprint.

## OAuth token failure diagnostics (PROVEN 2026-07-21)

When `resolve_anthropic_token()` returns `None` and Anthropic requests fall back
to another provider, trace the refresh chain:

```python
from agent.anthropic_adapter import (
    read_claude_code_credentials,
    is_claude_code_token_valid,
    resolve_anthropic_token,
    refresh_anthropic_oauth_pure,
)
```

### Endpoint status (verified live 2026-07-21)

| Endpoint | Status | Notes |
|---|---|---|
| `platform.claude.com/v1/oauth/token` | Working | May intermittently return Cloudflare 403 (error 1010) — retry; real errors surface behind the WAF flap |
| `console.anthropic.com/v1/oauth/token` | **DEAD (404)** | The adapter's `_OAUTH_TOKEN_URL` constant points here — this is the fallback and it never works |
| `api.anthropic.com/v1/oauth/token` | 400 "Invalid request format" | Doesn't accept the same payload format |

### Error meanings

| Error | Meaning | Fix |
|---|---|---|
| `400 invalid_grant: Refresh token expired` | Refresh token hit absolute lifetime (~30 days). Cannot recover — full re-auth required | `hermes auth add anthropic --type oauth` |
| `403 error code: 1010` | Cloudflare WAF block (intermittent) | Retry; this masks the real error |
| `404 Not Found` | `console.anthropic.com` dead endpoint | This is always dead — ignore, use `platform.claude.com` |
| Token valid but `resolve_anthropic_token()` returns None | Check `ANTHROPIC_TOKEN`/`CLAUDE_CODE_OAUTH_TOKEN` env vars — they shadow credential-file resolution | Unset the env var or ensure it's the refreshed token |

### Re-authentication gotcha (2026-07-21)

### Re-authentication gotcha (2026-07-21)

`hermes auth add anthropic --type oauth` generates a PKCE URL. The token
exchange uses `_OAUTH_TOKEN_URL="he dead (404)`. The `run_hermes_oauth_login_pure()` function in the
adapter has this baked in.

Workaround: after obtaining the authorization code via the browser, exchange it
manually against `platform.claude.com/v1/oauth/token` instead:

```python
data = urllib.parse.urlencode({
    'grant_type': 'authorization_code',
    'client_id': '9d1c250a-e61b-44d9-88ed-5944d1962f5e',
    'code': code,
    'redirect_uri': 'https://console.anthropic.com/oauth/code/callback',
    'code_verifier': verifier,
}).encode()
# POST to platform.claude.com/v1/oauth/token — NOT console.anthropic.com
```

The PKCE verifier must be captured from the session that generated the URL.
When running `hermes auth add` via PTY, the verifier is lost if the PTY times
out — use the web UI OAuth endpoint (`POST /api/providers/oauth/anthropic/start`
on the dispatcher) instead, which persists state server-side.

**Gateway restart required after manual credentials update (PROVEN 2026-07-21):**
the gateway caches credentials at startup. Writing a new `~/.claude/.credentials.json`
does NOT take effect until the gateway restarts. The one exception: the adapter's
lazy `_refresh_oauth_token()` writes back atomically and the in-memory state picks
up the refreshed access token, but a MANUAL edit to the file is invisible. After
any manual credentials update: `systemctl --user restart hermes-gateway.service`
(must be run from outside the gateway — see PITFALL 9 in parent SKILL.md).

### The billing bypass patch is separate from token resolution

`~/.hermes/patches/anthropic_billing_bypass.py` wraps `build_anthropic_kwargs`
to inject Claude Code billing headers and stainless SDK spoof headers. It does
**NOT** handle token resolution — that's `resolve_anthropic_token()` in the
adapter. Both must work for Anthropic requests to succeed on the subscription
tier.

### Managed keys from `claude setup-token` (proven 2026-07-21)

`claude setup-token` produces a **managed key** — no refresh token, no OAuth
grant flow. The bypass's `is_claude_code_token_valid()` returns `False` for these
tokens when `expiresAt` is non-zero and in the past, and `_refresh_oauth_token()`
fails because there's no refresh token.

**Fix:** set `expiresAt` to `0` and `refreshToken` to `""`. The bypass treats
`expiresAt == 0` as a managed key (permanently valid, never refreshes):
```python
d["claudeAiOauth"]["expiresAt"] = 0
d["claudeAiOauth"]["refreshToken"] = ""
```

**Error signatures to recognize:**
- `refresh_token is required` in the error log → managed key being treated as OAuth
- `is_claude_code_token_valid` → `False` but `accessToken` is present
- `resolve_anthropic_token()` → `None` despite valid token in file

~~**Billing headers are load-bearing (superseded 2026-10-13):**~~ as of 2026-10-13,
the upstream `anthropic_adapter.py` natively sends Bearer-for-oat tokens,
`_OAUTH_ONLY_BETAS`, and Claude Code UA. Non-haiku models work without the bypass.
The bypass was the original source of these headers; upstream caught up. Live-proven:
fable-5-1 and sonnet-4-6 answer with no bypass loaded, zero 429/401 (FACTS.md
2026-10-13). Full diagnostics: see `references/setup-token-diagnostics.md`.
