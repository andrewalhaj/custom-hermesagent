#!/usr/bin/env bash
# Incremental chunk-index rebuild for the Hermes references store.
# flock-guarded (one rebuild at a time), nice 19, logs to ~/.hermes/logs/ref_chunks_build.log
# Does NOT install the crontab entry — see the printed line below.
#
# Hourly crontab line to add (run: crontab -e):
#   0 * * * * ~/.hermes/scripts/build_ref_chunks.sh
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SCRIPT_DIR="$HERMES_HOME/scripts"
PYTHON="${HERMES_VENV:-/usr/local/lib/hermes-agent/venv}/bin/python3"
LOCK_FILE="$HERMES_HOME/cache/ref_chunks/.build.lock"
LOG_FILE="$HERMES_HOME/logs/ref_chunks_build.log"

# Ensure directories exist
mkdir -p "$(dirname "$LOCK_FILE")"
mkdir -p "$(dirname "$LOG_FILE")"

# Acquire exclusive lock; exit 0 (not error) if another build is running
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date -u +%FT%TZ) [ref_chunks_build] Another build already running — skipping." \
        >> "$LOG_FILE"
    exit 0
fi

{
    echo "$(date -u +%FT%TZ) [ref_chunks_build] Starting incremental index build ..."
    cd "$SCRIPT_DIR"
    PYTHONPATH="$SCRIPT_DIR" \
        nice -n 19 "$PYTHON" -m knowledge.ref_chunks build 2>&1
    echo "$(date -u +%FT%TZ) [ref_chunks_build] Done."
} >> "$LOG_FILE" 2>&1

# Print the crontab line for reference
echo "# Crontab line (run: crontab -e to add):"
echo "0 * * * * ~/.hermes/scripts/build_ref_chunks.sh"
