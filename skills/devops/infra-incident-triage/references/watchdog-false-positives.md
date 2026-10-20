# Watchdog False-Positive Triad (verified 2026-06-08)

> *(Real service IPs/addresses used in probes: `memory_search "Tailnet"`.)*

A single watchdog alert can be a stale CHECK, not a real outage. Before deep-diving any
P1, reproduce the check by hand and confirm the target is actually down. Three distinct
false-positive classes hit `infra_watchdog.py` / `dedup_scan.py` this session — all
were healthy systems with broken checks.

## 1. Probe hits the wrong interface (public IP vs Tailscale bind)

**Symptom:** `Backup nginx :5051 unreachable` (HTTP 000), but the service is up.

**Root cause:** the check curled the host's **public** IP (`http://<VPS-A-ip>:5051/`),
but nginx binds the **Tailscale** interface only (`<VPS-A-tailnet-ip>:5051`). Tailnet-only by
design → public probe always fails.

**Diagnose:**
```bash
# prove the service is up on the tailnet addr from THIS host first
curl -s -o /dev/null -w 'tailnet %{http_code} (%{time_total}s)\n' --max-time 8 http://<VPS-A-tailnet-ip>:5051/
# then SSH the host and confirm the bind
ssh root@<host> 'ss -tlnp | grep 5051; systemctl is-active nginx'
```
The `ss` output shows the literal bound IP — believe it over any stored "public IP" fact.

**Fix (least-astonishment):** do NOT repoint a shared host constant (`BACKUP_HOST` is also
used by the SSH disk check, which correctly uses the public IP). Add a dedicated,
clearly-named constant for the dashboard URL and use it only on that one probe:
```python
WALL_DASH_URL = "http://<VPS-A-tailnet-ip>:5051/"   # nginx binds tailnet iface, not public IP
...
if http_code(WALL_DASH_URL) != 200:
```

## 2. Cron runs the script under bare system python3 (missing venv deps)

**Symptom:** `Script exited with code 1`, stderr `ModuleNotFoundError: No module named 'numpy'`.
Runs clean by hand (because you sourced a venv), fails in cron.

**Root cause:** `no_agent` cron script jobs launch with **system `python3`**, which lacks
numpy/sentence_transformers/etc. The script needs the Hermes venv.

**Reproduce cron exactly:** `/usr/bin/python3 ~/.hermes/scripts/<script>.py` — if it dies on
import, that's the cron path.

**Find the venv with the libs** (NOTE: there is no `~/.venv`; the Hermes venv is at
`/usr/local/lib/hermes-agent/venv`):
```bash
for py in /usr/local/lib/hermes-agent/venv/bin/python $(which python3); do
  "$py" -c "import numpy, sentence_transformers" 2>/dev/null && echo "OK -> $py"
done
```

**Fix — venv self-guard (self-contained, no cron-schema change).** Insert BEFORE the heavy
imports so the script re-execs itself under the venv python when a dep is missing:
```python
import os, sys
try:
    import numpy  # noqa: F401
except ModuleNotFoundError:
    _VENV_PY = "/usr/local/lib/hermes-agent/venv/bin/python"
    if os.path.exists(_VENV_PY) and os.path.realpath(sys.executable) != os.path.realpath(_VENV_PY):
        os.execv(_VENV_PY, [_VENV_PY] + sys.argv)
    raise
import numpy as np
from sentence_transformers import SentenceTransformer
```

## 3. Report-script exit code misread as failure (exit-1-on-findings)

**Symptom:** even after fixing #2, the watchdog keeps flagging
`Cron '<job>': last run FAILED`. The job's own `last_status` is `error`.

**Root cause:** the script `sys.exit(1)` when it *finds* something (e.g. dedup found N
duplicate pairs). For a **report-only scan**, finding items is a normal result, not a
failure — but the cron runner records exit!=0 as `error`, and the watchdog reads
`jobs.json` `last_status` and pages on it.

**Fix:** a report/scan script should **exit 0 always** (the report is delivered via stdout
regardless). Reserve non-zero for genuine crashes. Then trigger one run to refresh the
stale `last_status` → `ok`:
```python
if pairs:
    print(format_report(pairs))
    sys.exit(0)   # report-only — finding items is a normal result, not a failure
```
Verify the status flipped by reading jobs.json directly (the cron list view can lag):
```bash
python3 -c "import os; import json; d=json.load(open(os.path.expanduser('~/.hermes/cron/jobs.json'))); \
print([j['last_status'] for j in (d.get('jobs',d) if isinstance(d,dict) else d) if j['name']=='<JobName>'])"
```

## 4. Liveness check greps the process command line

**Symptom:** "`hermes_cli.main gateway run` not found" / "gateway down" alert while
`systemctl --user is-active hermes-gateway.service` says `active` and the chat works.

**Root cause:** the check runs `pgrep -f '<module path>'`. The command line is a detail
of how the process was started, not proof that it is alive. A launcher change (an
interpreter shim, `python -I -c <bootstrap>`, a wrapper script) rewrites argv while the
service stays healthy, and the grep goes blind.

**Diagnose:** `systemctl --user show hermes-gateway -p ActiveState,MainPID`, then
`tr '\0' ' ' < /proc/<MainPID>/cmdline` to see what argv actually looks like now. If the
unit is active and the PID exists, the check is stale.

**Fix (gated, propose first):** check liveness through the supervisor
(`systemctl --user is-active <unit>`, or the unit's MainPID), never by grepping argv.
If a process check is truly needed, use the PID that systemd reports.

Also check where the alert was delivered: `deliver: origin` sends it to whichever chat
created the job. Re-route recurring watchdogs to the cron channel when you propose the fix.

## General rule
A watchdog is a snapshot of assumptions that rots after infra changes (port moves, iface
re-binds, dep changes, exit-code semantics). After ANY service decommission, port change,
or migration, audit every check against live state (`ss -tlnp`, `docker ps`, hand-run the
script under the cron interpreter) BEFORE trusting an alert. Cross-check, then fix the
CHECK — the system is usually fine.

## execute_code is sandbox-blocked from subprocess in this env
`execute_code` refuses scripts that shell out (`BLOCKED: ... subprocess`). For reading
jobs.json / probing state mid-triage, use the `terminal` tool with a one-liner instead.
