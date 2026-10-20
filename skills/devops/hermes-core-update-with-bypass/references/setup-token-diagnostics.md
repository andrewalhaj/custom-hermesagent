# Setup Token Diagnostics — full verification script

> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

Proven 2026-07-21 on a `claude setup-token` managed key.

## Quick auth-chain test (run from Hermes venv)

```python
import sys, httpx, json, hashlib, time
sys.path.insert(0, '/usr/local/lib/hermes-agent')
from agent.anthropic_adapter import (
    resolve_anthropic_token, build_anthropic_client, build_anthropic_kwargs,
    read_claude_code_credentials, is_claude_code_token_valid, _is_oauth_token,
    _get_claude_code_version
)

# ── 1. Check token state ──
creds = read_claude_code_credentials()
token = resolve_anthropic_token()
print(f"Token valid: {is_claude_code_token_valid(creds)}")
print(f"Is OAuth: {_is_oauth_token(token)}")
print(f"Has refresh: {bool(creds.get('refreshToken'))}")
print(f"expiresAt: {creds.get('expiresAt', 'MISSING')}")

# ── 2. Test with billing headers (full bypass path) ──
if token:
    client = build_anthropic_client(token, "https://api.anthropic.com")
    kwargs = build_anthropic_kwargs(
        model="claude-opus-4-8", messages=[{"role":"user","content":"Reply: OK"}],
        tools=None, max_tokens=10, reasoning_config={}, is_oauth=True
    )
    filtered = {k:v for k,v in kwargs.items() if k != 'extra_headers'}
    try:
        resp = client.messages.create(**filtered)
        print(f"SDK opus-4-8: 200 — {resp.content[0].text}")
    except Exception as e:
        print(f"SDK opus-4-8: {e}")

# ── 3. Test bare Bearer vs billing headers (raw httpx) ──
    version = _get_claude_code_version() or "2.1.112"
    salt = "59cf53e54c78"
    ts = str(int(time.time()))
    sig = hashlib.sha256(f"{salt}{ts}".encode()).hexdigest()

    # With billing headers
    r = httpx.post("https://api.anthropic.com/v1/messages",
        headers={
            "Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
            "x-anthropic-billing-header": f"{ts}:{sig}",
            "user-agent": f"claude-cli/{version} (external, cli)",
            "x-app": "cli", "x-stainless-arch": "x64", "x-stainless-lang": "js",
            "x-stainless-os": "linux", "x-stainless-package-version": "0.81.0",
            "anthropic-dangerous-direct-browser-access": "true",
        },
        json={"model":"claude-opus-4-8","max_tokens":10,"messages":[{"role":"user","content":"OK"}]},
        timeout=15
    )
    print(f"Bare httpx + billing: {r.status_code}")

    # Without billing headers
    r2 = httpx.post("https://api.anthropic.com/v1/messages",
        headers={
            "Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model":"claude-opus-4-8","max_tokens":10,"messages":[{"role":"user","content":"OK"}]},
        timeout=15
    )
    print(f"Bare httpx (no billing): {r2.status_code}")
```

## Interpreting results

| Token check | Billing headers | Result | Meaning |
|---|---|---|---|
| Valid | Present | 200 | ✅ Everything working |
| Valid | Absent | 200 | Upstream adapter sends billing headers natively (2026-10-13); bypass no longer required for non-haiku models |
| Invalid | — | 401 | Token expired or wrong format — PITFALL 10 |
| Valid | Present | 401 | Token doesn't match subscription — re-run `claude setup-token` |

## Fix flow

1. **Setup token with no refresh:** set `expiresAt = 0`, `refreshToken = ""` in credentials JSON
2. **Gateway stale:** restart gateway from outside (PITFALL 9 + 12)
3. **Bypass not intercepting:** check `apply_patches()` in logs; verify `is_oauth=True` reaches `build_anthropic_kwargs`
4. **Dead fallback:** remove DeepSeek from `fallback_providers` if 402 (PITFALL 13)
