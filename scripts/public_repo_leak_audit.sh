#!/usr/bin/env bash
# Weekly public-repo leak audit. Clones each public repo fresh, runs gitleaks
# (strict local config) + banned-pattern grep + tracked-.db check.
# Watchdog pattern: prints NOTHING when clean; prints findings when dirty.
set -u
REPOS="${REPOS:-hermes-dispatcher custom-hermesagent}"
CONFIG="${GITLEAKS_STRICT_CONFIG:-$HOME/.config/gitleaks/gitleaks.toml}"
if [ ! -f "$CONFIG" ]; then
  echo "leak audit: gitleaks config not found: $CONFIG" >&2
  exit 2
fi
# Banned-pattern regex is built from the local config's rules, so no literals ship in this script.
BANNED=$(python3 - "$CONFIG" <<'PY'
import re, sys
IDS = {"tailscale-cgnat-ip", "known-vps-ips", "tailnet-hostname", "private-domain", "host-codenames", "root-deploy-path"}
out = []
for block in re.split(r"(?m)^\[\[rules\]\]", open(sys.argv[1]).read())[1:]:
    rid = re.search(r"(?m)^\s*id\s*=\s*[\"']([^\"']+)[\"']", block)
    rx = re.search(r"(?m)^\s*regex\s*=\s*'''(.*?)'''", block, re.S)
    if rid and rx and rid.group(1) in IDS:
        out.append(rx.group(1))
print("|".join(out))
PY
)
if [ -z "$BANNED" ]; then
  echo "leak audit: no banned-pattern rules found in $CONFIG" >&2
  exit 2
fi
WORK=$(mktemp -d /tmp/repo-audit.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
FINDINGS=""

for repo in $REPOS; do
  if ! git clone -q --depth 50 "https://github.com/andrewalhaj/$repo.git" "$WORK/$repo" 2>/dev/null; then
    FINDINGS="$FINDINGS\n⚠️ $repo: clone failed (deleted? renamed? network?)"
    continue
  fi
  cd "$WORK/$repo"

  # 1. gitleaks with the strict local config
  if ! gitleaks git --config "$CONFIG" --no-banner --exit-code 1 >/dev/null 2>&1; then
    N=$(gitleaks git --config "$CONFIG" --no-banner --report-format json --report-path /dev/stdout 2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
    FINDINGS="$FINDINGS\n🔴 $repo: gitleaks found $N leak(s) in recent history"
  fi

  # 2. tracked binary DBs
  DBS=$(git ls-files | grep -E '\.(db|sqlite|sqlite3)$' || true)
  [ -n "$DBS" ] && FINDINGS="$FINDINGS\n🔴 $repo: tracked database file(s): $DBS"

  # 3. working-tree banned-pattern grep (belt and suspenders over gitleaks)
  HITS=$(git ls-files -z | xargs -0 grep -l -E "$BANNED" 2>/dev/null | grep -v -E 'gitleaks\.toml' || true)
  [ -n "$HITS" ] && FINDINGS="$FINDINGS\n🔴 $repo: banned pattern in HEAD files: $(echo "$HITS" | tr '\n' ' ')"
done

if [ -n "$FINDINGS" ]; then
  printf '🚨 Weekly public-repo leak audit — FINDINGS:%b\n\nAct on these: rotate any exposed secret first, then scrub (see repo-publication-scrub skill).' "$FINDINGS"
fi
# Clean run: no output → cron stays silent.
