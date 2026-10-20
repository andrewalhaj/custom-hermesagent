# Neon PostgreSQL: Migration Lessons

**Date:** 2026-06-02  
**Outcome:** Neon free tier connection limit (20) forced migration to self-hosted postgres on VPS.

## Pitfalls

**Connection limits**
- Neon free tier: 20 max connections. Two Manifest instances with default connection pools blow past this immediately.
- Manifest holds persistent connections (not request-scoped). Two instances × pool-size-5 = 10 connections minimum.
- Scale-to-zero never triggers because health checks ping the DB every 30s.
- Launch plan ($19/month minimum) removes the limit but is overkill for a 66KB config database.

### pg_dump auth issues
- `pg_dump` via URL string with embedded password: intermittent auth failures. Neon rejects password-in-URL auth unpredictably.
- `export PGPASSWORD=<key>`: sometimes works, sometimes doesn't. Not reliable for scripting.
- **Pooler vs non-pooler endpoints:** Different hostnames (`ep-...neon.tech` vs `ep-...pooler.neon.tech`). The pooler endpoint adds `channel_binding=require`. Neither consistently resolves the auth issue.

### Workaround: dump through Docker
```bash
# When Neon connections are exhausted by Manifest pools, stop one Manifest first
ssh vps 'cd ~/manifest && docker compose stop manifest'
# Then dump from Docker postgres (which can reach external networks)
docker exec mnfst-postgres-1 pg_dump [neon-url] > dump.sql
```

## Migration pattern

When moving off Neon:

1. **Don't fight the dump** — if Neon auth is blocking, use the most recent verified dump. Core config tables (tenants, agents, api_keys) don't change during operation.
2. **Exclude session tables:** `--exclude-table-data='message_recordings' --exclude-table-data='reasoning_content_cache' --exclude-table-data='agent_messages'`. These are 99% of the dump size and regrow immediately.
3. **Firewall-gate postgres:** When exposing port 5432 for cross-host access, use iptables to restrict to the specific source IP. Never leave postgres on 0.0.0.0:5432 without firewall rules.
4. **Internal DNS for colocated:** When Manifest and postgres share a Docker host, use `postgresql://user:pass@postgres:5432/db` (Docker internal DNS). No port exposure needed.
5. **Cross-host with explicit IP:** When Manifest on host A needs postgres on host B, use `postgresql://user:pass@<host-b-ip>:5432/db?sslmode=disable`. Ensure firewall allows only host A's IP.

## When to use Neon
- Single-instance Manifest (stays under 20 connections)
- Need point-in-time recovery without managing backups
- Short-lived or dev environments where scale-to-zero saves money
