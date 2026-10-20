# Zapier MCP

Connects Hermes to 9,000+ apps (Gmail, Google Calendar, Slack, etc.) via Zapier's hosted MCP server. Verified working 2026-06.

## Endpoint & auth

- Connect URL shape: `https://mcp.zapier.com/api/v1/connect?token=<TOKEN>`
  - The bare MCP path `https://mcp.zapier.com/api/mcp/mcp` also speaks the protocol (used `Authorization: Bearer <token>` in a manual probe).
- **The token is embedded in the URL.** When `hermes mcp add` asks "Does this server require authentication?", answer **n** — there is no separate header. (Answering Y and pasting the token also works but is redundant.)
- The token is a base64 `id:secret` pair. It lands plaintext in `config.yaml` (+ its backups) and in session DB / gateway+agent logs the moment it's pasted in chat. Treat as exposed; rotate in Zapier when the server is removed.

## Read-only probe before committing (no gated write)

Prove the token + tool surface with curl first:
```bash
URL="https://mcp.zapier.com/api/v1/connect?token=<TOKEN>"
curl -s -X POST --max-time 20 "$URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"hermes","version":"1.0"}}}'
```
Success = SSE `event: message` with `serverInfo:{name:"zapier","title":"Zapier MCP"}`. Follow with a `tools/list` POST to see the meta-tools.

## Meta-tool architecture (key mental model)

Zapier MCP does NOT expose one tool per app. It ships ~14 **meta-tools** and you operate apps THROUGH them:
- `discover_zapier_actions` — search apps/actions to enable.
- `enable_zapier_action` / `disable_zapier_action` — add/remove a specific action (e.g. "Gmail: Send Email").
- `list_enabled_zapier_actions` — CALL FIRST before any execute; shows what's actually wired.
- `execute_zapier_read_action` / `execute_zapier_write_action` — run a search/read or a write/create.
- `get_configuration_url` — returns the Zapier dashboard URL where the user manages connections.
- `list_zapier_skills` / `get_zapier_skill` / `create/update/delete_zapier_skill` — saved Zapier workflows.
- `send_feedback`, `auto_provision_mcp`.

Consequence: right after `hermes mcp add`, **zero apps are enabled** — `list_enabled_zapier_actions` returns empty until you enable specific actions.

## App OAuth happens on Zapier's side, not from Hermes

Connecting Gmail / Google Calendar to the user's Google account is an OAuth consent in the **Zapier dashboard** (`get_configuration_url`). The agent CANNOT OAuth into the user's Google account from the host. Flow: user authorizes the app in Zapier → agent `enable_zapier_action` for the wanted actions → agent calls `execute_zapier_*`.

## Treat every write as gated

`execute_zapier_write_action` sends real email / creates real calendar events on the user's behalf. Per this user's standing rule, show the drafted action and wait for greenlight before executing any write — same posture as infra changes.
