"""Cron job health, shared by infra_watchdog (hourly alert) and health_audit (probe_cron).

Pure functions over the scheduler's jobs.json: no I/O besides load_jobs.
"""
import json
import os
from datetime import datetime, timezone, timedelta

JOBS_PATH = os.path.expanduser("~/.hermes/cron/jobs.json")


def load_jobs(path=JOBS_PATH):
    with open(path) as f:
        return json.load(f).get("jobs", [])


def alerts(jobs, now=None):
    """One alert per enabled job whose last run was not ok (error, timeout, delivery_failed...)
    or that was scheduled over 2h ago and never ran."""
    now, out = now or datetime.now(timezone.utc), []
    for job in jobs:
        name, st = job["name"], job.get("last_status")
        if not job.get("enabled", True) or name.startswith("Infra Watchdog"):
            continue   # skip self — prevents cascading false alarms
        if st not in (None, "ok"):
            why = job.get("last_error") or job.get("last_delivery_error") or ""
            out.append(f"Cron '{name}': last run {st}" + (f": {why[:120]}" if why else ""))
        elif st is None and job.get("last_run_at") is None and job.get("next_run_at"):
            dt = datetime.fromisoformat(job["next_run_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < now - timedelta(hours=2):
                out.append(f"Cron '{name}': scheduled but never executed")
    return out
