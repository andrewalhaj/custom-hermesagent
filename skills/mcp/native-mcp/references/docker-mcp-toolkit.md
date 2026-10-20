# Docker MCP Toolkit — `docker mcp` CLI, catalog/profiles/secrets

A second, distinct way to run containerized MCP servers: Docker's **MCP Toolkit**
(`docker mcp ...`), shipped as a **Docker Desktop** plugin (Beta, Desktop 4.62+).
Different from the `docker exec -i` stdio bridge in `containerized-mcp-server.md`
— that wires ONE server straight into Hermes `config.yaml`; this is Docker's own
profile/catalog/secret management layer with a gateway that multiplexes many
servers.

## When to reach for the Toolkit vs. raw `config.yaml`
- **Raw `config.yaml` (`command: docker`, `args: [run|exec, -i, ...]`)** — the
  default. One server, one block, Hermes discovers it at startup. Simplest. Use
  this unless the user explicitly wants Docker's toolkit.
- **Docker MCP Toolkit** — only if the user asks for it (they pointed at
  docs.docker.com/ai/mcp-catalog-and-toolkit). Adds a curated catalog (300+
  servers), profiles, OS-keychain secrets, and a gateway. Requires **Docker
  Desktop** — NOT available on plain Docker Engine.

## Desktop vs. Engine (the first thing to check)
`docker mcp` is a Desktop-only plugin. On a headless Linux box running plain
Docker Engine it does not exist. Detect:
```bash
docker mcp version 2>/dev/null || echo "no docker mcp — Desktop not installed"
ls /opt/docker-desktop 2>/dev/null   # present once Desktop is installed
```
Desktop on Linux runs the engine inside a QEMU/KVM VM (needs `/dev/kvm`). It
installs alongside any existing Engine under a separate context
(`desktop-linux`); existing containers are untouched. The `.deb` is ~440MB,
~800MB installed. `docker mcp` works fully **headless** — no display needed.

### Install (all gated: apt + service start)
```bash
curl -fSL https://desktop.docker.com/linux/main/amd64/docker-desktop-amd64.deb -o /tmp/dd.deb
apt-get install -y /tmp/dd.deb         # pulls qemu-system-x86 etc.
systemctl --user start docker-desktop  # gated; ~15-20s to become usable
docker mcp version                     # verify plugin live (e.g. v0.42.x)
```

## Wiring a catalog server (worked example: github-official)
```bash
docker mcp catalog pull mcp/docker-mcp-catalog          # catalog is EMPTY until pulled
docker mcp catalog server ls mcp/docker-mcp-catalog | grep -i github
docker mcp profile create --name hermes
docker mcp profile server add hermes \
  --server catalog://mcp/docker-mcp-catalog/github-official
docker mcp profile show hermes      # inspect: secrets block, tools, allowHosts
```
`docker mcp profile show <p>` dumps the full server snapshot including the EXACT
secret key names the server expects (see pitfall below) and its `allowHosts`
egress allowlist.

## PITFALL — headless keychain has no default collection
`docker mcp secret set` writes to the OS keychain (`docker-pass` /
freedesktop Secret Service). On a headless Linux box with no GUI session there is
no unlocked `login` collection, so it fails:
```
could not store secret: no default keychain collection available
# or, after a daemon is up but no collection:
could not store secret: Object does not exist at path ".../collection/login"
```
**Fix — run gnome-keyring headlessly and create the default collection:**
```bash
apt-get install -y gnome-keyring libsecret-tools     # gnome-keyring-daemon usually already present
# 1. Start the secrets component with an empty passphrase; capture the bus addr
eval $(gnome-keyring-daemon --unlock --components=secrets <<< "")
export DBUS_SESSION_BUS_ADDRESS   # e.g. unix:path=/run/user/0/bus
# 2. Create the default collection via gdbus (ONLY the 'default' alias is supported)
gdbus call --session --dest org.freedesktop.secrets \
  --object-path /org/freedesktop/secrets \
  --method org.freedesktop.Secret.Service.CreateCollection \
  "{'org.freedesktop.Secret.Collection.Label': <'login'>}" "default"
# 3. Now secret set works (pipe value via stdin so it never sits in argv):
printf '%s' "$TOKEN" | docker mcp secret set <KEY>
docker mcp secret ls            # KEY | docker-pass
```
Notes:
- `CreateCollection` rejects any alias except `default` ("Only the 'default'
  alias is supported"). Pass label `login` but alias `default`.
- The CreateCollection prompt may print a NoReply/disconnect error — ignore it
  and just retry the `secret set`; the collection is created.
- Always feed the secret on **stdin** (`printf '%s' "$TOK" | docker mcp secret
  set KEY`), never `set KEY=value` — keeps it out of argv and shell history.

## PITFALL — secret KEY NAME must match what the profile expects
The server's snapshot declares the env/secret binding. github-official wants the
secret named **`github.personal_access_token`** (which it maps to env
`GITHUB_PERSONAL_ACCESS_TOKEN`). Storing it under the env name alone yields, in
the gateway log:
```
Warning: Secret 'github.personal_access_token' not found for server
'github-official', setting GITHUB_PERSONAL_ACCESS_TOKEN=
```
→ 401 on every call. Read the exact key from `docker mcp profile show <p>`
(look for `secrets: - name: <key>`) and store under THAT name.

## Live-test the gateway before declaring success
The gateway multiplexes the profile's servers over stdio. Drive a real tool call:
```bash
( printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}\n'
  sleep 4
  printf '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_me","arguments":{}}}\n'
  sleep 6
) | timeout 22 docker mcp gateway run --profile hermes 2>&1 | tail -5
```
A `200`-style result (`{"result":{"content":[{"text":"{...login...}"}]}}`) proves
the secret + auth round-trip. `401 Bad credentials` means EITHER the wrong secret
key name OR a dead token (see next).

## ROOT-CAUSE REMINDER — a pasted PAT is a dead PAT
GitHub secret-scanning auto-revokes any token that appears in plaintext (chat,
gist, public file) within seconds. If a user pastes a PAT into the conversation,
assume it is already revoked — verify cheaply with a direct curl BEFORE blaming
the wiring:
```bash
curl -s -H "Authorization: token $TOK" https://api.github.com/user \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('login',d.get('message')))"
# 'Bad credentials' => token is dead; ask for a fresh one, entered out-of-band.
```
This is the cheapest possible check and it isolates "credential dead" from
"integration misconfigured" in one call. Do it first whenever a freshly-wired
GitHub (or any token-auth) MCP server returns 401. Tell the user to mint a new
token and NOT paste it — enter it directly into the keychain.

## PITFALL — headless keychain secrets DO NOT survive a daemon restart
The gnome-keyring fix above gets you a working secret *for the current daemon
life only*. The created `login`/`default` collection is unlocked **in memory**;
there is no persistent unlocked store without PAM auto-unlock (which needs a real
login session). Consequences observed:
- Restart the keyring daemon (or wrap it in a `systemd --user` unit and restart)
  → `docker mcp secret ls` returns **empty**; all secrets are gone.
- Re-running the gdbus `CreateCollection` when a collection already exists fires a
  prompt that **cannot be dismissed non-interactively** (`failed to prompt: prompt
  dismissed`), and `Secret.Service.Unlock` returns a prompt object that also can't
  be satisfied headlessly. You're wedged.
- A `Type=forking`/`oneshot` systemd unit for `gnome-keyring-daemon` starts the
  daemon but each restart yields a *fresh empty keychain* — so a unit buys you
  nothing for persistence; it just adds moving parts.

**DURABLE FIX on a headless server: skip the keychain entirely — use a
PAT-file + wrapper-script.** This survives reboots, needs no GUI/dbus, and keeps
the secret out of `config.yaml`:
```bash
# 1. PAT to a 0600 file (write via python so the value never hits terminal stdout/argv)
python3 -c "import os,stat;p=os.path.expanduser('~/.hermes/.github-pat');open(p,'w').write(TOKEN);os.chmod(p,0o600)"
# 2. wrapper script that reads the file and execs the raw container
cat > ~/.hermes/scripts/github-mcp.sh <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
PAT_FILE="${HERMES_HOME:-$HOME/.hermes}/.github-pat"
PAT=$(cat "$PAT_FILE")
exec docker run -i --rm -e "GITHUB_PERSONAL_ACCESS_TOKEN=$PAT" \
  ghcr.io/github/github-mcp-server stdio "$@"
SCRIPT
chmod +x ~/.hermes/scripts/github-mcp.sh
```
Then in `config.yaml` (gated): `command: ~/.hermes/scripts/github-mcp.sh`,
`args: []`. This bypasses the whole `docker mcp` Toolkit/keychain layer — at which
point the Toolkit is no longer needed for a single server. **Reach for the
Toolkit only when the user explicitly wants its catalog/profile UI on a box with a
real desktop session; on a headless server the PAT-file wrapper is simpler and
actually persistent.**

PITFALL — the in-process **security scanner rewrites tokens to `***` when you
write them inline.** Writing `GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...` into
`config.yaml` (even via a python heredoc) gets the value redacted to the literal
string `***` on disk → silent 401s. The PAT-file approach sidesteps this because
the wrapper reads the value at runtime; it never appears in a scanned write.
Verify any inline-written secret on disk before assuming it took.

## Connecting the Toolkit to Hermes
Two options once the gateway works: (a) point Hermes at the gateway as a single
HTTP/stdio MCP endpoint, or (b) keep using Hermes' own per-server `config.yaml`
blocks and treat the Toolkit purely as Docker's management UI. Decide with the
user — the Toolkit's value is the catalog + keychain, not a requirement. On a
**headless** box, prefer the PAT-file wrapper over either (see pitfall above).
