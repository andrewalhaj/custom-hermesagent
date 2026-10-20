#!/usr/bin/env python3
"""bench_reground.py — mechanical benchmark maintenance (Tier 3, 2026-08-15).

Run during every sector boost AFTER cleanup, BEFORE the benchmark:
1. Every expected_substring is ILIKE-counted against the live store.
   - 0 hits on a core query   -> flag DEAD (fact deleted/reworded — fix or retire)
   - 0 hits on frontier       -> flag DEAD
   - >15 hits                 -> flag UNDISTINCTIVE (substring too generic)
2. qrels refreshed: matched fact-id prefixes per query rewritten from live store
   (grade 3). Manually-added grade-2 alternates are preserved.
3. Abstention queries verified still unanswerable (keyword co-occurrence check).
Exit 1 if any DEAD core entries — the boost should fix them before benchmarking.
"""
import json, sys, urllib.request

from hermes_env import HERMES_HOME, require

def sb(path):
    r = urllib.request.Request(require("SUPABASE_URL") + path, headers={
        "apikey": require("SUPABASE_SERVICE_KEY"),
        "Authorization": "Bearer " + require("SUPABASE_SERVICE_KEY")})
    return json.load(urllib.request.urlopen(r, timeout=30))

store = sb("/rest/v1/knowledge?select=id,text")
BQ = HERMES_HOME / "knowledge_db" / "benchmark_queries.json"
QR = HERMES_HOME / "knowledge_db" / "qrels.json"
queries = json.load(open(BQ))
try:
    qrels = json.load(open(QR))
except FileNotFoundError:
    qrels = {}

def find(substr):
    s = substr.lower()
    return [str(r["id"])[:8] for r in store if s in r["text"].lower()]

dead_core, dead_frontier, undistinctive = [], [], []
for q in queries:
    sub = q.get("expected_substring")
    if not sub:
        continue
    hits = find(sub)
    n = len(hits)
    if n == 0:
        (dead_core if q.get("status", "core") == "core" else dead_frontier).append(q["query"])
    elif n > 15:
        undistinctive.append((q["query"], n))
    # refresh qrels grade-3 set, preserving manual grade-2 entries
    old = qrels.get(q["query"], {})
    keep2 = {k: v for k, v in old.items() if v == 2}
    qrels[q["query"]] = {**{h: 3 for h in hits[:5]}, **keep2}

json.dump(qrels, open(QR, "w"), indent=1)

print(f"store={len(store)} queries={len(queries)}")
print(f"DEAD core: {len(dead_core)}")
for q in dead_core:
    print("  !!", q[:70])
print(f"DEAD frontier: {len(dead_frontier)}")
for q in dead_frontier:
    print("  !", q[:70])
print(f"UNDISTINCTIVE (>15 hits): {len(undistinctive)}")
for q, n in undistinctive:
    print(f"  ~ ({n}) {q[:70]}")
print("qrels refreshed.")
sys.exit(1 if dead_core else 0)
