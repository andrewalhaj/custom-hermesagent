#!/usr/bin/env bash
# LH02 Ollama health watchdog
# Runs every 15 min via cron (no_agent=true)
# Silent on healthy — stdout only on failure (triggers Telegram/Discord alert)
#
# Checks, in order:
#   1. Reachability  — /api/tags responds
#   2. Models present — at least one model is installed
#   3. Live throughput — actually generates tokens at an acceptable rate.
#      This is the check that matters: a thrashing 72B returned HTTP 200 on
#      /api/tags while taking ~57s/token. Reachability alone is a false-negative
#      trap; only a real generation probe catches a swap-thrashing node.
#
# Recovery: on check 1 failure, auto-restarts ollama via SSH kill → launchd
# KeepAlive respawn (proven 2026-06-17). Reports outcome either way.

set -u -o pipefail
. "$(dirname "$0")/lib/hermes_env.sh"
HOST="$(hermes_require STUDIO_OLLAMA_HOST)" || exit 1
STUDIO_SSH="$(hermes_require STUDIO_SSH)" || exit 1
TAGS_URL="http://${HOST}/api/tags"
GEN_URL="http://${HOST}/api/generate"
PROBE_MODEL="qwen2.5-32b-64k"   # the delegation/cron workhorse
CONNECT_TIMEOUT=10
GEN_MAX_TIME=60                 # generation probe hard ceiling (s)
MIN_TOK_PER_SEC=5               # below this = thrashing/degraded
SSH_KEY="${HOME}/.ssh/id_ed25519"
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes -o IdentitiesOnly=yes -i "$SSH_KEY")

_restart_ollama() {
    # Kill the ollama serve process — launchd KeepAlive=true respawns it
    ssh "${SSH_OPTS[@]}" "$STUDIO_SSH" \
        'kill $(pgrep -f "ollama serve" | head -1) 2>/dev/null; true' 2>/dev/null
    sleep 12
    # Recheck
    curl -sf --connect-timeout "$CONNECT_TIMEOUT" "$TAGS_URL" > /dev/null 2>&1
    return $?
}

# 1. Reachability
response=$(curl -sf --connect-timeout "$CONNECT_TIMEOUT" "$TAGS_URL" 2>/dev/null)
if [ $? -ne 0 ]; then
    echo "⚠️ LH02 Ollama UNREACHABLE at ${HOST} — attempting auto-restart..."
    _restart_ollama
    if [ $? -eq 0 ]; then
        echo "✅ LH02 Ollama auto-restarted successfully and is now responding. Root cause: process needed respawn (launchd KeepAlive triggered)."
    else
        echo "❌ LH02 Ollama UNREACHABLE and auto-restart FAILED. Manual intervention required. SSH: ssh ${STUDIO_SSH} and check launchctl list | grep ollama, /tmp/ollama.log"
    fi
    exit 0
fi

# 2. At least one model installed
model_count=$(echo "$response" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('models',[])))" 2>/dev/null)
if [ -z "$model_count" ] || [ "$model_count" -eq 0 ]; then
    echo "⚠️ LH02 Ollama is UP but NO models installed. Run: ssh ${STUDIO_SSH} ollama list"
    exit 0
fi

# 3. Live throughput probe — generate a few tokens, measure eval rate.
probe=$(curl -sf --max-time "$GEN_MAX_TIME" "$GEN_URL" \
    -d "{\"model\":\"${PROBE_MODEL}\",\"prompt\":\"Reply with exactly: OK\",\"stream\":false,\"options\":{\"num_predict\":8}}" 2>/dev/null)
probe_exit=$?

if [ $probe_exit -ne 0 ]; then
    echo "⚠️ LH02 generation probe FAILED/timed out (>${GEN_MAX_TIME}s) for ${PROBE_MODEL}. Node reachable but not generating — likely swap-thrashing or model load failure. Delegation/cron will hang. Check LH02 memory pressure."
    exit 0
fi

rate=$(echo "$probe" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    ec=d.get('eval_count',0); ed=d.get('eval_duration',0)
    if not ec or not ed:
        print('NODATA'); sys.exit(0)
    print(f'{ec/(ed/1e9):.1f}')
except Exception:
    print('PARSEFAIL')
" 2>/dev/null)

if [ "$rate" = "NODATA" ] || [ "$rate" = "PARSEFAIL" ] || [ -z "$rate" ]; then
    echo "⚠️ LH02 generation probe returned no timing data for ${PROBE_MODEL}. Response malformed — investigate Ollama on LH02."
    exit 0
fi

# Compare rate against threshold (awk handles float compare)
if awk "BEGIN{exit !($rate < $MIN_TOK_PER_SEC)}"; then
    echo "⚠️ LH02 DEGRADED: ${PROBE_MODEL} generating at ${rate} tok/s (threshold ${MIN_TOK_PER_SEC}). Likely memory thrashing — a model too large for VRAM, or competing load. Delegation/cron will be painfully slow. Check LH02: ssh ${STUDIO_SSH} 'sysctl vm.swapusage; curl -s localhost:11434/api/ps'"
    exit 0
fi

# Healthy — stay silent (no_agent=true: empty stdout = no delivery)
exit 0
