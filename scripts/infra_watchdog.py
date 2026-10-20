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
import glob, json, os, re, socket, sys, time, subprocess, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cron_health
from hermes_env import HERMES_HOME, env

HERMES = os.path.expanduser("~/.hermes")
STATE = "/tmp/infra_watchdog_state.json"
COOLDOWN_MIN = 60

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

def _missing(key):
    return f"{key} is not set: export it or add {key}=... to {HERMES_HOME / '.env'}"


# Each check returns its P1 findings (message strings); main() concatenates them in order.

def check_wall_dash(url):
    # wall-dash nginx binds the Tailscale interface, not the public IP — probe the tailnet addr
    if not url:
        return [_missing("WALL_DASH_URL")]
    if http_code(url) != 200:
        return ["wall-dash nginx :5051 unreachable on the backup host"]
    return []

def check_gateway():
    rc, out, _ = sh("XDG_RUNTIME_DIR=/run/user/0 systemctl --user is-active hermes-gateway.service")
    return [f"Hermes gateway not active (state={out or 'unknown'})"] if out != "active" else []

def check_dispatcher():
    # moved from memory-drift-watchdog.sh (step 7.3)
    rc, out, _ = sh("systemctl is-active hermes-dispatcher.service")
    if out != "active":
        return [f"hermes-dispatcher not active (state={out or 'unknown'}) — webhooks/kanban dispatch down"]
    return []

def _kb_sock_ok():
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(3)
        c.connect(f"{HERMES}/run/kb.sock")
        c.close()
        return True
    except Exception:
        return False

def check_kb_daemon():
    # SOCKET probe, not unit state: the unit can be "active" while the socket file is
    # orphaned/deleted (proven 2026-06-18: clients silently fall back to the ~8s cold path).
    # Pure-acceleration service: a clean auto-restart is SILENT (self-healed); only a
    # FAILED restart escalates to P1.
    if _kb_sock_ok():
        return []
    sh("XDG_RUNTIME_DIR=/run/user/0 systemctl --user restart hermes-kb-daemon.service")
    time.sleep(10)
    if _kb_sock_ok():
        return []
    rc, out, _ = sh("XDG_RUNTIME_DIR=/run/user/0 systemctl --user is-active hermes-kb-daemon.service")
    return [f"KB warm-search daemon socket dead and auto-restart FAILED (unit={out or 'unknown'}) — knowledge.py search degraded to ~8s cold-start"]

def check_disks(vps_a_host):
    """>90% on either host = P1."""
    found = []
    rc, out, _ = sh("df -P / | awk 'NR==2{gsub(\"%\",\"\",$5); print $5}'")
    if out.isdigit() and int(out) > 90:
        found.append(f"Primary disk {out}% full")
    if not vps_a_host:
        return found + [_missing("VPS_A_HOST")]
    rc, out, _ = sh(f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@{vps_a_host} "
                    f"\"df -P / | awk 'NR==2{{gsub(\\\"%\\\",\\\"\\\",\\$5); print \\$5}}'\"", timeout=20)
    if rc != 0 or not out.isdigit():
        found.append(f"Backup host ({vps_a_host}) unreachable — disk probe failed (rc={rc})")
    elif int(out) > 90:
        found.append(f"Backup host ({vps_a_host}) disk {out}% full")
    return found

def check_cron():
    try:
        return cron_health.alerts(cron_health.load_jobs(f"{HERMES}/cron/jobs.json"))
    except Exception as e:
        return [f"Cron DB unreadable: {e}"]

def check_delegation():
    # Delegation authenticates through the OAuth credential pool in auth.json,
    # NOT delegation.api_key in config.yaml (that literal is stale and unused).
    # Probing a key the adapter never uses produced false P1s, so this checks the
    # only real evidence: the final status lines of recent subagent transcripts.
    try:
        logs = sorted(glob.glob(f"{HERMES}/cache/delegation/live/*/task-*.log"),
                      key=os.path.getmtime, reverse=True)
        recent = [p for p in logs if time.time() - os.path.getmtime(p) < 24 * 3600]
        ok = bad = 0
        for p in recent[:40]:
            try:
                tail = open(p, errors="replace").read()[-4000:]
            except OSError:
                continue
            m = re.findall(r"final\s*\|\s*status=(\w+)", tail)
            if not m:
                continue
            if m[-1] == "completed":
                ok += 1
            elif re.search(r"401|authentication|credit balance|invalid x-api-key", tail, re.I):
                bad += 1
        if bad and not ok:
            return [f"Delegation dead: last {bad} subagent run(s) failed on auth — check auth.json credential pool"]
        if bad:
            return [f"Delegation flaky: {bad} auth failure(s) vs {ok} completed in 24h"]
        return []
    except Exception as e:
        return [f"Delegation probe failed: {e}"]

def check_memory_pressure():
    # Backstop for the proactive-storage doctrine: the in-session doctrine stops autonomous ADD
    # at 90% and gates eviction; page only when a store creeps to >=92% of its cap, i.e. the
    # doctrine failed to keep up. Caps live in each profile's config.yaml
    # (memory.memory_char_limit / memory.user_char_limit).
    try:
        import yaml
        # Probe BOTH the default profile AND each sister profile under profiles/.
        # Each profile carries its OWN caps in its OWN config.yaml — never assume
        # default's caps apply to a sister (ha-bot ran 2200/1375 vs default 3000/1750).
        # (label, config.yaml path, memories dir)
        targets = [("default", f"{HERMES}/config.yaml", f"{HERMES}/memories")]
        prof_root = f"{HERMES}/profiles"
        # Skip rollback/update SNAPSHOTS — they are frozen archives, not live
        # profiles; their stores are stale by design and must not page.
        snap_re = re.compile(r"^(pre-update|stable|pre-|backup-|snapshot)", re.I)
        if os.path.isdir(prof_root):
            for p in sorted(os.listdir(prof_root)):
                if snap_re.match(p):
                    continue
                cfg, mem = f"{prof_root}/{p}/config.yaml", f"{prof_root}/{p}/memories"
                if os.path.isfile(cfg) and os.path.isdir(mem):
                    targets.append((p, cfg, mem))
        found = []
        for label, cfgpath, memdir in targets:
            try:
                with open(cfgpath) as cf:
                    pc = (yaml.safe_load(cf) or {}).get("memory") or {}
            except Exception:
                pc = {}
            mlimit = int(pc.get("memory_char_limit", 3000))
            ulimit = int(pc.get("user_char_limit", 2250))
            for name, path, lim in (("MEMORY.md", f"{memdir}/MEMORY.md", mlimit),
                                    ("USER.md",   f"{memdir}/USER.md",   ulimit)):
                try:
                    with open(path, encoding="utf-8") as mf:
                        n = len(mf.read())
                    pct = round(n / lim * 100)
                    if pct >= 92:
                        found.append(f"[{label}] {name} at {pct}% ({n}/{lim}) — compaction overdue, store near reject")
                except FileNotFoundError:
                    pass
        return found
    except Exception as e:
        return [f"Memory pressure probe failed: {e}"]

def check_auth_fallback():
    # "Primary provider auth failed" means the bypass token expired/refused and
    # requests are falling back to the fallback provider. Alert if any in the last 65 min.
    try:
        with open(f"{HERMES}/logs/gateway.log", "rb") as f:
            count = auth_failures(f.read().decode("utf-8", errors="ignore"), datetime.now())
        if count > 0:
            return [f"Anthropic bypass fallback: {count} auth failure(s) in last 65 min — primary provider auth failing"]
        return []
    except Exception as e:
        return [f"Auth-failure log probe failed: {e}"]

def check_rag_prefetch():
    # 2026-10-15: swarm workers starved the LH02 embedder ~40 min and every turn silently
    # ran without cold-store recall.
    try:
        with open(f"{HERMES}/logs/agent.log", "rb") as f:
            n = log_hits(f.read().decode("utf-8", errors="ignore"), datetime.now(), "prefetch timed out")
        if n >= 3:
            return [f"RAG prefetch timed out {n}x in last 65 min — turns ran without recall (check LH02 Ollama /api/ps, swarm workers)"]
        return []
    except Exception as e:
        return [f"Prefetch-timeout log probe failed: {e}"]

def check_local_guards():
    # Proves each native guard (plugins/local-guards/guards/*.py) emitted
    # '[local-guards] <name> armed' in the LIVE GATEWAY PROCESS (journal since gateway start).
    try:
        guards_dir = f"{HERMES}/plugins/local-guards/guards"
        try:
            names = sorted(f[:-3] for f in os.listdir(guards_dir) if f.endswith(".py") and not f.startswith("_"))
        except FileNotFoundError:
            return [f"local-guards guards/ missing at {guards_dir} — guards not active in gateway"]
        if not names:
            return []
        found = []
        _, pid, _ = sh(
            "XDG_RUNTIME_DIR=/run/user/0 systemctl --user show "
            "-p MainPID --value hermes-gateway.service"
        )
        pid = pid.strip()
        since = None
        if not pid or pid == "0":
            found.append("local-guards liveness: gateway MainPID unavailable — cannot probe liveness")
        else:
            try:
                with open("/proc/stat") as pf:
                    boot_epoch = next(int(ln.split()[1]) for ln in pf if ln.startswith("btime"))
                with open(f"/proc/{pid}/stat") as sf:
                    start_epoch = boot_epoch + int(sf.read().split()[21]) / os.sysconf(os.sysconf_names["SC_CLK_TCK"])
                since = datetime.fromtimestamp(start_epoch).strftime("%Y-%m-%d %H:%M:%S")  # local: journalctl --since parses local
            except Exception as e:
                found.append(f"local-guards liveness: start time read failed: {e}")
        if since:
            _, journal, _ = sh(
                f"XDG_RUNTIME_DIR=/run/user/0 journalctl --user "
                f"-u hermes-gateway.service --since '{since}' --no-pager",
                timeout=30,
            )
            for name in names:
                marker = f"[local-guards] {name} armed"
                if journal.count(marker) == 0:
                    found.append(f"GUARD NOT ARMED IN GATEWAY: '{marker}' absent in journal since {since}")
        return found
    except Exception as e:
        return [f"local-guards liveness probe failed: {e}"]


def main():
    findings = (check_wall_dash(env("WALL_DASH_URL"))
                + check_gateway()
                + check_dispatcher()
                + check_kb_daemon()
                + check_disks(env("VPS_A_HOST"))
                + check_cron()
                + check_delegation()
                + check_memory_pressure()
                + check_auth_fallback()
                + check_rag_prefetch()
                + check_local_guards())
    fails = [("P1", m) for m in findings]
    sig = signature(findings)

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

    print(f"\u26a0 INFRA WATCHDOG [P1] {now.strftime('%Y-%m-%d %H:%M')}Z — {len(fails)} issue(s):")
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
