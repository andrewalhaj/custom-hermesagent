# Figma MCP — verified wiring workflow (and what does NOT work)

Session-verified 2026-06-20. Figma's remote MCP is OAuth-gated and client-whitelisted.
This is the single hardest "wire an MCP" case encountered — document it so the next
session doesn't re-walk the same dead ends.

## The core facts (each one cost a wrong turn)

1. **PAT does not authenticate the MCP.** A Figma Personal Access Token (`figd_...`)
   is for the Figma REST API only. The MCP server requires **browser OAuth**.
   Proof: `curl -s -X POST https://mcp.figma.com/mcp -H 'Authorization: Bearer <PAT>'
   -d '{"jsonrpc":"2.0","id":1,"method":"initialize",...}'` → `Unauthorized`.
   Do NOT spend time on `-H "Authorization: Bearer <PAT>"` for Figma.

2. **Endpoint + transport.** Correct: `https://mcp.figma.com/mcp`, **HTTP** transport.
   - `https://mcp.figma.com/v1/sse` → **404** (wrong path).
   - `GET https://mcp.figma.com/mcp` → **405** (exists, wrong method — MCP needs POST).
     405 is the "URL is correct" signal.

3. **Client whitelist.** Figma restricts the remote MCP to approved clients:
   VS Code, Cursor, Claude Code, Codex, Gemini CLI, Xcode. A headless agent
   (Hermes) is not approved and has no browser → **cannot be wired into Hermes.**
   If a `figma:` block was added to `~/.hermes/config.yaml`, remove it.

## Install into Claude Code (the path that works)

Claude binary on the Mac Mini: `~/.hermes/node/bin/claude` (not in system PATH).

```bash
# 1. (headless) give git an HTTPS credential so marketplace clones work
git config --global credential.helper store
echo "https://x-access-token:$(cat ~/.hermes/.github-pat)@github.com" > ~/.git-credentials

# 2. add the community marketplace (indexes vendor plugins by name)
claude plugin marketplace add https://github.com/anthropics/claude-plugins-community

# 3. install Figma's official plugin. The community entry named `figma-test`
#    has source.url = github.com/figma/mcp-server-guide (Figma's official repo,
#    internal plugin name `figma`, v2.x). `figma@claude-community` does NOT exist;
#    `figma-test@claude-community` is the correct install target.
claude plugin install figma-test@claude-community

# 4. verify
claude plugin list                      # figma-test ... ✔ enabled
claude mcp list | grep -i figma         # plugin:figma:figma ... ! Needs authentication
```

`! Needs authentication` is expected and correct at this point.

## The final OAuth step is INTERACTIVE — an agent cannot do it

Tell the user to run, in a real terminal on the Mac Mini:
```
~/.hermes/node/bin/claude        # launch Claude Code
/plugin                              # open plugin UI
# → Installed tab → select figma-test → Enter → browser opens → "Allow access" in Figma
```
After auth, `claude mcp list` shows `figma: ✔ Connected`.

## Why not the documented `claude plugin install figma@claude-plugins-official`

That marketplace name is not configured by default and `marketplace update
claude-plugins-official` reports "not found". The official Anthropic repo
`anthropics/claude-code-marketplaces` 404s. The working index is
`anthropics/claude-plugins-community`.

## Finding any vendor plugin in the community marketplace

```bash
curl -s -H "Authorization: token $(cat ~/.hermes/.github-pat)" \
  https://raw.githubusercontent.com/anthropics/claude-plugins-community/main/.claude-plugin/marketplace.json \
  | python3 -c "import json,sys; d=json.load(sys.stdin);
[print(p['name'],'->',p.get('source',{}).get('url','')) for p in d['plugins'] if 'figma' in p['name'].lower()]"
```
Match on `source.url` pointing at the vendor's own GitHub org to pick the official entry.
