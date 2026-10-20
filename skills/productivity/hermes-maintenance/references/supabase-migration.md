# Supabase Migration — Connection Quirks

Migrating Manifest PostgreSQL to Supabase free tier. Documented from a failed
attempt on 2026-06-03 (297MB dump, PostgreSQL 16→17.6).

## Connection Details

- **Host:** `db.<ref>.supabase.co:5432` (direct), port 6543 (PgBouncer pooler)
- **IPv6 only:** No A record — connection resolves to IPv6 address only
- **Password:** standard PostgreSQL auth (SCRAM-SHA-256)

## Free Tier Anti-Abuse (Aggressive)

Supabase free tier auto-bans IPs on any of these triggers:

| Trigger | Ban Pattern |
|---------|------------|
| Bulk restore (pg_restore or psql -f) over ~100MB | Immediate IP block after completion |
| Multiple rapid `psql -c` calls (2+ in 60 seconds) | Password auth failure → Connection refused |
| Repeated connection attempts after auth failure | Escalating ban duration |

### Recovery from IP Ban

1. Go to Supabase Dashboard → look for the warning banner
2. Click **"Dismiss"** on the IP block notification
3. Wait 30-60 seconds before next connection attempt
4. Make only ONE query per connection cycle

## Connection Pattern That Works

**Wrong (gets banned):**
```bash
psql -h db.xxx.supabase.co -p 5432 -U postgres -d postgres -c "SELECT 1;"
psql -h db.xxx.supabase.co -p 5432 -U postgres -d postgres -c "SELECT count(*);"
# Second call gets banned
```

**Right (survives):**
```bash
# Single connection, single statement
psql -h db.xxx.supabase.co -p 5432 -U postgres -d postgres -c "SELECT 1;"
# Wait 60+ seconds before next connection
```

## pg_dump / Restore Considerations

- `pg_dump` with `--no-owner --no-acl` is mandatory (Supabase doesn't have local users)
- Large dumps (297MB+) WILL trigger a post-restore IP ban — the restore completes, but verification is blocked
- Restore completion can be confirmed by the ALTER TABLE output at the end of psql -f (no errors = success)

## Why Supabase Free Tier is Incompatible with Manifest Production

Manifest makes rapid PostgreSQL connections during normal operation:
- Health checks every 30s
- LLM call logging
- Agent message storage
- Cost/token tracking

These patterns will trigger bans within minutes of pointing Manifest at a Supabase free tier database.

## Recommendation

- **Supabase free tier:** Fine for one-time dumps/restores as a data migration target, but NOT for live Manifest operation
- **For live Manifest:** Use paid Supabase tier (lifts connection limits), self-hosted VPS PostgreSQL (see `references/manifest-load-balancer.md`), or other managed providers with more generous connection limits (Crunchy Bridge, DigitalOcean Managed)
- **If you must use Supabase free tier** for testing: connect via the Session Pooler (port 6543, PgBouncer) which may handle connection pooling more gracefully — but this was not tested in the 2026-06-03 session
