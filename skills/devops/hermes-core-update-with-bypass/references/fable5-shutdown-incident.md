# Fable 5 shutdown — OAuth bypass incident (2026-06-12)

> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

## What happened

On 2026-06-12, the US government issued an export control directive forcing
Anthropic to disable **Fable 5 and Mythos 5** for all customers. Anthropic's
statement: "Access to all other Claude models is not affected."
(https://www.anthropic.com/news/fable-mythos-access)

## Why ALL models broke through the OAuth bypass

The `hermes-claude-auth` bypass has a complexity classifier that auto-upgrades
`claude-sonnet-*` → `_HEAVY_MODEL` (set to `claude-fable-5`) on complex tasks.
The system prompt (AGENTS.md) is keyword-saturated — it clears the classifier
threshold on nearly every request. Net effect: **every Anthropic API call was
silently upgraded to Fable 5, which was disabled**, returning HTTP 404.

## Diagnostic signal

Gateways logs showed:
```
provider=anthropic model=claude-sonnet-4-6
summary=HTTP 404: Claude Fable 5 is not available.
  Please use Opus 4.8.
  Learn more: https://www.anthropic.com/news/fable-mythos-access
```

Key diagnostic: the **model in the log line is the ORIGINAL requested model**
(Sonnet), but the **error body names the disabled model** (Fable 5). The
classifier swaps after the model name is captured for logging but before the
HTTP call. This mismatch is the reliable signal — not a cross-model API bug.

The same 404 appeared for `claude-opus-4-8` requests too — any request that
cleared the complexity threshold got upgraded to the dead model.

## Verification steps

1. Confirm the bypass is the culprit (not API keys, not token expiry):
   ```bash
   grep '_HEAVY_MODEL =' ~/.hermes/patches/anthropic_billing_bypass.py
   ```
2. Confirm the token is valid:
   ```bash
   python3 -c "
   import json, time
   c = json.load(open(os.path.expanduser('~/.claude/.credentials.json')))['claudeAiOauth']
   print(f'expires: {time.strftime(\"%Y-%m-%d %H:%M:%S\", time.gmtime(c[\"expiresAt\"]/1000))}')
   print('token valid:', c['expiresAt']/1000 > time.time())
   "
   ```
3. Confirm the Anthropic API is reachable (not a network issue) — the 404 is a
   real response from Anthropic, not a timeout/connection error.
4. Check external events: Anthropic's statements, X posts, status pages — the
   model shutdown was a public announcement, not a silent API change.

## Fix

Change `_HEAVY_MODEL` to an available model:
```bash
python3 -c "
import re
src = open(os.path.expanduser('~/.hermes/patches/anthropic_billing_bypass.py')).read()
m = re.search(r'_HEAVY_MODEL = \"([^\"]+)\"', src)
print(f'current: {m.group(1) if m else \"NOT FOUND\"}')
"
```
If it's `claude-fable-5`, swap to `claude-opus-4-8` or back to `claude-sonnet-4-6`.

Gateway restart required (gated — drops the live session).

## Prevention

> ⚠️ **HISTORICAL (2026-10-13):** the bypass is ARCHIVED and the classifier
> (`_HEAVY_MODEL` upgrade) is not loaded by the current gateway loader. This
> prevention guidance only applies if the bypass is re-enabled.

- Prefer `_HEAVY_MODEL = "claude-opus-4-8"` (stable tier) over preview/limited models.
- When Anthropic announces model deprecations/shutdowns, immediately check whether
  the bypass's `_HEAVY_MODEL` targets the affected model.
- The classifier has no health check — it'll route to a dead model silently
  until someone notices all Anthropic calls are failing.

## Related

- `references/complexity-classifier-tuning.md` — the system-prompt over-fire trap
  (why nearly every request triggers the classifier)
- PITFALL 2 in the parent SKILL.md — the same incident, as a procedural pitfall
