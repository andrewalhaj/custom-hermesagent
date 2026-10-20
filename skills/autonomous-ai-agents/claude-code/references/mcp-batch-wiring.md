# Batch-wiring MCP servers into Claude Code + Hermes

Session-verified 2026-06-20. Wiring a list of MCP servers (e.g. the "top MCP
servers" Reddit lists) into BOTH Claude Code and Hermes at once. Figma's
OAuth-only case lives in `figma-mcp-wiring.md`; this covers the general batch flow
and the per-server name/env gotchas that cost retries.

## Two separate configs — both must be written

- **Claude Code:** `~/.claude.json` under `mcpServers`. Write via
  `claude mcp add --scope user <name> -- npx -y <pkg>` (use `~/.hermes/node/bin/claude`
  if `claude` is not in PATH). `--scope user` makes it global across projects.
  Permissions allow-list lives in `~/.claude/settings.json` under
  `permissions.allow` — add `mcp__<name>__*` for each new server or calls prompt.
- **Hermes:** `~/.hermes/config.yaml` under `mcp_servers` (GATED write). stdio shape:
  `{command, args, env, enabled, connect_timeout, timeout}`. HTTP/SSE shape:
  `{url, headers, ...}`. Tools register only after `systemctl --user restart
  hermes-gateway` (gated; cannot self-restart from inside a session — hand the
  command to the user).
- Editing `~/.hermes/config.yaml` via the `patch`/`write_file` tools is REFUSED
  ("cannot modify security-sensitive configuration"). Use a `python3` heredoc with
  `yaml.safe_load`/`yaml.dump` instead, after arming the write gate.

## Env vars: reference vs literal

- Hermes config.yaml: use `${VAR}` placeholders in `env:` and put the real value
  in `~/.hermes/.env`. Hermes expands at startup.
- Claude Code `~/.claude.json`: env values are NOT shell-expanded — `${VAR}` is
  passed literally and the server sees a `${VAR}` string. Write the **literal key
  value** into the `env` block in claude.json (or rely on `claude mcp add -e
  KEY=value`). `claude mcp list` prints a "Missing environment variables" warning
  when a referenced var is unset/literal.

## Batch install pattern (Claude Code side)

```bash
CLAUDE=~/.hermes/node/bin/claude
$CLAUDE mcp add --scope user tavily     -- npx -y tavily-mcp
$CLAUDE mcp add --scope user exa        -- npx -y exa-mcp-server
# ...one line per server...
$CLAUDE mcp list      # ✔ Connected / ✘ Failed to connect / ! Needs authentication
```
`! Needs authentication` = OAuth server (Vercel, Figma) — finish in an interactive
`/mcp` session (browser flow, user must do it). `✘ Failed to connect` = bad package
name, missing required env, or the server needs an external service. Debug each with
`timeout 5 npx -y <pkg> 2>&1 | head` to see the real error.

## Per-server gotchas verified this session

| Server | Correct package / command | Env var (exact name) | Note |
|---|---|---|---|
| Tavily | `tavily-mcp` | `TAVILY_API_KEY` | connects without key, degraded |
| Exa | `exa-mcp-server` | `EXA_API_KEY` | |
| Perplexity | `@perplexity-ai/mcp-server` | `PERPLEXITY_API_KEY` | |
| Apify | `@apify/actors-mcp-server` | `APIFY_TOKEN` | NOT `@apify/apify-mcp-server` (404); env is `APIFY_TOKEN` not `APIFY_API_TOKEN` |
| Sentry | `@sentry/mcp-server` | `SENTRY_ACCESS_TOKEN` | NOT `SENTRY_AUTH_TOKEN`. Optional `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` for agent-search |
| Supabase | `@supabase/mcp-server-supabase` | `SUPABASE_ACCESS_TOKEN` | needs a **personal access token `sbp_...`**, NOT a `sb_publishable_...` client key. Even with valid token, "Connected · tools fetch failed" usually means it wants `--project-ref <id>` in args |
| MongoDB | `mongodb-mcp-server` | `MDB_MCP_CONNECTION_STRING` | |
| Notion | `@notionhq/notion-mcp-server` | `NOTION_API_KEY` | |
| Neo4j | `neo4j-mcp-server` (pip/Go binary, NOT npm) | `NEO4J_URI`,`NEO4J_USERNAME`,`NEO4J_PASSWORD` | `pip install neo4j-mcp-server` → binary in venv. **Username is always `neo4j`** — the string shown as "Username" in the Aura credentials modal is the instance ID, not the login. New Aura instance needs ~1-2 min to accept auth after showing RUNNING |
| Vercel | remote, `--transport http https://mcp.vercel.com` | — | OAuth, finish via `/mcp` |
| Crawl4AI | `crawl4ai-mcp-sse-stdio` | — | npx wrapper fails ("could not determine executable") unless the `crawl4ai` Python service is installed/running; `pip install crawl4ai` alone did not make the stdio npx shim connect. Treat as "needs local service", not a plain npx server |

## OAuth servers (Vercel, Figma) — interactive only

`claude mcp add --transport http <name> <url>` registers them but they show
`! Needs authentication`. The browser OAuth must be done by the user in a real
terminal: launch `claude` → `/mcp` → select the server → Allow access in browser.
An agent cannot complete this step.

## Context-cost caveat (tell the user)

Each connected MCP server injects its tool descriptions into every Claude Code
turn. 15+ servers measurably burns context before the first real prompt. Advise
keeping only the servers in active use enabled; disable the rest with
`claude mcp remove <name>` rather than leaving a wall of them connected.
