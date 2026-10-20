"""unified_search ranking benchmark (gate for step 8.1): what prefetch actually ranks, across all stores.

Queries: eval_v2's benchmark_queries.json (abstention rows skipped; hit = expected_substring in a result)
plus factbench's direct questions (hit = any needle in a result). Reports Success@1/5 and MRR over the
top 10, with a bootstrap 95% CI on MRR — deltas inside the CI width are noise.
Run (gateway venv, from ~/.hermes/scripts):
  RERANK=true HF_HUB_OFFLINE=1 /usr/local/lib/hermes-agent/venv/bin/python -m knowledge.unified_bench
"""
import json, os, random, sys

from knowledge.config import DB_DIR
from knowledge.factbench import load_facts


def load_queries(db_dir=DB_DIR):
    """[(query, needles)] — needles are lowercase substrings; any one in a result's content is a hit."""
    rows = json.load(open(os.path.join(db_dir, "benchmark_queries.json")))
    qs = [(q["query"], [q["expected_substring"].lower()]) for q in rows if not q.get("expect_no_hit")]
    return qs + [(direct, needles) for needles, direct, _ in load_facts(os.path.join(db_dir, "factbench.json"))]


def rank_of(results, needles):
    for i, r in enumerate(results):
        text = ((r.get("title") or "") + " " + (r.get("content") or "")).lower()
        if any(n in text for n in needles):
            return i
    return -1


def run(search, queries, top_k=10, bootstrap_n=1000, seed=42):
    ranks = [rank_of(search(q, top_k=top_k) or [], needles) for q, needles in queries]
    rrs = [1.0 / (r + 1) if r >= 0 else 0.0 for r in ranks]
    n = len(ranks)
    rng = random.Random(seed)
    boots = sorted(sum(rng.choices(rrs, k=n)) / n for _ in range(bootstrap_n))
    return {"n": n,
            "success@1": round(sum(r == 0 for r in ranks) / n, 4),
            "success@5": round(sum(0 <= r < 5 for r in ranks) / n, 4),
            "MRR": round(sum(rrs) / n, 4),
            "MRR_ci95": (round(boots[int(bootstrap_n * 0.025)], 4), round(boots[int(bootstrap_n * 0.975)], 4)),
            "misses": [q for (q, _), r in zip(queries, ranks) if not 0 <= r < 5]}


if __name__ == "__main__":
    from knowledge.unified_search import unified_search
    os.environ.setdefault("REF_CHUNKS", "off")  # results identical to 'log'; skip the shadow-log thread
    out = run(unified_search, load_queries())
    print(f"unified_search n={out['n']}  S@1 {out['success@1']:.2f}  S@5 {out['success@5']:.2f}  "
          f"MRR {out['MRR']:.4f}  CI95 {out['MRR_ci95'][0]}–{out['MRR_ci95'][1]}")
    for q in out["misses"]:
        print(f"  miss@5: {q[:70]}")
    if "--json" in sys.argv:
        print(json.dumps(out))
