# Figma MCP

Figma's remote MCP server brings design-file context (variables, components, layout, code-from-frame, write-to-canvas) into an MCP client. Verified behavior 2026-06.

## The critical trap: OAuth, NOT a PAT

Figma issues two unrelated credential types — do not confuse them:

| Credential | Prefix | Works for |
|---|---|---|
| Personal Access Token (PAT) | `figd_...` | Figma **REST API** only |
| OAuth (browser consent) | — | Figma **MCP server** |

A `figd_...` PAT **cannot** authenticate the MCP server. Probe proof:
```bash
curl -s -X POST https://mcp.figma.com/mcp \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer figd_..." \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
# → "Unauthorized"
```
The MCP server requires a browser OAuth flow (`Allow access` page). There is no header-token shortcut.

## Endpoint facts

- Correct URL: `https://mcp.figma.com/mcp` (StreamableHTTP). **NOT** `/v1/sse` and **NOT** SSE transport — that path 404s.
- A `GET` to `/mcp` returns **405** (method not allowed) = endpoint exists, MCP needs POST. A 404 means wrong path.
- **Client allowlist:** Figma gates the MCP to approved clients only (VS Code, Cursor, Claude Code, Codex, Gemini CLI, Xcode). Arbitrary MCP clients are rejected — there's a waitlist form to add new ones.

## Consequence for Hermes (headless)

Hermes on a headless server **cannot** use the Figma MCP:
1. No browser → can't complete OAuth.
2. Hermes isn't on Figma's approved-client list anyway.

Do not wire `figma` into `~/.hermes/config.yaml mcp_servers`. It will sit `enabled: true` but never authenticate. If a user asks, explain both blockers rather than leaving a dead entry.

## Claude Code install (the supported path)

Preferred = the official plugin, which bundles MCP config + agent skills and triggers OAuth:
```bash
claude plugin install figma@claude-plugins-official
# then /plugin → Installed tab → figma → Enter → Allow access (browser)
```
This MUST be run interactively from the user's own terminal — the OAuth redirect needs a browser. A headless agent shell can't complete it.

Manual fallback (still needs OAuth on first connect):
```bash
claude mcp add --scope user --transport http figma https://mcp.figma.com/mcp
```
`claude mcp list` will show `✘ Failed to connect` until OAuth is completed — that's expected, not a config error.

## Plugin marketplace gotcha (this host)

`claude plugin install figma@claude-plugins-official` failed with "marketplace not found"; `claude plugin marketplace add <github-url>` then failed because git HTTPS had no credential helper and SSH had no key for github.com. On a box without GitHub git auth, the plugin-marketplace route is blocked — fall back to `claude mcp add` (above) and complete OAuth interactively, or fix git auth first (`gh auth login` / SSH key).
