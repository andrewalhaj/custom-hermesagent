#!/bin/bash
# run_one.sh <job-name> [workdir]
# One headless `claude -p` job per invocation. Launch each as its own transient unit:
#   systemd-run --user --unit=job-<tag>-$(date +%H%M%S) --collect --quiet -p MemoryMax=4G \
#     /bin/bash $OUT/run_one.sh <job-name>
# Layout (OUT = report dir, e.g. ~/reports/<task>-<date>):
#   $OUT/prompts/<job-name>.txt   the brief (file list, rules, verification clause)
#   $OUT/<job-name>.json          --output-format json result (0 B until the job exits)
#   $OUT/_status.log              one line per job: start/end, subtype, cost
# Read results with: python3 -c "import json;d=json.load(open('<job>.json'));print(d['subtype'],d['num_turns'],d['total_cost_usd']);print(d['result'])"
set -u
JOB="$1"
WORKDIR="${2:-$HOME/.hermes}"
OUT="$(cd "$(dirname "$0")" && pwd)"
MODEL="${CLAUDE_JOB_MODEL:-sonnet}"
EFFORT="${CLAUDE_JOB_EFFORT:-medium}"
MAX_TURNS="${CLAUDE_JOB_MAX_TURNS:-45}"

# A transient systemd unit does not inherit the interactive PATH: the claude binary
# lives under the Hermes node prefix, so put it first or the job exits before START.
export PATH="$HOME/.hermes/node/bin:/usr/local/bin:/usr/bin:/bin"

# OAuth token read at runtime, never inlined into the unit or the prompt.
export CLAUDE_CODE_OAUTH_TOKEN="$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.claude/.credentials.json")))["claudeAiOauth"]["accessToken"])')"

echo "$(date +%FT%T) START $JOB" >> "$OUT/_status.log"
cd "$WORKDIR" || { echo "$(date +%FT%T) FAIL $JOB bad workdir" >> "$OUT/_status.log"; exit 2; }

claude -p "$(cat "$OUT/prompts/$JOB.txt")" \
  --model "$MODEL" --effort "$EFFORT" --max-turns "$MAX_TURNS" \
  --output-format json \
  --allowedTools 'Read,Write,Edit,Bash,Grep,Glob' \
  --disallowedTools 'WebFetch,WebSearch' \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  --no-session-persistence \
  > "$OUT/$JOB.json" 2> "$OUT/$JOB.err"
RC=$?

SUMMARY="$(python3 -c "import json,sys
try:
    d=json.load(open('$OUT/$JOB.json')); print(d.get('subtype'), d.get('num_turns'), '\$%.2f' % d.get('total_cost_usd', 0))
except Exception as e:
    print('no-json', e)" 2>/dev/null)"
echo "$(date +%FT%T) END $JOB rc=$RC $SUMMARY" >> "$OUT/_status.log"
exit $RC
