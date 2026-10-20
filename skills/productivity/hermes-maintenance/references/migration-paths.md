# Migration Paths Off Single-Host

> ⚠️ **DEPRECATED (2026-06-05).** Manifest was fully uninstalled — all containers, compose dirs, nginx configs, and Railway DB purged from both hosts. This roadmap is retained for historical reference only.

## Original Roadmap (June 2026)

Roadmap for moving Manifest and the Hermes scheduler off single-host assumptions. Current state, phased migration, rollback, and effort estimates.

## Current Topology

```
Single host:
  [Hermes Gateway] ─→ [Manifest: Docker container on 127.0.0.1:2099]
                      [PostgreSQL: Docker container, named volume 'manifest_pgdata']
                      [Scheduler: in-process with Hermes]
                      [Cron state: ~/.hermes/cron/jobs.json, 5KB]
```

Both bottlenecks converge on PostgreSQL — it's the prerequisite for everything.

---

## Manifest — Horizontal Scaling

### Phase 1: Externalize PostgreSQL (★ prerequisite)

Pull postgres out of Docker Compose. Managed PostgreSQL (Neon free tier) recommended — handles backups, patching, point-in-time recovery. The free 3GB tier is ~60x what Manifest needs.

**Steps:**
1. `pg_dump` current Docker postgres → `manifest_backup.sql`
2. `psql <neon-connection-string> < manifest_backup.sql`
3. Update `~/manifest/.env`: `DATABASE_URL=<neon-string>`
4. `docker compose down && docker compose up -d`
5. Verify: `curl localhost:2099/api/v1/health` → 200

**Rollback:** Revert `.env` to `DATABASE_URL=postgresql://manifest:manifest@postgres:5432/manifest`, restart compose. Docker volume `manifest_pgdata` is never deleted — zero data loss. Downtime <2 min.

**Pitfall: pg_dump without exclusions produces massive dumps.** Manifest's `message_recordings`, `agent_messages`, and `reasoning_content_cache` tables can total 250MB+ of session metadata. Exclude them with:
```bash
docker exec mnfst-postgres-1 pg_dump -U manifest --no-password \
  --exclude-table-data='message_recordings' \
  --exclude-table-data='reasoning_content_cache' \
  --exclude-table-data='agent_messages' \
  manifest > manifest_backup.sql
```
Result: ~66KB of actual config (tenants, agents, API keys, tiers) vs 260MB raw.

**Pitfall: Neon requires the pooler endpoint, not the direct connection string.** The standard Neon connection uses the pooler host (`ep-xxx-pooler.us-east-2.aws.neon.tech`) with `channel_binding=require`. The non-pooler endpoint will fail silently — Manifest starts but database writes hang. Use the connection string from the Neon dashboard's "Pooled connection" tab.

**Pitfall: `.env` DATABASE_URL edits must produce an active line, not a comment.** The default `.env` has `# DATABASE_URL=...` (commented out with explanation). Search-and-replace on the commented line won't work because the `#` makes it a comment. Append `DATABASE_URL=<neon-string>` as a new uncommented line at the end of the file. Use Python heredoc, not sed/inline `-c`.

**Pitfall: Neon free tier connection limits.** Direct `psql` connections can fail with "password authentication failed" when Manifest's connection pool is active — the free tier throttles concurrent connections aggressively. This is not a credential bug; verify via the Manifest health endpoint instead. For post-migration verification, `curl localhost:2099/api/v1/health` is the ground truth — if Manifest reports healthy, the database is reachable.

### Phase 2: Second Manifest instance

Same compose stack on a second host, same `DATABASE_URL` pointing to external postgres. Verify with health check.

### Phase 3: Load balancer

nginx or HAProxy in front of both Manifest instances. Health check on `/api/v1/health`. Round-robin.

### Phase 4: Point Hermes at LB

Update `custom_providers.custom.base_url` in `config.yaml` to the LB address.

**Total effort:** ~4 hours. No code changes — all ops.

---

## Scheduler — Decoupling from Hermes

### Option A: Documented Recovery (cheapest, 80% value)

Scheduler state is 5KB JSON backed up daily. Recovery: restore `jobs.json` from backup, start Hermes on new host. Max data loss: one missed tick.

**Effort:** 30 min to write + test recovery doc.

### Option B: Externalize State + Leader Election (real HA)

Move scheduler state to PostgreSQL, add advisory lock for leader election. Multiple Hermes instances, only one fires cron.

**Effort:** 1-2 days. Touches core code.

### Option C: Standalone Scheduler Binary (middle ground)

Extract scheduler into its own process. Single binary, reads `jobs.json`, no LLM, no gateway. Run on a dedicated lightweight host.

**Effort:** 1 day. Decouples from Hermes restarts but still single-host for scheduler itself.

---

## Recommendation Order

1. **Immediate:** Manifest Phase 1 (externalize postgres). Unblocks everything.
2. **Next:** Scheduler Option A (documented recovery). Low effort, covers practical risk.
3. **When needed:** Manifest Phases 2-4. Only when you have a second host and want multi-instance.
4. **Long-term:** Scheduler Option B or C. Only with multi-Hermes or high reliability requirements.

Postgres externalization is step zero — without it, neither Manifest replication nor scheduler HA is possible.

---

## Execution History (June 2-3, 2026)

**Attempt 1 (June 2):** Neon → VPS postgres → dual Manifest. Failed: Neon
dump too large, VPS scram auth, instances on separate DBs. Rolled back.

**Attempt 2 (June 3):** Railway direct. Full dump restored. VPS .env had
`***` placeholder — fixed. All 4 phases complete with LB round-robin verified.

**Status:** Phase 1-4 complete. Phase 5 (Postgres HA, LB HA) deferred.
Scheduler Option A documented and tested. Off-host backups active (VPS sync).

Full plan at `~/.hermes/references/migration-paths-off-single-host.md`.
Recovery procedure at `~/.hermes/references/scheduler-recovery-procedure.md`.

**Key pitfalls encountered:**
- **VPS postgres TCP auth** — `pg_hba.conf` required `scram-sha-256` but user
  passwords weren't stored in that format. Fix: set to `trust` for Docker
  internal networks. Undetected until manifest crashed.
- **Context compaction killed the plan** — the Phase 1-4 draft was discussed
  in-session, got compacted out, and the agent couldn't reference it. Always
  save architecture plans to durable files BEFORE context compaction runs.
- **Placeholder passwords in .env** — when .env files are copied/synced across
  hosts, literal `***` placeholders can end up in production configs. Verify
  password length (should be 32 chars for Railway) on each host.
- **Neon → VPS migration chain** — migrating twice (local → Neon → VPS)
  introduced unnecessary complexity. Direct local → Railway was faster.
