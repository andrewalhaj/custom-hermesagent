#!/usr/bin/env python3
"""Rollback for the Jev write-path judges. Replays what they removed, from the logs that recorded it.

  jev_restore.py dropped    [--since ISO] [--apply]   facts the store gate dropped (logs/store_gate_shadow.jsonl,
                                                        backend=jev, verdict=drop, stored=false, guard_rejected=false)
  jev_restore.py superseded [--since ISO] [--apply]   rows deleted by the supersede judge (knowledge_db/deleted_facts.jsonl,
                                                        reason jev-supersede:<new_id>) — re-inserted verbatim with their old id
Without --apply it only lists. Digests need no restore: distill re-creates them (reset the watermark to re-run).
"""
import argparse, json, os, sys
H = os.path.expanduser("~/.hermes"); sys.path.insert(0, f"{H}/scripts")

ap = argparse.ArgumentParser(); ap.add_argument("what", choices=["dropped", "superseded"])
ap.add_argument("--since", default="2026-10-21T00:00:00"); ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

if a.what == "dropped":
    rows = [json.loads(l) for l in open(f"{H}/logs/store_gate_shadow.jsonl") if l.strip()]
    rows = [r for r in rows if r.get("backend") == "jev" and r["verdict"] == "drop" and not r["stored"]
            and not r["guard_rejected"] and r["ts"] >= a.since]
    print(f"{len(rows)} dropped facts since {a.since}")
    for r in rows: print(f"  {r['ts']} p={r['p_drop']:.2f} [{r['tag']}] {r['fact'][:100]}")
    if a.apply:
        from knowledge.storage import store
        n = 0
        for r in rows:
            try:
                store(r["fact"], tags=[r["tag"], "session-heartbeat", "jev-restored"], priority="normal",
                      source=r["session_id"], provenance="extracted"); n += 1
            except Exception as e: print("  store failed:", e)
        print(f"re-stored {n}/{len(rows)} (run with STORE_GATE unset or they may be dropped again)")
else:
    rows = [json.loads(l) for l in open(f"{H}/knowledge_db/deleted_facts.jsonl") if l.strip()]
    rows = [r for r in rows if str(r.get("reason", "")).startswith("jev-supersede:") and r["ts"] >= a.since]
    print(f"{len(rows)} judge-superseded rows since {a.since}")
    for r in rows: print(f"  {r['ts']} {r['reason']} | {r['row'].get('text', '')[:100]}")
    if a.apply:
        from knowledge.supabase_client import _supa_insert
        n = 0
        for r in rows:
            row = dict(r["row"]); row.pop("created_at", None)
            try: _supa_insert(row); n += 1
            except Exception as e: print("  insert failed:", row.get("id"), e)
        print(f"re-inserted {n}/{len(rows)} verbatim (same ids)")
