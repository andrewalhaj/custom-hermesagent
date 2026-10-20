# Railway PostgreSQL — Connection Attempt (2026-06-03)

Tried Railway Hobby ($5/mo) as a Supabase free tier alternative for Manifest PostgreSQL.

## Setup

- **Plan:** Hobby ($5/mo, $5 credits included)
- **Connection:** `acela.proxy.rlwy.net:13314` (public proxy) → internal `:5432`
- **Internal URL:** `postgres.railway.internal:5432` (Railway network only)
- **Database name:** `railway` (not `postgres`)
- **User:** `postgres`
- **Password:** Auto-generated 40-char alphanumeric string

## Connection Failure

Despite public networking being enabled (confirmed in Railway dashboard → Networking), all connection attempts from the Hermes host failed with `FATAL: password authentication failed for user "postgres"`.

### Attempted connection formats (all failed):
```bash
# URL format
psql "postgresql://postgres:password@acela.proxy.rlwy.net:13314/railway"
psql "postgresql://postgres:password@acela.proxy.rlwy.net:13314/railway?sslmode=require"
psql "postgresql://postgres:password@acela.proxy.rlwy.net:13314/railway?sslmode=disable"

# Parameter format
psql -h acela.proxy.rlwy.net -p 13314 -U postgres -d railway

# Alternative user
psql "postgresql://railway:password@acela.proxy.rlwy.net:13314/railway"
```

### Root cause: IP allowlisting (confirmed 2026-06-03)

Local Hermes host connects to Railway successfully. VPS (<vps-ip>) fails with the same `password authentication failed` error using identical credentials. This confirms IP allowlisting — Railway's public proxy rejects connections from unrecognized source IPs even with public networking enabled.
(real address: memory_search "Tailnet")

Other hypotheses eliminated:
1. ~~Password rotation~~ — same password works from local host
2. ~~Proxy propagation delay~~ — connection has been active for hours
3. ~~TLS/SSL mismatch~~ — local host connects with same psql/libpq

### Remaining hypotheses
4. **VPS IPv6 routing** — the VPS may be connecting via IPv6 to Railway's IPv4-only proxy, causing a different auth path

## Recommendation

- Add VPS IP (<vps-ip>) to Railway's IP allowlist if available, or
- Route VPS Manifest through a local SSH tunnel to the Railway DB, or
- Use a different DB provider for the VPS instance (Neon, Fly.io, Crunchy Bridge)
