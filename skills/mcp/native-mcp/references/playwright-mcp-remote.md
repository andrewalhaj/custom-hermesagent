# Playwright MCP — Remote Browser Access via SSH Tunnel

## Problem

Playwright MCP only accepts connections from `localhost` — it rejects remote IPs even with `--host 0.0.0.0`:
```
Access is only allowed at localhost:9378
```

When Hermes runs on a different machine than the browser, direct HTTP transport fails (403 Forbidden).

## Solution: SSH Tunnel

Run Playwright MCP **without** `--host 0.0.0.0` (keep it localhost-only) on the browser machine, then open a persistent SSH tunnel from the Hermes server so connections appear local to Playwright.

### Step 1: Start Playwright on the browser machine

```bash
npx @playwright/mcp@latest --port 9378
```

(No `--host` flag — keep it localhost-only.)

### Step 2: Open SSH tunnel from Hermes server

```bash
ssh -N -L 9378:localhost:9378 user@browser-machine -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes
```

- `-N`: no remote command
- `-L 9378:localhost:9378`: forward Hermes localhost:9378 → browser machine localhost:9378
- `-o ServerAliveInterval=30`: keepalive every 30s to prevent timeout
- `-o ExitOnForwardFailure=yes`: fail fast if the port is already in use

Run this as a background process (it never exits):
```bash
# In Hermes terminal:
terminal(background=true, command='ssh -N -L 9378:localhost:9378 user@browser-ip -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes')
```

### Step 3: Verify

```bash
curl -s --max-time 5 http://localhost:9378/ -o /dev/null -w "%{http_code}"
# Should return 400 (MCP endpoint, not 403 or connection refused)
```

### Step 4: Configure Hermes

```yaml
mcp_servers:
  playwright-mac:
    url: http://localhost:9378
    timeout: 60
    connect_timeout: 30
```

Then restart the gateway. Tools appear as `mcp_playwright_mac_browser_*`.

### Step 5: Use in a new session

MCP tools only register on gateway startup — they won't appear in the session that did the restart. Send a new message to get a fresh session with the tools loaded.

### Full tools list (23 tools)

`browser_close`, `browser_resize`, `browser_console_messages`, `browser_handle_dialog`, `browser_evaluate`, `browser_file_upload`, `browser_drop`, `browser_fill_form`, `browser_press_key`, `browser_type`, `browser_navigate`, `browser_navigate_back`, `browser_network_requests`, `browser_network_request`, `browser_run_code_unsafe`, `browser_take_screenshot`, `browser_snapshot`, `browser_click`, `browser_drag`, `browser_hover`, `browser_select_option`, `browser_tabs`, `browser_wait_for`

### Pitfalls

- **Playwright must stay running.** Closing the terminal kills it. Use `&` or a dedicated window.
- **Tunnel must survive.** If the SSH connection drops, tools silently fail. Monitor with `process(action='poll')`.
- **`browser_run_code_unsafe` is the escape hatch.** When ref-based selectors fail (minified bundles strip `data-` attributes), use `page.getByRole('button', { name: 'Chat' }).click()` or raw `page.evaluate()`.
- **Screenshots save to the browser machine's filesystem**, not the Hermes server. Can't directly `read_file` them — use `browser_snapshot` for text-based inspection instead.
- **The `console_messages` tool retrieves ALL messages since page load**, not just since last call. Use `clear=true` and then do your actions, then call again for only new messages.
