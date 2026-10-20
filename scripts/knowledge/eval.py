"""Retrieval benchmarks over knowledge_db/benchmark_queries.json (+ qrels.json).

eval_benchmark: fast P@5 / MRR smoke check (kbench.py, build_centroids).
run: benchmark v2 (2026-08-15) — Success@1/3/5/8, MRR core/frontier/combined, nDCG@10 against
graded qrels, abstention set (no hit above threshold in top-3), bootstrap 95% CI on MRR
("delta < CI width" = noise), run config recorded.

Usage (from ~/.hermes/scripts):
  RERANK=true HF_HUB_OFFLINE=1 \
    python3 -c "from knowledge.eval import run; run()"
"""
import hashlib, json, math, os, random, subprocess, sys, time

from knowledge.search import search
from knowledge.config import DB_DIR


def eval_benchmark(benchmark_path=None, top_k=8):
    """Run benchmark queries and report precision/recall.

    Benchmark file: JSON array of {query, expected_id, description?}.
    Defaults to ~/.hermes/knowledge_db/benchmark_queries.json.

    Returns dict with metrics and per-query results.
    """
    if benchmark_path is None:
        benchmark_path = os.path.join(DB_DIR, 'benchmark_queries.json')
    if not os.path.exists(benchmark_path):
        print(f"No benchmark file at {benchmark_path}", file=sys.stderr)
        return {'error': 'no_benchmark_file', 'path': benchmark_path}

    with open(benchmark_path) as f:
        queries = json.load(f)

    results = []
    for q in queries:
        hits = search(q['query'], top_k=top_k)
        hit_ids = [h['id'] for h in hits]
        # Ground-truth matching: prefer 'expected_substring' (re-chunk robust —
        # survives dedup re-IDing) over the legacy volatile 'expected_id'.
        sub = q.get('expected_substring')
        if sub:
            sub_l = sub.lower()
            rank = -1
            for i, h in enumerate(hits):
                if sub_l in (h.get('text') or '').lower():
                    rank = i
                    break
            found = rank >= 0
            expected = sub
        else:
            expected = q.get('expected_id')
            found = expected in hit_ids
            rank = hit_ids.index(expected) if found else -1
        results.append({
            'query': q['query'],
            'expected': expected,
            'found': found,
            'rank': rank,
            'top_hit': hit_ids[0] if hits else None,
            'top_score': hits[0]['score'] if hits else 0,
            'description': q.get('description', ''),
        })

    p_at_5 = sum(1 for r in results if r['found']) / len(results)
    mrr = sum(1.0 / (r['rank'] + 1) for r in results if r['found']) / len(results)
    return {
        'p@5': round(p_at_5, 4),
        'MRR': round(mrr, 4),
        'total': len(results),
        'passed': sum(1 for r in results if r['found']),
        'results': results,
    }


ABSTAIN_THRESHOLD = 0.35  # normalized-score fallback (rerank off)
RAW_ABSTAIN_THRESHOLD = 0.0  # raw bge logit: >0 ≈ genuinely relevant


def _dcg(grades):
    return sum(g / math.log2(i + 2) for i, g in enumerate(grades))


def _run_config():
    bq = os.path.join(DB_DIR, "benchmark_queries.json")
    git = subprocess.run(["git", "-C", DB_DIR, "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    flags = {k: os.environ.get(k) for k in
             ["RERANK", "SECTOR_FILTER",
              "RERANK_REMOTE_URL"]}
    qhash = hashlib.sha256(open(bq, "rb").read()).hexdigest()[:12]
    return {"flags": flags, "query_file_git": git, "query_file_sha": qhash,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}


def run(top_k=10, bootstrap_n=1000, seed=42):
    bq = os.path.join(DB_DIR, "benchmark_queries.json")
    qrels_path = os.path.join(DB_DIR, "qrels.json")
    queries = json.load(open(bq))
    qrels = json.load(open(qrels_path)) if os.path.exists(qrels_path) else {}

    cfg = _run_config()
    results, abstain_results = [], []
    t0 = time.time()

    for q in queries:
        qtext = q["query"]
        hits = search(qtext, top_k=top_k) or []
        hit_ids = [str(h.get("id"))[:8] for h in hits]
        scores = [h.get("score", 0) for h in hits]

        if q.get("expect_no_hit"):
            # abstention: prefer raw cross-encoder logits (absolute scale;
            # >0 ≈ relevant). Fall back to normalized-score threshold when
            # rerank is off (weaker — normalized top is always 1.0).
            raws = [h.get("rerank_raw") for h in hits[:3]]
            if any(r is not None for r in raws):
                confident = any((r or -99) >= RAW_ABSTAIN_THRESHOLD for r in raws)
                top = max((r for r in raws if r is not None), default=-99)
            else:
                confident = any(s >= ABSTAIN_THRESHOLD for s in scores[:3])
                top = scores[0] if scores else 0.0
            abstain_results.append({
                "query": qtext, "pass": not confident,
                "top_score": round(top, 4)})
            continue

        rel = qrels.get(qtext, {})
        sub = (q.get("expected_substring") or "").lower()
        # rank by qrels first, substring fallback (keeps v1 compatibility)
        rank = -1
        for i, h in enumerate(hits):
            hid = str(h.get("id"))[:8]
            txt = (h.get("text") or "").lower()
            if hid in rel or (sub and sub in txt):
                rank = i
                break
        grades = []
        for hid, txt in ((str(h.get("id"))[:8], (h.get("text") or "").lower()) for h in hits):
            if hid in rel:
                grades.append(rel[hid])
            elif sub and sub in txt:
                grades.append(3)
            else:
                grades.append(0)
        ideal = sorted((rel.values() if rel else [3]), reverse=True)[:top_k]
        ndcg = _dcg(grades) / _dcg(ideal) if ideal and _dcg(ideal) > 0 else 0.0
        results.append({
            "query": qtext, "status": q.get("status", "core"),
            "type": q.get("type", "single-fact"), "rank": rank,
            "rr": (1.0 / (rank + 1)) if rank >= 0 else 0.0,
            "ndcg10": round(ndcg, 4)})

    dt = time.time() - t0

    def _agg(rows):
        n = len(rows)
        if not n:
            return {}
        return {
            "n": n,
            "success@1": round(sum(r["rank"] == 0 for r in rows) / n, 4),
            "success@3": round(sum(0 <= r["rank"] < 3 for r in rows) / n, 4),
            "success@5": round(sum(0 <= r["rank"] < 5 for r in rows) / n, 4),
            "success@8": round(sum(0 <= r["rank"] < 8 for r in rows) / n, 4),
            "MRR": round(sum(r["rr"] for r in rows) / n, 4),
            "nDCG@10": round(sum(r["ndcg10"] for r in rows) / n, 4)}

    core = [r for r in results if r["status"] == "core"]
    frontier = [r for r in results if r["status"] != "core"]

    # bootstrap CI on combined MRR
    random.seed(seed)
    rrs = [r["rr"] for r in results]
    boots = sorted(sum(random.choices(rrs, k=len(rrs))) / len(rrs)
                   for _ in range(bootstrap_n))
    ci = (round(boots[int(bootstrap_n * 0.025)], 4),
          round(boots[int(bootstrap_n * 0.975)], 4))

    out = {"config": cfg, "core": _agg(core), "frontier": _agg(frontier),
           "combined": _agg(results),
           "abstention": {"n": len(abstain_results),
                          "passed": sum(a["pass"] for a in abstain_results),
                          "details": abstain_results},
           "MRR_ci95": ci, "sec_per_query": round(dt / max(len(queries), 1), 2),
           "results": results}

    print(f"=== benchmark v2 [{cfg['ts']}] flags={ {k:v for k,v in cfg['flags'].items() if v} } "
          f"qfile={cfg['query_file_git']}/{cfg['query_file_sha']} ===")
    for name, agg in (("CORE", out["core"]), ("FRONTIER", out["frontier"]),
                      ("COMBINED", out["combined"])):
        if agg:
            print(f"{name}: n={agg['n']}  S@1 {agg['success@1']:.2f}  S@3 {agg['success@3']:.2f}  "
                  f"S@5 {agg['success@5']:.2f}  S@8 {agg['success@8']:.2f}  "
                  f"MRR {agg['MRR']:.4f}  nDCG@10 {agg['nDCG@10']:.4f}")
    ab = out["abstention"]
    print(f"ABSTENTION: {ab['passed']}/{ab['n']} correctly returned nothing confident")
    for a in ab["details"]:
        if not a["pass"]:
            print(f"  FAIL (over-confident): {a['query'][:60]} top_score={a['top_score']}")
    print(f"MRR 95% CI: {ci[0]}–{ci[1]} (width {round(ci[1]-ci[0],4)}) — deltas inside width = noise")
    print(f"{out['sec_per_query']}s/query")
    for r in results:
        if r["rank"] != 0:
            print(f"  rank {r['rank']}: [{r['type']}] {r['query'][:58]}")
    return out
