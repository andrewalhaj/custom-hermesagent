# Infrastructure Codebase Review Checklist

Systematic read-only review pattern. Run this for scheduled audits or when Andrew asks "review our infrastructure code base."

## Pre-flight: Load Skill + References

> *(Real SSH addresses: `memory_search "Tailnet"` or `~/.hermes/references/infrastructure-summary.md`)*

Load `infra-incident-triage` skill and read the durable refs:
- `~/.hermes/references/infrastructure-summary.md`
- `~/.hermes/references/manifest-topology.md`
- `~/.hermes/references/scheduler-recovery-procedure.md`

## Phase 1: Live Health (DETECT)

Run system vitals on BOTH hosts (primary local, backup via SSH):

```bash
# PRIMARY (local)
uptime; ps aux --sort=-%cpu | head -6; free -h; df -h /
systemctl --failed --no-pager
docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
systemctl --user is-active hermes-gateway.service
du -sh ~/.hermes/state.db

# BACKUP (SSH)
ssh -o ConnectTimeout=10 root@<VPS-A-ip> '
  uptime; free -h; df -h /
  docker ps -a --format "{{.Names}}\t{{.Status}}\t{{.Image}}"
  nginx -t; systemctl is-active nginx
  ss -tlnp | grep -E "8080|2099"
'
```

Green = all services active, no failed units, disk <70%, containers healthy.

## Phase 2: Script Inventory (stale detection)

1. List all Python scripts: `search_files(path='~/.hermes/scripts', pattern='*.py', target='files')`
2. For each script, check if it's referenced by any cron job in `~/.hermes/cron/jobs.json`
3. Check if it's the active version or superseded:
   - `infra_watchdog.py` is current — it superseded `heartbeat.py` and `vps_watchdog.py`
   - `heartbeat.py` and `vps_watchdog.py` are dead weight (not in any cron)
4. Verify syntax: `python3 -c "import ast; ast.parse(open('script.py').read())"`
5. For any file that looks broken, hex-verify: `python3 -c "print(open('script.py').readlines()[N].encode().hex())"`
   - Tool rendering can mask `"` characters as `***` — hexdump is authoritative

## Phase 3: Config Review

1. `hermes config show` — check key sections:
   - `model` / `provider`: correct active provider?
   - `providers.manifest-vision`: base_url points to nginx LB?
   - `delegation`: provider/model match current working config?
2. Cross-check against live topology:
   - base_url → nginx LB → primary Manifest → Railway DB
   - Delegation: v0.15.1 cannot use `custom:manifest-vision` (transport bug)
3. Check for no-op sections: `fallback_providers: []`, empty `credential_pool_strategies: {}`, `checkpoints.enabled: false`

## Phase 4: Docker Compose

1. `cat ~/manifest/docker-compose.yml` — verify:
   - `restart: unless-stopped` on ALL services
   - `read_only: true` + `no-new-privileges:true` + `cap_drop: ALL` (Manifest container)
   - Log rotation: `max-size: 10m, max-file: 5`
   - Networks: `internal` (postgres-manifest), `frontend` (exposed)
2. `cat ~/manifest/.env` — verify DATABASE_URL:
   - Points to Railway (`acela.proxy.rlwy.net:13314`), NOT localhost
   - Test with live PSQL: `PGPASSWORD='pw' psql "$URL" -c "SELECT 1"`
   - Note: tool output may mask the password as `***` — raw-byte read for actual content
3. `docker inspect mnfst-manifest-1 --format '{{.State.StartedAt}}'` — compare with `.env` mtime to confirm container uses current config

## Phase 5: Nginx Config (backup host)

```bash
ssh root@<VPS-A-ip> 'cat /etc/nginx/sites-enabled/manifest-lb'
```

Verify:
- Both servers are active (NO `backup` directive) — round-robin load balancing since 2026-06-03
- `proxy_next_upstream error timeout http_502 http_503 http_504` — proper failover triggers
- `proxy_next_upstream_tries 2` — fails over once, not infinite
- `max_fails=2 fail_timeout=30s` on both servers — consistent health checks

## Phase 6: Cron Jobs

1. `cat ~/.hermes/cron/jobs.json | python3 -c "..."` — dump all jobs
2. For each job, check: schedule (no overlaps), enabled, last_status (all `ok`?), script references (point to existing files?)
3. Cross-check against reference docs — `scheduler-recovery-procedure.md` may be stale

Current expected set (2026-06-03):
- Infra Watchdog (15-min, `infra_watchdog.py`, no_agent)
- Daily Hermes Backup (03:00, `backup.sh`, no_agent)
- Honcho→Obsidian Bridge (08:00, `honcho-bridge.sh`, no_agent)
- Daily Delegation Audit (09:00, agent, deepseek)
- Daily Knowledge Capture (02:30, `session_digest.py`, agent)
- Weekly KB Dedup Scan (Sun 04:00, `dedup_scan.py`, no_agent)

## Phase 7: Reference Doc Staleness

For each file in `~/.hermes/references/`:
- Check job counts, IDs, names against live `cron/jobs.json`
- Check topology claims against live manifests (`docker ps`, `ss`)
- Check procedures (restart commands, SSH targets) against current config
- Flag outdated content but do NOT edit without approval

## Phase 8: Present

Compile findings into structured report:
1. Health summary (both hosts)
2. Stale/dead scripts
3. Config issues (if any)
4. Doc staleness (if any)
5. Optimization opportunities
6. No remediation without approval

**Remember:** This is a READ-ONLY review. All findings are presented. No changes without explicit greenlight.
