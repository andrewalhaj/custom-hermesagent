# Deploying a Dockerized third-party tool on a Hermes host + tailnet access

Verified recipe from deploying Open Design (nexu-io/open-design) on `VPS-B`.
Generalizes to any "self-hosted web tool, accessed from the user's desktop over Tailscale,
optionally wired into Hermes via MCP."

> *(Real tailnet IP for section 3 port binding: `memory_search "Tailnet"` — plug in the target host's tailnet address.)*

## 0. Gate + pre-flight (read-only, no greenlight needed)

All install steps below ARE gated (apt install, docker compose up, config writes). The PROBE is not.
Probe the target host first and trust its output over stored topology:

```
hostname; free -h; df -h /; docker --version; docker compose version
node --version; corepack --version
pgrep -af hermes | grep gateway        # which gateway(s) run here
ss -tlnp | grep -E ':(<ports>)'        # port conflicts
(which tailscale || echo "tailscale: NOT FOUND")
```

Key gotchas this surfaces: Node may be too old for a native build (host had v22, tool wanted v24 →
use the Docker route to sidestep entirely); host may have **no swap** (8GB box had 0B swap).

## 1. Swap cushion (gated) — for a no-swap box about to run a new daemon

```
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak-$(date +%Y%m%d-%H%M%S)
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl vm.swappiness=10
# NOTE: /etc/sysctl.conf may not exist on fresh Ubuntu — create it or use /etc/sysctl.d/*.conf
```
Rollback: `swapoff /swapfile; rm /swapfile`; remove the fstab line.

## 2. Tailscale on a headless host (gated; AUTH NEEDS THE USER)

```
curl -fsSL https://tailscale.com/install.sh | sh      # installs + enables tailscaled
tailscale up --authkey=tskey-auth-<KEYID>-<SECRET>    # user generates key at login.tailscale.com
tailscale ip -4                                        # confirm 100.x address
```
- **Auth-key shape check before running:** a real key is `tskey-auth-<keyID>-<secret>` (~50+ chars).
  If the user pastes only the key-ID stub (e.g. `k1hf...CNTRL` with no `tskey-auth-` prefix and no
  secret half), it WILL 401. Ask for the full key; don't fire-and-report-success.
- Alternative if no key: run `tailscale up`, hand the user the printed `login.tailscale.com/a/...` URL.
- Health note "Some peers are advertising routes but --accept-routes is false" is harmless for UI access.
- Rollback: `tailscale down; tailscale logout; apt remove tailscale`.

## 3. Bind the container to the tailnet IP (not localhost) without editing upstream compose

Default compose often binds `127.0.0.1:<port>` (localhost-only). To reach the UI from the user's
desktop over Tailscale, override the port binding with a SEPARATE override file (keeps upstream pristine,
trivial rollback):

```yaml
# docker-compose.override.yml  (sits beside their docker-compose.yml)
services:
  <service>:
    ports: !override
      - "<VPS-B-tailnet-ip>:<port>:<port>"   # tailnet IP, not 0.0.0.0 (don't expose publicly)
```
Then: `docker compose up -d` (run via background=true or a `.sh` file — the literal "compose up"
trips the foreground-server guard). Verify: `docker ps` shows `(healthy)` and
`ss -tlnp | grep <port>` shows the tailnet IP bound.

Save the access URL + API token to a root-only ref for the user:
`~/.hermes/references/<tool>-access.txt` (chmod 600). You cannot see the rendered UI from the
host — state plainly that pixel verification is the user's to do from their desktop browser.

## 4. Verifying an auth-gated HTTP API from the Hermes host — KNOWN-HARD

The host's secret-redaction + shell-eval layer fights you here:
- A 64-hex bearer token gets rendered as `***` in tool echoes, and `$(...)` / parens in echo strings
  break the eval wrapper (`unexpected EOF`, `syntax error near unexpected token`).
- Container's own internal healthcheck (hits loopback inside the container) passing does NOT prove
  external authed access works.
- Best-effort path: write token to a file, build the whole curl in a `.sh` file, run that file.
  Even so, external authed verification from the host may stay inconclusive. That's an ENVIRONMENT
  limitation, not a broken tool — say so, and note the user's browser path (token in UI) bypasses
  the host shell entirely and is unaffected.

## 5. MCP wiring (the part that usually can't be done from Docker)

See the SKILL.md pitfall. `<tool> mcp install hermes` needs the tool's CLI on the host + visibility
of host `~/.hermes/config.yaml`. The Docker image has neither (and may have a namesake binary like
BusyBox `od`). If blocked, re-propose: native CLI install (Node/pnpm) OR `hermes mcp add` against an
HTTP MCP endpoint. Don't silently switch methods after a greenlight for the Docker path.

### 5a. `docker exec -i` AS the MCP stdio transport (the lightest viable wiring)

If the tool ships a stdio MCP server INSIDE the container (e.g. `node /app/.../cli.js mcp`), you don't
need the CLI on the host OR a native install. Hermes can spawn it over a docker pipe. Verified working
for Open Design 2026-06-07. Config block in `mcp_servers:` (config.yaml):
```yaml
  <tool>:
    command: docker
    args: [exec, -i, <container>, node, /app/.../cli.js, mcp, --daemon-url, http://127.0.0.1:<port>]
    enabled: true
```
Prove the handshake BEFORE writing config (read-only, spawns + exits): pipe a JSON-RPC `initialize`
into `docker exec -i <container> <cmd>` and confirm a valid MCP response (protocolVersion, capabilities,
serverInfo). Hermes stdio MCP schema is `command:` + `args:` directly under the server name (same shape
as the npx filesystem example), NOT a `url:` (that's for HTTP servers like the zapier entry).
Cost: ~Ntools schemas + the server's instructions block injected EVERY turn (Open Design = 18 tools
≈ 4.3K tokens). Trim dead tools with a per-server `tools:` allowlist; isolate the whole cost to a
dedicated profile (CLI on-demand via `hermes profile <name> chat`) so the default chat pays nothing.

### 5b. Sharing the host's REFRESHABLE OAuth bypass token with a container — the dual-writer race

When the containerized tool needs to GENERATE (spawn its own `claude`/agent CLI) on the user's Claude
Max plan, it must read the host's OAuth token at `~/.claude/.credentials.json`. Findings 2026-06-07:
- **Who refreshes the bypass token:** Hermes ITSELF, lazily, in `agent/anthropic_adapter.py`
  (`_resolve_claude_code_token_from_credentials` → `_refresh_oauth_token` → `_write_claude_code_credentials`).
  Before each Anthropic request it checks `expiresAt` with a 60s buffer; if stale, POSTs
  `grant_type=refresh_token` to `platform.claude.com/v1/oauth/token` and atomically rewrites the creds
  file (0600, temp+os.replace). There is NO standalone refresher daemon/cron/hook — that's why a
  systemd/cron search for it comes up empty. Token life ≈ 50 min.
- **The race that makes a naive bind-mount FRAGILE:** the container's own `claude` CLI also self-refreshes.
  Mount host `~/.claude` rw into the container and you get TWO independent refreshers on ONE OAuth session.
  Refresh tokens are often single-use (refresh rotates the refresh_token, invalidating the prior one), so
  near a shared expiry window one writer wins and the other's token becomes invalid → silent auth death
  mid-run (runs are 5–30 min). Classic 3am silent failure.
- **Clean architecture = single-writer:** host Hermes stays the SOLE refresher (it already is); the
  container READS the token read-only and must not rotate it. Either mount creds read-only + disable the
  CLI's refresh, OR run a tiny host-side mirror that copies the freshly-refreshed token into a
  container-readable path after each host refresh. Tradeoff of read-only: if a run outlives the token
  window AND the host makes no Anthropic call to trigger refresh during it, the container hits an expired
  token mid-generation — mitigate with a host-side keep-warm.
- **Container exec/mount facts that bite (Open Design image):** rootfs is read-only, `/tmp` is tmpfs
  `noexec` (a 240MB native binary WON'T run from `/tmp` → `Permission denied` on an `-rwxr-xr-x` file —
  check `mount | grep noexec`); only the named volume (`/app/.od`, ext4) is writable AND exec-capable, so
  ephemeral installs/binaries must live there. `docker cp` can't write into a tmpfs mount — pipe via
  `docker exec -i <c> sh -c 'cat > /path'` instead. A global `npm i -g` lands in `/usr/local` (NOT the
  persisted volume) so it vanishes on recreate; install into the volume or bake a custom image for
  permanence. The CLI's bin entry may be a wrapper/`.exe` shim — find the real per-platform native binary
  (e.g. `.../node_modules/@scope/<pkg>-linux-x64-musl/<bin>`) and invoke that.
- **Decision rule:** auth-PROVEN ≠ build-CLEAN. If the goal is the tool's native generation pipeline,
  the permanent build is viable but REQUIRES the single-writer design — it is NOT bind-mount-and-go. If
  the goal is just reachable tools/context, prefer the MCP route (§5a): Hermes is already authed and
  already refreshing, so there's zero token-sharing race.
