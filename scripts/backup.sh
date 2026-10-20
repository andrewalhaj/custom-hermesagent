#!/bin/bash
# Daily backup: core Hermes config, memory, skills, scripts, knowledge DB, cron,
# state DB, kanban, profiles, and Supabase knowledge table export.
# Excludes: node/, image_cache/, audio_cache/, node_modules/, .venv/
set -eo pipefail
umask 077
. "$(dirname "$0")/lib/hermes_env.sh"
DEST="$HERMES_HOME/backups"
mkdir -p "$DEST"
TIMESTAMP=$(date +%Y-%m-%d)
ARCHIVE="$DEST/hermes-backup-$TIMESTAMP.tar.gz"

# -- Supabase knowledge export (no embedding vector) ─────────────────
# Columns: all except 'vector'.  Paginated 1000 rows at a time via Range header.
# Non-fatal: a Supabase outage must not block the local backup (reported, exit 1 at the end).
KB_DIR=$(mktemp -d)   # 700, under $TMPDIR
KB_EXPORT="$KB_DIR/knowledge_export_${TIMESTAMP}.json"
KB_FAIL=0
SB_URL="$(hermes_env SUPABASE_URL)" SB_KEY="$(hermes_env SUPABASE_SERVICE_KEY "$(hermes_env SUPABASE_KEY)")" \
KB_EXPORT="$KB_EXPORT" python3 - <<'PYEOF' || KB_FAIL=1
import json, os, urllib.request
url, key = os.environ['SB_URL'], os.environ['SB_KEY']
if not url or not key: raise SystemExit('SUPABASE_URL / key not set')
sel = 'id,text,tags,priority,source,stored_at,context_prefix,body_hash,tsv,metadata,sector_path'
rows, offset, page = [], 0, 1000
while True:
    req = urllib.request.Request(
        f'{url}/rest/v1/knowledge?select={sel}&order=stored_at.asc',
        headers={'apikey': key, 'Authorization': f'Bearer {key}',
                 'Range': f'{offset}-{offset+page-1}'})
    chunk = json.loads(urllib.request.urlopen(req, timeout=120).read())
    rows.extend(chunk)
    if len(chunk) < page: break
    offset += page
with open(os.environ['KB_EXPORT'], 'w') as f:
    json.dump(rows, f, default=str)
PYEOF
if [ "$KB_FAIL" -ne 0 ]; then
  rm -f "$KB_EXPORT"
  echo "ERROR: Supabase knowledge export failed — archive will omit it" >&2
fi

# -- Consistent sqlite snapshots: a raw tar of a WAL db misses the -wal file ──
SNAP=$(mktemp -d)
trap 'rm -rf "$SNAP" "$KB_DIR"' EXIT
python3 - "$SNAP" <<'PYEOF'
import os, sqlite3, sys
for name in ('state.db', 'kanban.db'):
    src = sqlite3.connect('file:' + os.path.expanduser(f'~/.hermes/{name}') + '?mode=ro', uri=True)
    dst = sqlite3.connect(f'{sys.argv[1]}/{name}')
    src.backup(dst)
    dst.close(); src.close()
PYEOF

# tar exit code 1 = minor warnings (socket skipped, file changed while reading);
# exit code 2+ = fatal. Allow 1, fail on 2+.
tar_rc=0
KB_ARGS=(); [ "$KB_FAIL" -eq 0 ] && KB_ARGS=(-C "$KB_DIR" "knowledge_export_${TIMESTAMP}.json")
tar czf "$ARCHIVE" \
  --exclude='node' \
  --exclude='node_modules' \
  --exclude='image_cache' \
  --exclude='audio_cache' \
  --exclude='.venv' \
  --exclude='*.tar.gz' \
  --exclude='*.sock' \
  --exclude='*.session' \
  --exclude='profiles/pre-update-*' \
  -C "$HERMES_HOME" \
  config.yaml \
  memories/ \
  skills/ \
  scripts/ \
  knowledge_db/ \
  cron/ \
  .env \
  kanban/ \
  profiles/ \
  -C "$SNAP" \
  state.db \
  kanban.db \
  "${KB_ARGS[@]}" || tar_rc=$?
[ "$tar_rc" -le 1 ] || { echo "ERROR: tar failed (rc=$tar_rc)" >&2; exit "$tar_rc"; }

chmod 600 "$ARCHIVE"

# Keep last 7 daily backups
find "$DEST" -name 'hermes-backup-*.tar.gz' -mtime +7 -delete

# Off-host copy (cleanup non-fatal; copy is fatal so cron alert fires)
OFFSITE_SSH="$(hermes_env BACKUP_OFFSITE_SSH)"
OFFSITE_DIR="$(hermes_env BACKUP_OFFSITE_DIR)"
if [ -z "$OFFSITE_SSH" ] || [ -z "$OFFSITE_DIR" ]; then
  echo "WARN: BACKUP_OFFSITE_SSH/BACKUP_OFFSITE_DIR not set — skipping off-host copy" >&2
else
  ssh "$OFFSITE_SSH" "mkdir -p '$OFFSITE_DIR' && find '$OFFSITE_DIR' -name 'hermes-backup-*.tar.gz' -mtime +7 -delete" || true
  if ! scp "$ARCHIVE" "$OFFSITE_SSH:$OFFSITE_DIR/"; then
    echo "ERROR: scp to off-host failed — backup NOT copied off-host" >&2
    exit 1
  fi
fi

SIZE=$(du -h "$ARCHIVE" | cut -f1)
echo "OK: $ARCHIVE ($SIZE)"
[ "$KB_FAIL" -eq 0 ] || { echo "WARN: archive lacks the Supabase knowledge export" >&2; exit 1; }
