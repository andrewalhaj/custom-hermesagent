#!/usr/bin/env python3
"""
kanban_export.py — snapshot the kanban board to a static JSON the wall-dash
Projects tab can fetch. Read-only against kanban.db; pushes to the HA box.

WHY: kanban.db lives on the worker box; the dashboard is nginx on the HA box
with no path to a cross-host DB. This bridges them with a static file — the
fail-safe choice (stale data beats a broken dashboard, no new service).

OUTPUT shape (kanban-state.json):
  {
    "generated_at": <unix>, "generated_iso": "...",
    "boards": [
      {"slug","name","counts":{status:n},
       "tasks":[{"id","title","status","assignee","priority","created_at",
                 "completed_at"}]}
    ],
    "totals": {status: n}
  }

Tasks are capped + ordered (active first, then recent) so the file stays small.
Run from cron every few minutes. Silent on success unless --verbose.

BOARD ROUTING
Tasks are routed to named boards by title prefix (case-insensitive).
Boards are always emitted in BOARD_ORDER, even if empty (so the dropdown
shows all boards regardless of whether they have tasks yet).
"""
import json, os, sqlite3, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_env import require

HERMES = os.path.expanduser("~/.hermes")
DB = f"{HERMES}/kanban.db"
LOCAL_OUT = "/tmp/kanban-state.json"
HA_HOST = require("HA_HOST")
HA_DEST = "wall-dash/kanban-state.json"  # relative to the remote login home
MAX_TASKS_PER_BOARD = 60          # keep the file lean
ACTIVE = ("ready", "running", "blocked", "todo", "triage")

VERBOSE = "--verbose" in sys.argv

# Board definitions — ORDER matters (first match wins for prefix routing).
# slug: used as the JS filter key
# name: display name in the dropdown
# prefix: case-insensitive title prefix that assigns a task to this board
#         (None = catch-all for tasks that don't match any prefix)
BOARD_DEFS = [
    {"slug": "dm-voice-board", "name": "DM Voice Board", "prefix": "dm voice board"},
    {"slug": "mealio",         "name": "Mealio",          "prefix": "mealio"},
    {"slug": "default",        "name": "Other",           "prefix": None},  # catch-all
]

# Emit boards in this order (all boards always appear even when empty)
BOARD_ORDER = [b["slug"] for b in BOARD_DEFS]


def log(*a):
    if VERBOSE:
        print(*a)


def route_task(title: str) -> str:
    """Return the slug for a task based on its title prefix."""
    t = (title or "").lower().strip()
    for bd in BOARD_DEFS:
        if bd["prefix"] and t.startswith(bd["prefix"]):
            return bd["slug"]
    return "default"


def build_snapshot():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "select id,title,status,assignee,priority,created_at,completed_at "
        "from tasks"
    ).fetchall()

    # Initialise all boards (even empty ones so the dropdown always shows them)
    boards = {
        bd["slug"]: {
            "slug": bd["slug"],
            "name": bd["name"],
            "counts": {},
            "_tasks": [],
        }
        for bd in BOARD_DEFS
    }
    totals = {}

    for r in rows:
        bslug = route_task(r["title"])
        b = boards[bslug]
        st = (r["status"] or "unknown")
        b["counts"][st] = b["counts"].get(st, 0) + 1
        totals[st] = totals.get(st, 0) + 1
        b["_tasks"].append({
            "id": r["id"], "title": r["title"], "status": st,
            "assignee": r["assignee"], "priority": r["priority"],
            "created_at": r["created_at"], "completed_at": r["completed_at"],
        })

    # order tasks: active first (by priority then recency), then recent finished
    def sort_key(t):
        active = 0 if t["status"] in ACTIVE else 1
        return (active, -(t["priority"] or 0), -(t["created_at"] or 0))

    out_boards = []
    for slug in BOARD_ORDER:
        b = boards[slug]
        b["_tasks"].sort(key=sort_key)
        b["tasks"] = b["_tasks"][:MAX_TASKS_PER_BOARD]
        del b["_tasks"]
        out_boards.append(b)

    now = int(time.time())
    return {
        "generated_at": now,
        "generated_iso": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "boards": out_boards,
        "totals": totals,
    }


def main():
    snap = build_snapshot()
    with open(LOCAL_OUT, "w") as f:
        json.dump(snap, f, separators=(",", ":"))
    size = os.path.getsize(LOCAL_OUT)
    log(f"wrote {LOCAL_OUT} ({size}b, {sum(snap['totals'].values())} tasks)")

    # push to HA box web root
    r = subprocess.run(
        ["scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15",
         LOCAL_OUT, f"root@{HA_HOST}:{HA_DEST}"],
        capture_output=True, text=True, timeout=40,
    )
    if r.returncode != 0:
        # non-fatal: log to stderr so a cron failure is visible but doesn't spam
        sys.stderr.write(f"[kanban_export] scp failed rc={r.returncode}: {r.stderr.strip()}\n")
        sys.exit(1)
    log(f"pushed to {HA_HOST}:{HA_DEST}")
    sys.exit(0)


if __name__ == "__main__":
    main()
