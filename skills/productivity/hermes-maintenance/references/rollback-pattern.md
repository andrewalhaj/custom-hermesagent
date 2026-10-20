# Minimal Rollback Pattern

When a migration or change was narrow (one config line, one routing change, one cron job), full profile-based rollback is overkill. Use this minimal pattern instead.

## When to use

- The change was a single `base_url` edit, provider swap, or similar one-liner
- No data was migrated (no dump/restore, no schema changes)
- The rollback is literally: revert the config line, remove any dependent cron jobs
- Profile snapshots exist but are stale or post-migration — you don't want to revert everything

## When NOT to use

- Multi-file config changes (use profile revert)
- Database migrations (use pg_restore or volume rollback)
- Hermes core updates (use `hermes profile use`)

## Pattern

### Step 1: Identify the one config line that changed

```bash
# The migration added this:
hermes config set model.base_url http://<vps-ip>:8080/v1
# And possibly a provider variant:
hermes config set providers.manifest-vision.base_url http://<vps-ip>:8080/v1
```
(real address: memory_search "Tailnet")

### Step 2: Identify dependent cron jobs

Any cron that depends on the removed infrastructure (VPS watchdog, remote health checks) must be removed. Don't leave orphan cron jobs pinging dead infrastructure.

```bash
# In-session:
cronjob(action='list')
cronjob(action='remove', job_id='<id>')
```

### Step 3: Execute the revert

```bash
hermes config set model.base_url http://localhost:2099/v1
hermes config set providers.manifest-vision.base_url http://localhost:2099/v1
```

### Step 4: Verify

```bash
curl -s http://localhost:2099/api/v1/health
grep 'base_url:' ~/.hermes/config.yaml | head -3
```

### Step 5: Update memory

Infrastructure facts in memory go stale. Audit and replace:
- Database URLs (Neon → VPS → local)
- Cron job counts
- Provider names
- Disk sizes

## Real Example (June 3, 2026)

Migration added nginx LB at VPS → rolled back to single-host localhost:

```
Before: base_url → <vps-ip>:8080/v1 (nginx LB → two Manifests)
After:  base_url → localhost:2099/v1 (single Manifest, local postgres)

Changes:
  hermes config set model.base_url http://localhost:2099/v1
  hermes config set providers.manifest-vision.base_url http://localhost:2099/v1
  cronjob remove 1292d7fedc5c (VPS Manifest Watchdog)

Preserved:
  Backup cron, session pruning, heartbeat cron, KB dedup — all local, no VPS dependency
  Docker volume manifest_pgdata — pre-migration data intact, never touched
  VPS Docker stack — containers still running but idle, zero traffic routed to them

Result:   6 cron jobs → 5, 2 base_url refs updated, zero data loss
```

## Pitfalls

- **Don't assume Docker volumes were destroyed.** `pg_dump` reads data; it doesn't delete it. After a database migration, the source Docker volume still has the pre-migration data. Verify with `docker volume ls | grep postgres` before declaring data lost.
- **Don't forget provider-specific base_url overrides.** The main `model.base_url` and any custom provider base_urls must both be reverted. `grep` for the old URL to catch all instances.
- **Infrastructure cron jobs become orphans silently.** The VPS watchdog would have kept pinging a dead endpoint indefinitely, generating noise for no reason. Always audit cron jobs after infrastructure rollback.
