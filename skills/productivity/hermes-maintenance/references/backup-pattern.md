# Backup Pattern: Core Config Backup via no_agent Cron

Concrete pattern for a zero-token daily backup of Hermes core state.

## Script: `backup.sh`

```bash
#!/bin/bash
DEST="$HOME/.hermes/backups"
mkdir -p "$DEST"
TIMESTAMP=$(date +%Y-%m-%d)
ARCHIVE="$DEST/hermes-backup-$TIMESTAMP.tar.gz"

tar czf "$ARCHIVE" \
  --exclude='node' \
  --exclude='image_cache' \
  --exclude='audio_cache' \
  --exclude='profiles/stable-*' \
  --exclude='*.tar.gz' \
  -C "$HOME/.hermes" \
  config.yaml \
  memories/ \
  skills/ \
  scripts/ \
  knowledge_db/ \
  cron/ \
  .env 2>/dev/null

# Keep last 7 daily backups
find "$DEST" -name 'hermes-backup-*.tar.gz' -mtime +7 -delete

if [ $? -ne 0 ]; then
  echo "ERROR: backup failed"
  exit 1
fi

SIZE=$(du -h "$ARCHIVE" | cut -f1)
echo "OK: $ARCHIVE ($SIZE)"
```

## What to include vs exclude

**Include:** config.yaml, memories/, skills/, scripts/, knowledge_db/, cron/ definitions, .env
**Exclude:** node/ (204MB Node.js), image_cache/, audio_cache/, profile/stable-* snapshots (225MB each), existing .tar.gz files

Rationale: The excluded dirs are either versioned with Hermes core (node/) or reproducible (image_cache, audio_cache, profile snapshots). Core config and data are the irreplaceable bits.

## Cron scheduling

Use `no_agent=true` — the script IS the job. Zero token cost, zero LLM involvement.

```
cronjob create
  action=create
  name="Daily Hermes Backup"
  schedule="0 3 * * *"          # 03:00 UTC daily
  script="backup.sh"
  no_agent=true
  deliver="local"                # silent unless failure
```

`deliver="local"` means no message sent on success. On failure (non-zero exit), the scheduler alerts the user with the error output.

## Retention

7 rolling daily backups via `find -mtime +7 -delete`. At ~2-3MB each, 7 backups = ~20MB. Adjust `+7` for longer retention.

## Verification

```bash
# Check latest backup
ls -lh ~/.hermes/backups/hermes-backup-*.tar.gz | tail -1
# Inspect contents
tar tzf ~/.hermes/backups/hermes-backup-$(date +%Y-%m-%d).tar.gz | head -20
```
