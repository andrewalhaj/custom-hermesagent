#!/usr/bin/env bash
# heal-skill-descriptions.sh — on_session_start hook.
#
# Purpose: a core `hermes update` overwrites /usr/local/lib/hermes-agent/skills
# and re-introduces >60-char skill descriptions (the trigger-surface cliff).
# This guard re-heals them on the first session after any update, then stays
# silent and near-zero-cost on every subsequent session (nothing to do).
#
# Wire protocol: receives a JSON payload on stdin (ignored), prints nothing
# (silent no-op) unless it heals, in which case it emits a {"context": ...}
# note so the session knows reconciliation happened. Exit 0 always — a hook
# must never block session start.
set -euo pipefail
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
RECON="$HERMES_HOME/scripts/skill_desc_reconcile.py"
cat >/dev/null 2>&1 || true   # drain stdin payload

# Fast guard: exit 0 immediately if nothing is truncated (the common case).
if python3 "$RECON" --quiet-exit-code 2>/dev/null; then
    exit 0
fi

# Something is truncated (likely a post-update wipe) — heal it.
OUT="$(python3 "$RECON" --apply 2>&1 | tail -1 || true)"
# Emit a context note (valid JSON) so the session is aware; never block.
printf '{"context": "Auto-healed skill descriptions on session start: %s"}\n' "${OUT//\"/}"
exit 0
