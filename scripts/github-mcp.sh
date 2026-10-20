#!/usr/bin/env bash
# GitHub MCP Server wrapper — reads PAT from file, passes to system Docker engine
# Uses -c default to target the always-on system Docker Engine, not Docker Desktop
set -euo pipefail
PAT_FILE="${HERMES_HOME:-$HOME/.hermes}/.github-pat"
if [[ ! -f "$PAT_FILE" ]]; then
  echo '{"jsonrpc":"2.0","error":{"code":-32000,"message":"PAT file not found: '"$PAT_FILE"'"}}' >&2
  exit 1
fi
PAT=$(cat "$PAT_FILE")
exec docker -c default run -i --rm \
  -e "GITHUB_PERSONAL_ACCESS_TOKEN=${PAT}" \
  ghcr.io/github/github-mcp-server \
  stdio "$@"
