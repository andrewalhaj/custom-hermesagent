#!/usr/bin/env python3
"""Infra Watchdog — runs hourly (was every 15 min until 2026-10-13). SILENT when healthy (watchdog pattern).
Checks wall-dash nginx (VPS-A), gateway, KB daemon socket, disk on both hosts,
cron job health, delegation, memory-store pressure, provider auth, dispatcher unit,
RAG prefetch timeouts, and local-guards liveness (the only liveness check).

DETECT + ALERT ONLY, except one KB-daemon restart (a pure cache; check 2b).
On alert, Andrew decides whether to invoke the `infra-incident-triage` skill
(which diagnoses -> proposes -> waits for approval).

Anti-spam: a state file suppresses duplicate alerts unless the failure set CHANGES
or COOLDOWN_MIN has elapsed, so a multi-hour outage nags hourly, not every 15 min.
Exit 0 ALWAYS when the job ran (alerts are delivered via stdout, which the no_agent
cron sends verbatim). The exit code means "did the job run", not "did it find issues".
"""
import json, os, re, sys, time, subprocess, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_env import require

HERMES = os.path.expanduser("~/.hermes")
STATE = "/tmp/infra_watchdog_state.json"
COOLDOWN_MIN = 60
P0, P1 = [], []   # alertable buckets

def sh(cmd, timeout=12):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 124, "", str(e)

def http_code(url, timeout=8):
    try:
        return urllib.request.urlopen(urllib.request.Request(url), timeout=timeout).status
    except Exception:
        return 0

def log_hits(log_text, now, needle):
    """Count `needle` lines in the last 65 min.
    gateway.log/agent.log stamps are naive LOCAL time, so `now` must be naive local too."""
    cutoff, n = now - timedelta(minutes=65), 0
    for line in log_text.split("\n"):
        if needle in line:
            try:
                n += datetime.fromisoformat(line[:19]) > cutoff
            except ValueError:
                n += 1  # can't parse timestamp, be conservative
    return n

def auth_failures(log_text, now):
    return log_hits(log_text, now, "Primary provider auth failed")

def signature(msgs):
    # digits stripped: "disk 91%" → "disk 92%" is the same failure, so the cooldown holds
    return "|".join(sorted(re.sub(r"\d+", "#", m) for m in msgs))

def cron_health(jobs, now=None):
    """One alert per enabled job whose last run was not ok (error, timeout, delivery_failed...)."""
    now, alerts = now or datetime.now(timezone.utc), []
    for job in jobs:
        name, st = job["name"], job.get("last_status")
        if not job.get("enabled", True) or name.startswith("Infra Watchdog"):
            continue   # skip self — prevents cascading false alarms
        if st not in (None, "ok"):
            why = job.get("last_error") or job.get("last_delivery_error") or ""
            alerts.append(f"Cron '{name}': last run {st}" + (f": {why[:120]}" if why else ""))
        elif st is None and job.get("last_run_at") is None and job.get("next_run_at"):
            dt = datetime.fromisoformat(job["next_run_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < now - timedelta(hours=2):
                alerts.append(f"Cron '{name}': scheduled but never executed")
    return alerts


def main():
    # resolved here, not at import: a missing var must not kill importers (tests, health_audit)
    VPS_A_HOST = require("VPS_A_HOST")
    # wall-dash nginx binds the Tailscale interface, not the public IP — probe the tailnet addr
    WALL_DASH_URL = require("WALL_DASH_URL")
    # ── 1. backup nginx :5051 ─────────────────────────────────────────────
    if http_code(WALL_DASH_URL) != 200:
        P1.append(f"wall-dash nginx :5051 unreachable on the backup host")

    # ── 2. gateway (user-scoped systemd) ───────────────────────────────────
    rc, out, _ = sh("XDG_RUNTIME_DIR=/run/user/0 systemctl --user is-active hermes-gateway.service")
    if out != "active":
        P1.append(f"Hermes gateway not active (state={out or 'unknown'})")

    # ── 2a. dispatcher (system-scoped) — moved from memory-drift-watchdog.sh (step 7.3) ──
    rc, out, _ = sh("systemctl is-active hermes-dispatcher.service")
    if out != "active":
        P1.append(f"hermes-dispatcher not active (state={out or 'unknown'}) — webhooks/kanban dispatch down")

    # ── 2b. KB warm-search daemon — SOCKET probe, not unit state ───────────
    # The unit can be "active" while the socket file is orphaned/deleted (proven
    # 2026-06-18: clients silently fall back to the ~8s cold path). So probe the
    # socket with a real connect(). Pure-acceleration service: a clean auto-restart
    # is SILENT (self-healed); only a FAILED restart escalates to P1.
    def _kb_sock_ok():
        import socket as _s
        try:
            _c = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
            _c.settimeout(3)
            _c.connect(f"{HERMES}/run/kb.sock")
            _c.close()
            return True
        except Exception:
            return False
    if not _kb_sock_ok():
        sh("XDG_RUNTIME_DIR=/run/user/0 systemctl --user restart hermes-kb-daemon.service")
        import time as _t; _t.sleep(10)
        if not _kb_sock_ok():
            rc2, out2, _ = sh("XDG_RUNTIME_DIR=/run/user/0 systemctl --user is-active hermes-kb-daemon.service")
            P1.append(f"KB warm-search daemon socket dead and auto-restart FAILED (unit={out2 or 'unknown'}) — knowledge.py search degraded to ~8s cold-start")

    # ── 3. disk on both hosts (>90% = P1) ──────────────────────────────────
    rc, out, _ = sh("df -P / | awk 'NR==2{gsub(\"%\",\"\",$5); print $5}'")
    if out.isdigit() and int(out) > 90:
        P1.append(f"Primary disk {out}% full")
    rc, out, _ = sh(f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@{VPS_A_HOST} "
                    f"\"df -P / | awk 'NR==2{{gsub(\\\"%\\\",\\\"\\\",\\$5); print \\$5}}'\"", timeout=20)
    if rc != 0 or not out.isdigit():
        P1.append(f"Backup host ({VPS_A_HOST}) unreachable — disk probe failed (rc={rc})")
    elif int(out) > 90:
        P1.append(f"Backup host ({VPS_A_HOST}) disk {out}% full")

    # ── 4. cron job health ─────────────────────────────────────────────
    try:
        with open(f"{HERMES}/cron/jobs.json") as f:
            P1.extend(cron_health(json.load(f).get("jobs", [])))
    except Exception as e:
        P1.append(f"Cron DB unreadable: {e}")

    # ── 6. Delegation health (outcome-based) ──────────────────────────────
    # Delegation authenticates through the OAuth credential pool in auth.json,
    # NOT delegation.api_key in config.yaml (that literal is stale and unused).
    # Probing a key the adapter never uses produced false P1s, so this checks the
    # only real evidence: the final status lines of recent subagent transcripts.
    try:
        import glob as _glob, re as _re
        _logs = sorted(_glob.glob(f"{HERMES}/cache/delegation/live/*/task-*.log"),
                       key=os.path.getmtime, reverse=True)
        _recent = [p for p in _logs if time.time() - os.path.getmtime(p) < 24 * 3600]
        _ok = _bad = 0
        for _p in _recent[:40]:
            try:
                _tail = open(_p, errors="replace").read()[-4000:]
            except OSError:
                continue
            _m = _re.findall(r"final\s*\|\s*status=(\w+)", _tail)
            if not _m:
                continue
            if _m[-1] == "completed":
                _ok += 1
            elif _re.search(r"401|authentication|credit balance|invalid x-api-key", _tail, _re.I):
                _bad += 1
        if _bad and not _ok:
            P1.append(f"Delegation dead: last {_bad} subagent run(s) failed on auth — check auth.json credential pool")
        elif _bad:
            P1.append(f"Delegation flaky: {_bad} auth failure(s) vs {_ok} completed in 24h")
    except Exception as e:
        P1.append(f"Delegation probe failed: {e}")

    # ── 7. Memory store pressure (backstop for proactive-storage doctrine) ──
    # The in-session doctrine stops autonomous ADD at 90% and gates eviction.
    # This is the BACKSTOP: page only when a store creeps to >=98% of its cap —
    # i.e. the doctrine failed to keep up and the store is about to reject writes.
    # Caps live in config.yaml (memory.memory_char_limit / memory.user_char_limit).
    try:
        import yaml as _yaml7
        # Probe BOTH the default profile AND each sister profile under profiles/.
        # Each profile carries its OWN caps in its OWN config.yaml — never assume
        # default's caps apply to a sister (ha-bot ran 2200/1375 vs default 3000/1750).
        # (label, config.yaml path, memories dir)
        _mem_targets = [("default", f"{HERMES}/config.yaml", f"{HERMES}/memories")]
        _prof_root = f"{HERMES}/profiles"
        # Skip rollback/update SNAPSHOTS — they are frozen archives, not live
        # profiles; their stores are stale by design and must not page.
        _snap_re = re.compile(r"^(pre-update|stable|pre-|backup-|snapshot)", re.I)
        if os.path.isdir(_prof_root):
            for _p in sorted(os.listdir(_prof_root)):
                if _snap_re.match(_p):
                    continue
                _pcfg = f"{_prof_root}/{_p}/config.yaml"
                _pmem = f"{_prof_root}/{_p}/memories"
                if os.path.isfile(_pcfg) and os.path.isdir(_pmem):
                    _mem_targets.append((_p, _pcfg, _pmem))
        for _label, _cfgpath, _memdir in _mem_targets:
            try:
                with open(_cfgpath) as _cf:
                    _pc = (_yaml7.safe_load(_cf) or {}).get("memory") or {}
            except Exception:
                _pc = {}
            _mlimit = int(_pc.get("memory_char_limit", 3000))
            _ulimit = int(_pc.get("user_char_limit", 2250))
            for _name, _path, _lim in (("MEMORY.md", f"{_memdir}/MEMORY.md", _mlimit),
                                       ("USER.md",   f"{_memdir}/USER.md",   _ulimit)):
                try:
                    with open(_path, encoding="utf-8") as _mf:
                        _n = len(_mf.read())
                    _pct = round(_n / _lim * 100)
                    if _pct >= 92:
                        P1.append(f"[{_label}] {_name} at {_pct}% ({_n}/{_lim}) — compaction overdue, store near reject")
                except FileNotFoundError:
                    pass
    except Exception as e:
        P1.append(f"Memory pressure probe failed: {e}")

    # ── 8. Anthropic bypass fallback detection ──────────────────────────────
    # "Primary provider auth failed" means the bypass token expired/refused and
    # requests are falling back to the fallback provider. Alert if any in the last 65 min.
    try:
        with open(f"{HERMES}/logs/gateway.log", "rb") as _gf:
            _count = auth_failures(_gf.read().decode("utf-8", errors="ignore"), datetime.now())
        if _count > 0:
            P1.append(f"Anthropic bypass fallback: {_count} auth failure(s) in last 65 min — primary provider auth failing")
    except Exception as e:
        P1.append(f"Auth-failure log probe failed: {e}")

    # ── 8b. RAG prefetch timeouts — 2026-10-15: swarm workers starved the LH02 embedder
    # ~40 min and every turn silently ran without cold-store recall.
    try:
        with open(f"{HERMES}/logs/agent.log", "rb") as _af:
            _pf = log_hits(_af.read().decode("utf-8", errors="ignore"), datetime.now(), "prefetch timed out")
        if _pf >= 3:
            P1.append(f"RAG prefetch timed out {_pf}x in last 65 min — turns ran without recall (check LH02 Ollama /api/ps, swarm workers)")
    except Exception as e:
        P1.append(f"Prefetch-timeout log probe failed: {e}")

    # ── 9. local-guards plugin liveness (gateway armed, not just on disk) ──
    # Proves each native guard (plugins/local-guards/guards/*.py) emitted
    # '[local-guards] <name> armed' in the LIVE GATEWAY PROCESS (journal since gateway start).
    try:
        _GUARDS_DIR = f"{HERMES}/plugins/local-guards/guards"
        try:
            _guard_names = sorted(f[:-3] for f in os.listdir(_GUARDS_DIR) if f.endswith(".py") and not f.startswith("_"))
        except FileNotFoundError:
            P1.append(f"local-guards guards/ missing at {_GUARDS_DIR} — guards not active in gateway")
            _guard_names = []
        if _guard_names:
            _, _gw_pid, _ = sh(
                "XDG_RUNTIME_DIR=/run/user/0 systemctl --user show "
                "-p MainPID --value hermes-gateway.service"
            )
            _gw_pid = _gw_pid.strip()
            _gw_start_epoch = None
            _since_str = None
            if not _gw_pid or _gw_pid == "0":
                P1.append("local-guards liveness: gateway MainPID unavailable — cannot probe liveness")
            else:
                try:
                    with open("/proc/stat") as _pf:
                        _boot_epoch = next(
                            int(_ln.split()[1]) for _ln in _pf if _ln.startswith("btime")
                        )
                    with open(f"/proc/{_gw_pid}/stat") as _sf:
                        _gw_start_epoch = (
                            _boot_epoch
                            + int(_sf.read().split()[21]) / os.sysconf(os.sysconf_names["SC_CLK_TCK"])
                        )
                    _since_str = datetime.fromtimestamp(
                        _gw_start_epoch
                    ).strftime("%Y-%m-%d %H:%M:%S")  # local: journalctl --since parses local
                except Exception as _ste:
                    P1.append(f"local-guards liveness: start time read failed: {_ste}")
            if _since_str:
                _, _jout, _ = sh(
                    f"XDG_RUNTIME_DIR=/run/user/0 journalctl --user "
                    f"-u hermes-gateway.service --since '{_since_str}' --no-pager",
                    timeout=30,
                )
                for _gname in _guard_names:
                    _marker = f"[local-guards] {_gname} armed"
                    if _jout.count(_marker) == 0:
                        P1.append(
                            f"GUARD NOT ARMED IN GATEWAY: '{_marker}' absent "
                            f"in journal since {_since_str}"
                        )
    except Exception as _lge:
        P1.append(f"local-guards liveness probe failed: {_lge}")

    fails = [("P0", m) for m in P0] + [("P1", m) for m in P1]
    sig = signature(m for _, m in fails)

    if not fails:
        try: os.remove(STATE)   # clear state on recovery so next failure alerts immediately
        except FileNotFoundError: pass
        sys.exit(0)             # SILENT — healthy

    now = datetime.now(timezone.utc)
    prev = {}
    try:
        with open(STATE) as f: prev = json.load(f)
    except Exception: pass
    if prev.get("sig") == sig:
        last = datetime.fromisoformat(prev.get("ts"))
        if now - last < timedelta(minutes=COOLDOWN_MIN):
            sys.exit(0)         # same failure, within cooldown — stay silent
    with open(STATE, "w") as f:
        json.dump({"sig": sig, "ts": now.isoformat()}, f)

    sev = "P0" if P0 else "P1"
    print(f"\u26a0 INFRA WATCHDOG [{sev}] {now.strftime('%Y-%m-%d %H:%M')}Z — {len(fails)} issue(s):")
    for s, m in fails:
        print(f"  [{s}] {m}")
    print("\nRead-only detection. To act: ask me to run `infra-incident-triage` (diagnose \u2192 propose \u2192 your approval).")
    # Exit 0 even when issues are found: the alert is DELIVERED via stdout above
    # (no_agent cron sends stdout verbatim). The exit code only sets last_status —
    # exit 1 made every healthy-but-found-issues run record "error" (false alarm in
    # the cron-health view, and the watchdog then flags ITSELF). Detection != failure:
    # the job ran correctly, so exit 0. Real crashes still exit non-zero via traceback.
    sys.exit(0)


if __name__ == "__main__":
    main()
