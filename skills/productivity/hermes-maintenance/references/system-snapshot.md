# System Snapshot Checklist

Trigger: user says "snapshot," "status check," "health check," "system breakdown," or "what's running?"

## Parallel Data Points

Fire these simultaneously (all idempotent reads):

```
1. terminal  → hermes status 2>&1
2. terminal  → curl -s http://localhost:2099/health 2>&1; curl -s http://<VPS_IP>:8080/health 2>&1
3. terminal  → docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1
4. terminal  → df -h / && du -sh ~/.hermes/*/ | sort -rh && du -sh ~/.hermes/
5. cronjob   → action=list
6. skills_list → (no params)
7. terminal  → python3 ~/.hermes/scripts/knowledge.py status
8. terminal  → free -h && uptime
```

## Follow-up (if snapshot is the primary task)

```
9.  terminal  → grep 'version:' ~/.hermes/config.yaml && ls ~/.hermes/profiles/
10. terminal  → curl -s http://<VPS_IP>:2099/api/v1/health  (VPS Manifest direct)
11. terminal  → ssh <VPS> 'docker ps && df -h / | tail -1'  (VPS health)
12. terminal  → ls ~/.hermes/backups/
13. send_message → action=list
```

For multi-host setups, always check BOTH hosts independently — VPS nginx LB masks individual backend failures.

## Report Structure

Present as a systems-architecture overview, not a flat feature list. Frame components as interconnected subsystems — memory as a pipeline, tools as a composability chain, automation as autonomous maintenance. The goal is to show how pieces compose, not enumerate what exists.

### Tiered narrative structure

1. **Reasoning Core** — Manifest routing, model dispatch logic, subagent parallelism
2. **Memory Pipeline** — hot → warm → cold gradient, how facts promote across tiers
3. **Autonomous Maintenance** — cron jobs as headless agents, what runs when, delivery targets
4. **Tool Composability** — direct → scripted → delegated chain, how tools compose
5. **Current Metrics** — disk, sessions, facts, cron count, profiles (compact final summary)

### Style rules

- Start with architecture, end with metrics. Never lead with a table.
- Describe *how* systems compose, not just *what* exists.
- Use pipeline/gradient/chain/fabric metaphors for interconnected systems.
- Metrics block at the end — bullet list, 5–7 items, one metric per line.
- No emoji lists. No "here's what I can do" framing.
- For multi-host setups: show each host's role, running containers, disk, and health status. Show the routing path explicitly (Request → LB → Backend A/B → DB).

## Pitfalls

- `knowledge.py recent` needs `pylance` and `pandas` in the venv. If it fails with ModuleNotFoundError, install them with `python3 -m pip install pylance pandas`.
- `knowledge.py` may emit a DeprecationWarning about `table_names()` — this is cosmetic, the tool still works.
- Manifest `/v1/models` returns auth error without Bearer token — this confirms it's alive, not broken.
- Manifest `/health` endpoint returns HTML on some versions — check the response body, not just the HTTP code. A 200 with HTML content means the web UI is alive, not necessarily the API. Use `/api/v1/health` for a JSON health check.
- `session_search` does not index the active session — see main SKILL.md pitfalls section.
