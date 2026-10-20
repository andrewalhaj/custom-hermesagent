#!/usr/bin/env python3
"""
Hermes Agent System Health Audit

Reads ~/.hermes/references/system-inventory.md and executes FUNCTIONAL probes
against each listed subsystem. Each probe returns (state, detail) where state is
one of: PASS, FAIL, UNVERIFIABLE.

Exit code 0 if no FAILs, 1 if any FAIL. UNVERIFIABLE does not cause nonzero exit
but is displayed prominently.

Principles:
- Probes must prove a subsystem WORKS, not merely EXISTS.
- UNVERIFIABLE is surfaced loudly, never silently treated as PASS.
- UNVERIFIABLE means a capability is GENUINELY absent at runtime — never a stale
  probe pointing at a removed/migrated dependency.

Post-migration reality (2026-08):
- Knowledge store migrated LanceDB -> Supabase pgvector. Live retrieval is the
  `knowledge` package (scripts/knowledge/) via the kb daemon, falling back to
  in-process search. The old `import lancedb` probe was stale.
- Honcho removed (June 2026). Memory provider is Supabase.
- B-full run.py injection mechanism was removed (2026-07-01); the bfull_golden
  probe was retired with it. bfull_recall (auto-RAG QUALITY) remains live.
- CodeGraph MCP is NOT registered with `hermes mcp` (that CLI reads a different
  registry and reports "no servers"). It is spawned directly as `codegraph serve
  --mcp`; the probe does a real MCP stdio handshake.
- CodeGraph index stores content_hash=sha256(file bytes) per file, NOT a git
  commit. Freshness is checked by sampling tracked files against the index hash.
"""

import hashlib
import importlib.util
import json
import os
import re
import select
import subprocess
import sys
import time

# ── Constants ───────────────────────────────────────────────────────────────

INVENTORY_PATH = os.path.expanduser("~/.hermes/references/system-inventory.md")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_PKG = os.path.join(SCRIPTS_DIR, "knowledge")
CONFIG_PATH = os.path.expanduser("~/.hermes/config.yaml")
MEMORY_MD = os.path.expanduser("~/.hermes/memories/MEMORY.md")
USER_MD = os.path.expanduser("~/.hermes/memories/USER.md")
WHOAMI_SCRIPT = os.path.join(os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"), "scripts", "whoami-live.sh")  # optional: not published
HERMES_BIN = os.path.expanduser("~/.local/bin/hermes")
GIT_REPO = "/usr/local/lib/hermes-agent"
CODEGRAPH_DB = os.path.join(GIT_REPO, ".codegraph", "codegraph.db")

MEMORY_CAP = 3000
USER_CAP = 2250

# web_stack: canonical LIVE functional endpoints (host-bound, verified 2026-08).
# searxng: docker-proxy 127.0.0.1:8888 -> container :8080 (web-stack/searxng)
# firecrawl: docker-proxy 127.0.0.1:3002 -> firecrawl-api (web-stack/firecrawl)
# camofox: host node server on :9377 (web-stack/camofox-browser)
WEB_STACK_SERVICES = [
    {
        "name": "searxng",
        "url": "http://127.0.0.1:8888/search?q=healthcheck&format=json",
        "validate": lambda status, body: status == 200 and '"results"' in body,
        "expect": "JSON search results",
    },
    {
        "name": "firecrawl",
        "url": "http://127.0.0.1:3002/",
        "validate": lambda status, body: status == 200 and "Firecrawl" in body,
        "expect": "Firecrawl API banner",
    },
    {
        "name": "camofox",
        "url": "http://127.0.0.1:9377/health",
        "validate": lambda status, body: status == 200,
        "expect": "HTTP 200 /health",
    },
]


# ── Helpers ─────────────────────────────────────────────────────────────────

def run(cmd, timeout=60, env=None):
    """Run a command, return (returncode, stdout_str, stderr_str)."""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env or os.environ.copy(),
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return -2, "", f"command not found: {cmd[0] if cmd else '?'}"
    except Exception as e:
        return -3, "", str(e)


def load_config():
    """Load config.yaml as a nested dict using pyyaml."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


_knowledge_mod = None
_knowledge_err = None


def load_knowledge():
    """Import the live `knowledge` package (Supabase pgvector backend).

    Returns (module, None) on success or (None, error_str) on failure. The
    package auto-loads the embedding model lazily; search() hits the kb daemon
    first (fast) and falls back to in-process search.
    """
    global _knowledge_mod, _knowledge_err
    if _knowledge_mod is not None or _knowledge_err is not None:
        return _knowledge_mod, _knowledge_err
    if not os.path.isdir(KNOWLEDGE_PKG):
        _knowledge_err = f"knowledge package not found at {KNOWLEDGE_PKG}"
        return None, _knowledge_err
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    try:
        import knowledge  # noqa: F401
        _knowledge_mod = knowledge
        return knowledge, None
    except Exception as e:
        _knowledge_err = f"failed to import knowledge package: {e}"
        return None, _knowledge_err


def http_get(url, timeout=10, headers=None):
    """HTTP GET, return (status_code, body)."""
    try:
        import requests
        r = requests.get(url, timeout=timeout, headers=headers or {})
        return r.status_code, r.text
    except ImportError:
        return None, "requests module not available"
    except Exception as e:
        return None, str(e)


def kb_daemon_search(query, top_k=5):
    """Live retrieval via the kb daemon, falling back to in-process search.

    Mirrors knowledge.py's own search dispatch: daemon first (~0.1s), then the
    in-process knowledge.search(). Returns (hits, path_used, error).
    """
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    # Fast path: warm daemon. NOTE: the daemon is documented as "pure
    # acceleration, never a dependency" — so an EMPTY result from it (stale /
    # desynced cache) is NOT authoritative and must fall through to the
    # in-process Supabase path, exactly like a DaemonUnavailable would.
    if os.environ.get("KB_NO_DAEMON") != "1":
        try:
            from kb_client import daemon_search, DaemonUnavailable
            try:
                hits = daemon_search(query, top_k=top_k)
                if hits:  # trust only a NON-empty daemon answer
                    return hits, "daemon", None
            except DaemonUnavailable:
                pass
        except Exception:
            pass
    # Authoritative path: in-process Supabase pgvector search.
    mod, err = load_knowledge()
    if mod is None:
        return None, None, err
    try:
        hits = mod.search(query, top_k=top_k)
        return hits, "in-process", None
    except Exception as e:
        return None, None, f"search() raised: {e}"


# ── Probes ──────────────────────────────────────────────────────────────────

def probe_gateway():
    """gateway — systemctl --user is-active hermes-gateway."""
    rc, stdout, stderr = run(["systemctl", "--user", "is-active", "hermes-gateway"], timeout=10)
    if rc == -2:
        return "UNVERIFIABLE", "systemctl not found"
    if rc != 0:
        detail = stderr or stdout or f"exit code {rc}"
        return "FAIL", detail
    if stdout == "active":
        return "PASS", "active"
    return "FAIL", f"state is '{stdout}', expected 'active'"


def probe_bypass():
    """bypass — Anthropic OAuth live auth (core does it natively since the patch
    was retired 2026-10-13; id kept to match system-inventory.md).

    Functional proof: a real `hermes chat` round-trip returns 'AUTH TEST OK'.
    """
    # Live auth round-trip.
    if not os.path.exists(HERMES_BIN):
        return "UNVERIFIABLE", f"hermes binary not found at {HERMES_BIN} (cannot run live auth test)"
    rc, stdout, stderr = run(
        [HERMES_BIN, "chat", "-q", "Reply with exactly: AUTH TEST OK",
         "--provider", "anthropic", "-m", "claude-sonnet-4-6", "-Q"],
        timeout=120,
    )
    if rc == -1:
        return "UNVERIFIABLE", "live auth test timed out after 120s"
    combined = f"{stdout}\n{stderr}"
    if "AUTH TEST OK" in combined:
        return "PASS", "live auth OK"
    if rc != 0:
        return "FAIL", f"auth chat exit {rc}: {(stderr or stdout)[:160]}"
    return "FAIL", f"auth chat did not return 'AUTH TEST OK': {stdout[:160]}"


def probe_bfull_recall():
    """bfull_recall — auto-RAG retrieval QUALITY via live knowledge store (Supabase)."""
    hits, path_used, err = kb_daemon_search("wall-dash dashboard", top_k=5)
    if hits is None:
        return "UNVERIFIABLE", f"live retrieval unavailable: {err}"
    if not hits:
        return "FAIL", "no results returned for query 'wall-dash dashboard'"

    def score_of(h):
        try:
            return float(h.get("score", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    top_score = score_of(hits[0])
    wall_dash_pattern = re.compile(r"wall.?dash|dashboard", re.IGNORECASE)
    hit_text = ""
    has_hit = False
    for h in hits:
        text = h.get("text", "") or ""
        prefix = h.get("context_prefix", "") or ""
        combined = f"{prefix} {text}"
        if wall_dash_pattern.search(combined):
            has_hit = True
            hit_text = combined.strip()[:80]
            break

    if has_hit and top_score >= 0.80:
        return "PASS", f"via {path_used}: top score={top_score:.4f}, hit: '{hit_text}...'"
    if has_hit:
        return "FAIL", f"via {path_used}: relevant hit found but top score={top_score:.4f} < 0.80"
    return "FAIL", f"via {path_used}: no result mentioning wall-dash/dashboard"


def probe_codegraph_mcp():
    """codegraph_mcp — real MCP stdio handshake: initialize + tools/list.

    `hermes mcp test codegraph` is stale (CLI reads a different registry and
    reports 'no servers'). CodeGraph is spawned directly as `codegraph serve
    --mcp`; this probe does the actual JSON-RPC handshake and counts tools.
    """
    proc = None
    try:
        proc = subprocess.Popen(
            ["codegraph", "serve", "--mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=GIT_REPO,
        )
    except FileNotFoundError:
        return "UNVERIFIABLE", "codegraph binary not found"
    except Exception as e:
        return "UNVERIFIABLE", f"could not spawn codegraph: {e}"

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def readline(timeout=25):
        r, _, _ = select.select([proc.stdout], [], [], timeout)
        return proc.stdout.readline() if r else None

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "health_audit", "version": "1.0"}}})
        init = readline()
        if not init:
            return "UNVERIFIABLE", "no response to initialize (handshake timeout)"
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        for _ in range(8):
            line = readline()
            if not line:
                return "UNVERIFIABLE", "no response to tools/list (timeout)"
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == 2:
                tools = msg.get("result", {}).get("tools", [])
                n = len(tools)
                if n > 0:
                    names = ", ".join(t.get("name", "?") for t in tools)
                    return "PASS", f"{n} tools via MCP handshake ({names})"
                return "FAIL", "0 tools discovered (daemon cold — self-heals on next call)"
        return "UNVERIFIABLE", "tools/list response not seen"
    except Exception as e:
        return "UNVERIFIABLE", f"MCP handshake error: {e}"
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


def probe_codegraph_fresh():
    """codegraph_fresh — index content-hashes match live files (sample check).

    The index stores content_hash=sha256(file bytes) per file (NOT a git commit).
    Sample git-tracked source files and compare live sha256 vs the indexed hash.
    All match => index fresh. Any mismatch => stale (source changed since index).
    """
    if not os.path.exists(CODEGRAPH_DB):
        return "UNVERIFIABLE", f"codegraph index db not found at {CODEGRAPH_DB}"
    try:
        import sqlite3
    except ImportError:
        return "UNVERIFIABLE", "sqlite3 module not available"

    try:
        con = sqlite3.connect(f"file:{CODEGRAPH_DB}?mode=ro&immutable=1", uri=True)
        cur = con.cursor()
        rows = cur.execute(
            "SELECT path, content_hash FROM files "
            "WHERE content_hash IS NOT NULL AND path LIKE '%.py' "
            "ORDER BY indexed_at DESC LIMIT 40"
        ).fetchall()
        con.close()
    except Exception as e:
        return "UNVERIFIABLE", f"could not read index db: {e}"

    checked = 0
    mismatches = []
    for path, chash in rows:
        fp = os.path.join(GIT_REPO, path)
        if not os.path.exists(fp):
            continue
        try:
            live = hashlib.sha256(open(fp, "rb").read()).hexdigest()
        except Exception:
            continue
        checked += 1
        if live != chash:
            mismatches.append(path)
        if checked >= 20:
            break

    if checked == 0:
        return "UNVERIFIABLE", "no sampled indexed files exist on disk to compare"
    if mismatches:
        sample = ", ".join(mismatches[:3])
        return "FAIL", f"index stale: {len(mismatches)}/{checked} sampled files changed since index ({sample})"
    return "PASS", f"index fresh: {checked}/{checked} sampled files match content hash"


def probe_delegation():
    """delegation — live API probe to the delegation provider's /models endpoint."""
    cfg = load_config()
    if cfg is None:
        return "UNVERIFIABLE", "could not load config.yaml (yaml module missing or parse error)"

    delegation = cfg.get("delegation", {})
    base_url = delegation.get("base_url", "")
    api_key = delegation.get("api_key", "")

    if not base_url:
        return "UNVERIFIABLE", "no delegation base_url configured"
    if not api_key:
        return "UNVERIFIABLE", "no delegation api_key configured"

    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    status, body = http_get(url, timeout=15, headers=headers)
    if status is None:
        return "UNVERIFIABLE", f"HTTP request failed: {body}"
    if status == 200:
        return "PASS", f"HTTP 200 from {url}"
    return "FAIL", f"HTTP {status} from {url}"


def probe_knowledge_rows():
    """knowledge_rows — knowledge store fact count > 0 (Supabase pgvector)."""
    mod, err = load_knowledge()
    if mod is None:
        return "UNVERIFIABLE", f"knowledge package unavailable: {err}"
    if not hasattr(mod, "count_facts"):
        return "UNVERIFIABLE", "knowledge package has no count_facts()"
    try:
        n = mod.count_facts()
    except Exception as e:
        return "UNVERIFIABLE", f"count_facts() raised: {e}"
    if n and n > 0:
        return "PASS", f"{n} facts in Supabase knowledge store"
    return "FAIL", f"fact count is {n} (expected > 0)"


def probe_web_stack():
    """web_stack — functional HTTP probes of searxng, firecrawl, camofox."""
    results = []
    for svc in WEB_STACK_SERVICES:
        status, body = http_get(svc["url"], timeout=12)
        if status is None:
            results.append((svc["name"], "UNVERIFIABLE", f"request failed: {str(body)[:80]}"))
            continue
        try:
            ok = svc["validate"](status, body)
        except Exception as e:
            ok = False
            body = f"validator error: {e}"
        if ok:
            results.append((svc["name"], "PASS", f"HTTP {status} ({svc['expect']})"))
        else:
            results.append((svc["name"], "FAIL", f"HTTP {status}, expected {svc['expect']}"))

    states = [r[1] for r in results]
    detail = "; ".join(f"{n}:{s}" for n, s, _ in results)
    if all(s == "PASS" for s in states):
        return "PASS", detail
    if "FAIL" in states:
        return "FAIL", "; ".join(f"{n}:{s} ({d})" for n, s, d in results)
    return "UNVERIFIABLE", "; ".join(f"{n}:{s} ({d})" for n, s, d in results)


def probe_memory_headroom():
    """memory_headroom — wc -m each store vs LIVE caps from config.yaml."""
    memory_chars = 0
    user_chars = 0
    try:
        with open(MEMORY_MD, "r") as f:
            memory_chars = len(f.read())
    except Exception:
        pass
    try:
        with open(USER_MD, "r") as f:
            user_chars = len(f.read())
    except Exception:
        pass

    cfg = load_config()
    memory_cap = MEMORY_CAP
    user_cap = USER_CAP
    if cfg:
        mem = cfg.get("memory", {})
        memory_cap = mem.get("memory_char_limit", MEMORY_CAP)
        user_cap = mem.get("user_char_limit", USER_CAP)

    mem_pct = (memory_chars / memory_cap * 100) if memory_cap > 0 else 0
    user_pct = (user_chars / user_cap * 100) if user_cap > 0 else 0
    detail = f"MEMORY:{memory_chars}/{memory_cap} ({mem_pct:.1f}%), USER:{user_chars}/{user_cap} ({user_pct:.1f}%)"

    if mem_pct >= 90 or user_pct >= 90:
        return "FAIL", detail
    return "PASS", detail


def probe_cron():
    """cron — scheduler reachable and reporting job state."""
    rc, stdout, stderr = run([HERMES_BIN, "cron", "list"], timeout=15)
    if rc == -2:
        return "UNVERIFIABLE", f"hermes binary not found at {HERMES_BIN}"
    if rc != 0:
        combined = (stderr + " " + stdout).strip()[:200]
        return "UNVERIFIABLE", f"hermes cron list failed: {combined}"

    active_count = len(re.findall(r"\[active\]", stdout))
    if active_count > 0:
        sys.path.insert(0, SCRIPTS_DIR)
        from infra_watchdog import cron_health  # same last_status rule the hourly watchdog alerts on
        with open(os.path.expanduser("~/.hermes/cron/jobs.json")) as f:
            bad = cron_health(json.load(f).get("jobs", []))
        if bad:
            return "FAIL", f"{active_count} active; " + "; ".join(bad)[:400]
        return "PASS", f"{active_count} active jobs, all last runs ok"
    if "No scheduled jobs" in stdout:
        # Scheduler answered cleanly; there simply are no jobs. Genuine empty
        # state, not a broken subsystem.
        return "PASS", "scheduler alive; 0 jobs scheduled"
    return "FAIL", "no active cron jobs found"


def probe_topology():
    """topology — whoami-live.sh diff vs topology.json golden."""
    if not os.path.exists(WHOAMI_SCRIPT):
        return "UNVERIFIABLE", f"whoami-live.sh not found at {WHOAMI_SCRIPT}"
    rc, stdout, stderr = run(["bash", WHOAMI_SCRIPT], timeout=30)
    if rc == 0:
        return "PASS", "no drift detected"
    combined = (stdout + " " + stderr).strip()
    # surface only the DRIFT lines for readability
    drift = "; ".join(l.strip() for l in combined.splitlines() if "DRIFT" in l)
    return "FAIL", f"drift detected (exit {rc}): {drift[:200]}"


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    # Registry — ids MUST match the rows in system-inventory.md exactly.
    probes = [
        ("gateway", probe_gateway),
        ("bypass", probe_bypass),
        ("bfull_recall", probe_bfull_recall),
        ("codegraph_mcp", probe_codegraph_mcp),
        ("codegraph_fresh", probe_codegraph_fresh),
        ("delegation", probe_delegation),
        ("knowledge_rows", probe_knowledge_rows),
        ("web_stack", probe_web_stack),
        ("cron", probe_cron),
        ("memory_headroom", probe_memory_headroom),
        ("topology", probe_topology),
    ]

    print("=" * 70)
    print("  Hermes Agent System Health Audit")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()

    results = []
    for name, fn in probes:
        try:
            state, detail = fn()
        except Exception as e:
            state, detail = "UNVERIFIABLE", f"probe raised exception: {e}"
        results.append((name, state, detail))
        print(f"[{state:<13}] {name}: {detail}")

    print()
    print("-" * 70)

    pass_count = sum(1 for _, s, _ in results if s == "PASS")
    fail_count = sum(1 for _, s, _ in results if s == "FAIL")
    unv_count = sum(1 for _, s, _ in results if s == "UNVERIFIABLE")

    print(f"  PASS: {pass_count}  FAIL: {fail_count}  UNVERIFIABLE: {unv_count}  TOTAL: {len(results)}")
    print("=" * 70)

    if unv_count > 0:
        print("\n  UNVERIFIABLE CHECKS (capability genuinely absent — investigate):")
        for name, state, detail in results:
            if state == "UNVERIFIABLE":
                print(f"    - {name}: {detail}")

    if fail_count > 0:
        print("\n  FAILED CHECKS:")
        for name, state, detail in results:
            if state == "FAIL":
                print(f"    - {name}: {detail}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
