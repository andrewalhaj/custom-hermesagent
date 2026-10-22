---
name: preflight-validation
description: "Use before a restart: validate creds and connections"
sector_path: [infrastructure, hosting, deploy]
---

# Pre-Flight Validation

Before restarting any service after a config change (especially `.env` edits), validate the new credentials with a live check. Do not trust `sed`, `echo`, or copy-paste — verify with the actual protocol.

## When to Use

- After editing any `DATABASE_URL` in `.env` or `docker-compose.yml`
- After writing API keys, connection strings, or credentials to config files
- After copying `.env` files between hosts
- Before `docker compose up -d` or `systemctl restart`

## Pattern: Delegation model resolves on the inference node (before gateway restart)

> *(LH02 inference node address: `memory_search "Tailnet"` → look for the Ollama/LH02 entry.)*

After changing `delegation.model` (or any `auxiliary.*.model`) that points at a local
inference node, the model name in config MUST actually exist and respond on that node BEFORE
you restart the gateway. A config that names a deleted/renamed/never-built model does NOT
error at restart — it silently 404s at delegation time and falls back to the cloud fallback
provider (e.g. DeepSeek), wasting hours of "why is delegation on the wrong model" debugging.

Two compounding traps observed (2026-06-18, LH02):
- A model `ollama create`'d earlier in the session was GONE from `ollama list` later — models
  drift; never trust "we built it earlier."
- Repeated `yaml.load → edit → dump` cycles in one session silently REVERTED each other, so
  `delegation.model` on disk was not what the last edit intended.

```bash
# 1. Confirm the EXACT configured model name resolves on the node RIGHT NOW:
MODEL=$(python3 -c "import os; import yaml;print(yaml.safe_load(open(os.path.expanduser('~/.hermes/config.yaml')))['delegation']['model'])")
curl -sf -m10 http://<LH02-tailnet-ip>:11434/api/generate \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"hi\",\"stream\":false,\"options\":{\"num_predict\":3}}" \
  >/dev/null || { echo "FATAL: '$MODEL' does not resolve on the node — rebuild/repoint BEFORE restart"; exit 1; }

# 2. Cross-check it actually exists in the authoritative list (not just a cached blob):
ssh ... 'ollama list' | grep -q "$MODEL" || echo "WARN: $MODEL not in ollama list"

# 3. Re-read the config block off disk to confirm your edit persisted (writes can revert):
python3 -c "import os; import yaml;print('on-disk delegation.model:', yaml.safe_load(open(os.path.expanduser('~/.hermes/config.yaml')))['delegation']['model'])"
```

**The only end-to-end proof** (do this AFTER restart): a real `delegate_task` whose result
`model` field shows the local model — NOT the cloud fallback. config-looks-right is not proof.
Full delegation-routing failure taxonomy lives in the `ollama-inference-node-ops` skill.

## Pattern: PostgreSQL

```bash
# After writing DATABASE_URL, verify immediately:
PW=$(extract from .env)  # get actual password, not masked
URL="postgresql://user:$PW@host:port/dbname"
PGPASSWORD="$PW" psql "$URL" -c "SELECT 1" || {
  echo "FATAL: database connection failed — do NOT restart the service"
  exit 1
}
```

## Pattern: a valid `.env` key the service never sees (missing `EnvironmentFile=`)

A key can be present AND valid in `.env` yet still be rejected at runtime — because the systemd
unit never loads `.env` into the process environment. The credential resolver inside the app
reads `os.environ`, not the file; if the unit only has explicit `Environment=` lines and no
`EnvironmentFile=`, only those named vars exist in the process. Everything else in `.env` is
invisible. Symptom: `printenv | grep KEY` on the host shows it (because YOUR shell sourced it),
but the SERVICE 401s.

Hit 2026-06-19: `DEEPSEEK_API_KEY` was in `~/.hermes/.env`, 35 chars, `sk-` prefix, HTTP 200
against `/v1/models` — provably valid — yet delegation rejected it. Root cause: the
`hermes-gateway.service` unit had `Environment=` lines for PATH/VIRTUAL_ENV/HERMES_HOME but NO
`EnvironmentFile=~/.hermes/.env`, so the running gateway's `/proc/<pid>/environ` had no
DeepSeek key at all. The key had likely NEVER worked unless launched from a shell that sourced
`.env` first.

**Diagnose — read the SERVICE's actual environment, not your shell's:**
```bash
# Your shell sourcing .env proves nothing about the service. Read the live process env:
PID=$(ps aux | grep "<service-exec>" | grep -v grep | awk '{print $2}' | head -1)
cat /proc/$PID/environ | tr '\0' '\n' | grep -i <KEYNAME> || echo "NOT IN SERVICE ENV"

# Then confirm the unit actually loads the file:
systemctl --user cat <service> | grep -i EnvironmentFile || echo "NO EnvironmentFile — that's the bug"
```
Read the value out of `.env` WITHOUT triggering the write-gate (don't let a command string
contain a gated redirect to `.env`):
```bash
python3 -c "import os; k=[l.split('=',1)[1] for l in open(os.path.expanduser('~/.hermes/.env')).read().splitlines() if l.startswith('KEYNAME=')][0]; print('len',len(k),'prefix',k[:5])"
```

**Fix:** add `EnvironmentFile=~/.hermes/.env` under `[Service]` (GATED unit edit + backup +
`daemon-reload` + restart). After restart, re-read `/proc/<NEW_PID>/environ` to confirm the key
is now present. This is the permanent fix — every future restart auto-loads the key. Inlining
`Environment="KEY=..."` works too but exposes the secret in the unit file; prefer EnvironmentFile.

**General rule:** "the key is in `.env` and valid" is NOT proof the service can use it. The proof
is the key appearing in the running process's `/proc/<pid>/environ`. Check the process, not the file.

## Pattern: git-ignored secret files must exist on disk BEFORE restart

A service can run for WEEKS on in-memory state while a required git-ignored file
(password hash, secret, token file) is MISSING from disk — deleted by a rotation, a
`git clean`, or a checkout. Nothing errors until the next restart, which then
crash-loops on `FileNotFoundError` at import. Hit 2026-08-13: hermes-dashboard restart
crash-looped on missing `~/hermes-dispatcher/.dashboard_passwd_hash` (git-ignored,
read at startup); the running process had held it in memory since the Jul 8 rotation.

**Preflight before restarting any service:** grep its startup path for required files
(`grep -rn 'FileNotFoundError\|hash_file\|Path(.*secret' <entrypoint+auth modules>`), then
`ls` each git-ignored required file. If missing, look for a `.bak-<timestamp>` sibling from
the last rotation before regenerating — restoring a rotation-era backup may revert the
credential to its pre-rotation value; tell the user which password is now live.

**Restarting a service from INSIDE the gateway session.** Sibling services (kb-daemon, dispatcher)
restart with a plain `systemctl --user restart <unit>` followed by `is-active` + a `/proc/<pid>/environ`
read. The gateway itself cannot be restarted by a direct `systemctl restart` from its own session (the
SIGTERM kills the caller) — trigger the detached oneshot unit instead:
```bash
systemctl --user start --no-block hermes-gw-restart.service   # sleeps 5 s, then restarts hermes-gateway outside the gateway's cgroup
```
`--no-block` is required: `systemctl start` on a oneshot waits for it to finish, and its job is to kill the
session that called it, so without the flag the terminal call hangs to its timeout (the restart still
happens). The reply you send in the same turn goes out before the 5 s delay. Before triggering: no
delegate children running (a restart kills them), and the `.env`/drop-in diff is exactly what you meant
(`diff <backup> <live>` → the one line). After the session comes back, prove the change landed from the
NEW process: fresh `ActiveEnterTimestamp`, `/proc/<MainPID>/environ` shows the flag you changed, journal has
no Traceback. A drop-in edit alone changes `systemctl show`'s view after `daemon-reload` but not the running
process — dead flags persist in the live env until the restart, so say which you verified.

## Pattern: Placeholder Values

The most common silent failure: literal `***` in a connection string. This happens when:
- Hermes masks a password in tool output, and the masked value gets written to a file
- Config is copied from a terminal transcript instead of the actual source
- A `.env.example` with placeholders is copied instead of the real `.env`

**Detection:** Check password length. Railway generates 32-char passwords. Neon generates ~36-char passwords. A 3-char password (`***`) is always a placeholder.

```bash
PW=$(grep DATABASE_URL .env | sed 's/.*:\/\/.*:\(.*\)@.*/\1/')
if [ ${#PW} -lt 8 ]; then
  echo "FATAL: password is too short (${#PW} chars) — likely a placeholder"
fi
```

## Pattern: API Keys

```bash
# After writing API keys, validate with a lightweight API call:
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $API_KEY" \
  https://api.anthropic.com/v1/models
# 200 = valid, 401 = bad key
```

## Pattern: Service URLs

```bash
# After changing a service URL, verify the endpoint responds:
curl -sf "$BASE_URL/api/v1/health" || {
  echo "FATAL: service health check failed"
}
```

## Verification Checklist

After any infrastructure change, run in this order:

1. **Credential validation** — live connection test before restart
2. **Service health** — `curl` the health endpoint after restart  
3. **End-to-end** — exercise a real request through the full path
4. **LB check** — if behind a load balancer, hit it 3x and verify alternating backends
5. **Encrypted-at-rest keys** — after any container restart that may regenerate secrets, verify that encrypted credentials in the DB still decrypt correctly. A `Failed to decrypt API key` warning in container logs post-restart means the encryption secret changed and affected keys must be re-encrypted.

## Two-Layer Credential Gotcha

When a router/proxy sits between client and provider (e.g. Hermes → Manifest →
Anthropic), credentials live in TWO separate places:
- **Client→router** key (e.g. Hermes `config.yaml`: `api_key: mnfst_...`)
- **Router→provider** key (e.g. Manifest `user_providers` table: `sk-ant-...`)

A provider `401 invalid x-api-key` is almost always the ROUTER→PROVIDER key,
NOT the client config. Running `hermes model ...` or editing client config
CANNOT fix a Manifest→Anthropic auth error — the provider key lives in the
router's own store (DB or dashboard). 

**Diagnose the failing hop first** by reading the proxy logs:
```
docker logs <router> | grep -iE '401|auth|invalid'
# "provider=anthropic ... 401 invalid x-api-key" = router→provider key is bad
```
Then fix THAT layer. Don't waste a round-trip fixing the wrong config.

## Validate-on-Save Silently Rejects

Some systems (Manifest included) test a provider key by calling the provider
BEFORE persisting it. If the key is invalid, the save is silently refused —
no error to the user, and the row's `updated_at` stays unchanged. 

**Always confirm persistence after a dashboard/API save:**
```bash
# Re-query the store; updated_at MUST have advanced
psql "$DB" -c "SELECT provider, updated_at FROM user_providers WHERE provider='anthropic';"
```
A stale timestamp = the save failed. Test the key against the provider directly
(pattern above) before entering it, so the save isn't rejected in the first place.

## Per-Phase Verification (multi-step migrations)

Verify EACH phase independently before starting the next — don't batch.
A 4-phase Manifest migration had a dead second instance (Phase 2) that went
undetected until Phase 4 surfaced it. An end-to-end check at the end of Phase 2
(`curl` each instance individually, THEN through the LB) would have caught it
two phases earlier.

## Pattern: Restart wedged by a ghost LISTEN socket (`ss`/`lsof` show nothing)

After killing a service to force a systemd respawn, the new process can crash-loop on
`address already in use` / `Errno 98` **while `ss -tlnp`, `lsof -ti tcp:<port>`, and `fuser
<port>/tcp` all report the port free.** The old process (or a child that inherited the listening
fd) is still holding the socket, but the standard tools miss it — the holder may be a bash
wrapper, a defunct child, or a process in a namespace the tool didn't scan.

Hit 2026-07-08: a `kill <uvicorn PID>` (SIGTERM) was caught and the process stayed up; a
follow-up `kill -9` spawned a replacement that then crash-looped on 8787-in-use for ~670 restart
cycles, yet `ss`/`fuser`/`lsof` all showed the port clear. The preflight guard (`Refusing to
start… already in use`) correctly blocked the bind — it was telling the truth; the tools were lying.

**Recover by tracing the socket through `/proc` — this always finds the true owner:**
```bash
PORT=8787
# 1. Confirm the port is actually held and get the socket inode (state 0A = LISTEN).
#    Hex port: printf '%04X' 8787  ->  2253
python3 - <<'EOF'
port = 8787
for fn in ('/proc/net/tcp', '/proc/net/tcp6'):
    try:
        for line in open(fn).read().splitlines()[1:]:
            p = line.split()
            if not p: continue
            if int(p[1].split(':')[-1], 16) == port:
                print(fn, 'state', p[3], 'inode', p[9])   # 0A = LISTEN
    except FileNotFoundError:
        pass
EOF
# 2. Find the PID owning that inode via its fd symlinks (grep/ls miss it; use find -lname):
INODE=<from step 1>
find /proc -maxdepth 3 -name fd 2>/dev/null \
  | xargs -I{} find {} -maxdepth 1 -lname "socket:\[$INODE\]" 2>/dev/null
#   -> /proc/<PID>/fd/<n>
# 3. Confirm it's the stale service, then kill THAT pid (SIGTERM first):
cat /proc/<PID>/cmdline | tr '\0' ' '; echo
kill <PID>; sleep 6
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:$PORT/   # expect 200
```
Independent free-port probe when tools disagree: a Python `socket.bind(('0.0.0.0',PORT))` that
raises `OSError: [Errno 98]` proves the port is held even when `ss` says otherwise — trust the
bind, not the listing.

**Also:** a graceful `kill` (SIGTERM) may be trapped by the app for cleanup and not actually exit;
if the PID is unchanged after a few seconds, escalate to `kill -9`. And under systemd
`Restart=always`, expect a brief crash-loop window while the wedged socket clears — read
`journalctl -u <svc>` to distinguish \"backing off, will recover\" from a real config fault.

## Durable-Notes Corollary

Long infra sessions get context-compacted; in-session plans, route tables, and
credentials vanish. Write them to a durable file (`~/.hermes/references/<topic>.md`)
BEFORE compaction — that file is often the only reason a second attempt succeeds.

## Anthropic OAuth Token vs API Key — Do Not Confuse

When Hermes uses `hermes-claude-auth` for Claude Max subscription access, `ANTHROPIC_TOKEN` in `.env` is a **Claude Code OAuth token** (`sk-ant-o...` prefix). It will 401 when passed as `x-api-key` in a raw `curl`. The bypass patch (`sitecustomize.py`) must be in the call chain.

**Preflight test for the OAuth route** — do NOT use raw curl; let Hermes make the call:
```bash
hermes chat -q 'Reply with exactly: AUTH TEST OK' --provider anthropic -m claude-sonnet-4-6 -Q
```

**Preflight test for a real API key** (`sk-ant-api...`) — raw curl is fine:
```bash
source ~/.hermes/.env
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-4-5","max_tokens":16,"messages":[{"role":"user","content":"say ok"}]}'
```

**Distinguishing token types:**
- `sk-ant-o...` → OAuth token, only works through bypass hook
- `sk-ant-api...` → direct API key, works raw
- Empty `ANTHROPIC_API_KEY` with populated `ANTHROPIC_TOKEN` → OAuth-only setup, must use bypass

## References

- Docker postgres: `docker exec <postgres-container> psql -U manifest -c "SELECT 1"`
