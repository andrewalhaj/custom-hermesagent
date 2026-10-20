# Containerized MCP server — `docker exec -i` stdio bridge

When you want to wire an MCP server whose CLI lives **inside a Docker container**
(not installed on the host, and the host may lack the runtime entirely), you do
not need a native install or an HTTP endpoint. Use `docker exec -i` as the stdio
transport: Hermes spawns the in-container CLI over a docker pipe on each tool
call. Zero host footprint — pure config, plus a short-lived `docker exec` per
call.

## When this applies
- Tool ships as a Docker image with a daemon + an in-container CLI (e.g. Open
  Design: daemon on a tailnet port, CLI at `/app/apps/daemon/dist/cli.js`).
- The CLI's `mcp` subcommand is a **stdio** server (reads JSON-RPC on stdin,
  writes on stdout). Confirm via its `--help` or the daemon's
  `/api/mcp/install-info` (which advertises `command`/`args`/`env`).
- The host has neither the CLI nor (often) the runtime. Installing natively is
  the heavy footprint you're trying to avoid.

## The config block
```yaml
mcp_servers:
  open-design:
    command: docker
    args:
      - exec
      - -i                      # interactive: keep stdin open for the JSON-RPC pipe
      - <container_name>
      - node                    # in-container runtime
      - /app/apps/daemon/dist/cli.js
      - mcp
      - --daemon-url
      - http://127.0.0.1:7456   # daemon's loopback INSIDE the container
    enabled: true
```
Notes:
- `-i` is mandatory — without it stdin is closed and the MCP handshake never
  completes.
- The `--daemon-url` is the daemon's address **as seen from inside the
  container** (loopback), not the host/tailnet address.
- `OD_DATA_DIR` and similar env the daemon advertises can go under an `env:` key
  if the CLI needs them; for a sidecar/daemon already running they're usually
  resolved by the daemon itself.

## VERIFY THE HANDSHAKE BEFORE WRITING CONFIG (read-only probe)
Never wire a server you haven't proven answers. Pipe an `initialize` request
straight through `docker exec -i` and confirm a valid MCP response:

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0.1"}}}' \
 | timeout 20 docker exec -i <container> node /path/cli.js mcp --daemon-url http://127.0.0.1:PORT 2>&1 | head -20
```
A good result returns `{"result":{"protocolVersion":"2024-11-05","capabilities":{...},"serverInfo":{...}},...}`.
That proves: (1) the pipe is clean, (2) the CLI's `mcp` subcommand works, (3)
Hermes can drive it. Only then write the gated `config.yaml` block.

For a multi-call probe (e.g. `tools/call` on `list_agents`), feed the full MCP
sequence on separate lines — `initialize`, then
`{"jsonrpc":"2.0","method":"notifications/initialized"}`, then your
`tools/call`. Grep the response by id (`grep -o '"id":2.*"`) — naive
`head`/`tail` can clip the server's reply because output ordering is not
guaranteed.

## Restart still required
The MCP block is discovered at agent startup. After the (gated) config write,
the new `mcp_<server>_*` tools are NOT live until the gateway restarts
(`systemctl --user restart hermes-gateway`, itself gated). Don't claim the tools
are usable in-chat before the restart.

## AUTH GAP LESSON — "wired" ≠ "useful"
A containerized agent/design runtime can expose a rich MCP toolset (file
read/write, project CRUD) yet still be **unable to do its headline job**
(generate) because the generation pipeline needs an *agent CLI* installed and
authed inside the container. Check this before declaring the integration
valuable:

- Query the daemon's agent list (loopback, auth via its own token):
  `docker exec <c> sh -c 'wget -qO- --header="Authorization: Bearer $OD_API_TOKEN" http://127.0.0.1:PORT/api/agents'`
  — if **every** agent reports `"available": false`, no agent CLI is installed.
- Confirm directly: `docker exec <c> sh -c 'for b in claude codex gemini ...; do which $b || echo MISSING; done'`.
- Check the container env for model creds:
  `docker exec <c> sh -c 'env | grep -iE "API_KEY|TOKEN|ANTHROPIC|OPENAI" | sed -E "s/=.*/=<redacted>/"'`.

### Riding the host's Claude OAuth bypass into the container
If the host already has the `hermes-claude-auth` OAuth bypass, the live token is
at `~/.claude/.credentials.json` (`claudeAiOauth` = `accessToken`,
`refreshToken`, `expiresAt`). The container's `claude` runtime spawns the
`claude` CLI, which reads that exact file. To use the flat-rate Max plan inside
the container WITHOUT copying a token that rots:
- Install the `claude` CLI in the container (`npm i -g @anthropic-ai/claude-code`
  — needs outbound net; if the image has none, bake it into the image build).
- **Bind-mount host `~/.claude` → container** so the in-container CLI reads the
  same OAuth file AND the host's refresher keeps it alive (single source of
  truth, no expiry drift). A static copy goes stale when the host refreshes.

### Two architectures to offer the user
1. **Container generates, using the bypass** — install agent CLI + bind-mount
   `~/.claude` + compose volume edit + recreate. Heaviest; unlocks the real
   in-container generation pipeline on the flat-rate plan.
2. **Hermes is the brain, container is the canvas** — Hermes (already on the
   bypass) calls the container's MCP read/write tools; the container is just the
   project store + preview renderer. Zero auth porting, nothing expires; you
   lose the container's specialized skill/critique pipeline.

Present both and let the user pick the model before drafting the gated change.

## RUNNING A BINARY INSIDE A HARDENED CONTAINER (Phase-0 validation lessons)

Before committing to a permanent in-container install (Architecture 1) or any
auth test, validate ephemerally. Hardened images (e.g. Open Design:
`no-new-privileges:true`, read-only rootfs) break the naive `npm i -g` + `docker
cp` approach in ways that look like success or like dead ends but aren't. The
working diagnostic path, in order:

1. **Map writable + exec-capable mounts FIRST.** A hardened container's rootfs is
   read-only; only specific mounts are writable, and a writable mount may be
   `noexec`. Check:
   ```bash
   docker exec <c> sh -lc 'cat /proc/mounts | grep -E " /tmp | /app/.od "'
   ```
   Typical trap: `/tmp` is `tmpfs rw,...,noexec` and the persisted volume
   (`/app/.od`, ext4) is `rw,relatime` (exec OK). Empirically confirm exec with a
   throwaway script in each candidate dir — `printf '#!/bin/sh\necho OK\n' >
   X/t.sh; chmod +x X/t.sh; X/t.sh`. A 240MB binary on a `noexec` mount fails
   with **`Permission denied` even when it is `-rwxr-xr-x` and correctly owned** —
   that error means `noexec`, not a perms problem. Don't chase ownership.

2. **Install into the exec-capable WRITABLE mount, not `/usr/local`.** Read-only
   rootfs makes a global `npm i -g` fail (`EROFS` / can't write
   `~/.npm/_logs`). Use a prefix on the writable+exec volume and redirect the
   npm cache there too:
   ```bash
   docker exec --user 0 -e npm_config_cache=/app/.od/.npmcache <c> \
     npm i -g --prefix /app/.od/clitest @anthropic-ai/claude-code
   ```
   NOTE: anything under the persisted volume SURVIVES recreate — for an ephemeral
   test you MUST clean it up afterward (see step 6).

3. **`docker cp` CANNOT write into a tmpfs mount** (`Could not find the file ...`
   / silently fails on the target dir). Inject files by piping through the
   container's own process instead, which can write tmpfs:
   ```bash
   cat host_file | docker exec -i --user 0 <c> sh -c \
     'mkdir -p /dest/.claude && cat > /dest/.claude/.credentials.json && chown -R 1001:1001 /dest'
   ```
   (`docker cp` into a regular ext4 volume path works fine — the limitation is
   specific to tmpfs.)

4. **Modern `@anthropic-ai/claude-code` ships a per-platform NATIVE binary, not a
   JS `cli.js`.** The npm package installs a tiny `cli-wrapper.cjs` + a
   `bin/claude.exe` shim; the real binary is nested at
   `.../@anthropic-ai/claude-code/node_modules/@anthropic-ai/claude-code-linux-x64-musl/claude`
   (~240MB, Alpine = musl variant). Find it with
   `find <prefix> -path "*linux-*-musl/claude" -type f`. Don't assume a JS entry;
   don't mistake the small wrapper for a mock (verify via the package's
   `description` + `version` against the live `latest` dist-tag).

5. **Auth proof = a REAL round-trip, never a version check.** `claude --version`
   succeeding proves nothing about the token. Force an API call:
   `docker exec <c> sh -lc "HOME=/app/.od/odhome <native> -p 'Reply with exactly: OK'"`.
   A real completion = OAuth token accepted. For refresh behavior, read
   `claudeAiOauth.expiresAt` from the injected creds before and after — a
   sub-second one-shot will NOT trigger a refresh (it consumes the existing token
   within its window), so an unchanged `expiresAt` is inconclusive about
   long-run self-refresh, not proof it never refreshes.

6. **Cleanup is mandatory when you wrote to a persisted volume.** Ephemeral test
   = leave zero residue: `docker exec --user 0 <c> rm -rf
   /app/.od/clitest /app/.od/odhome /app/.od/.npmcache`, then verify (`find / -name
   .credentials.json` returns nothing in-container) and confirm the host token is
   byte-identical and unmodified. Never print actual `accessToken`/`refreshToken`
   values — `expiresAt` (a timestamp) and lengths are fine.

## Rollback
Pure config — no host packages, no processes between calls. To revert: delete
the server block (or set `enabled: false`), restore the `config.yaml.bak-<ts>`,
restart the gateway. Zero residue.
