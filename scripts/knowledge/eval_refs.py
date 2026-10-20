"""Reference-retrieval eval (RG1): score the reference search path with REF_CHUNKS off vs on.

Inputs (knowledge_db): benchmark_references.json (list of query rows) + qrels_references.json
({query: {doc_stem: grade}}); doc stems are `<stem>` for references and `reports/<stem>` for reports,
matching the ids returned by ref_chunks / unified_search._search_references.

Row fields used: query, depth ("deep" = ground-truth answer offset > 500 chars | "control" | null),
synth ("single_hop" | "multi_hop_abstract" | "multi_hop_specific").

Search path: knowledge.unified_search._search_references (reads REF_CHUNKS at call time).
Arms (env set in-process, restored afterwards; no live config touched):
  off      REF_CHUNKS unset                      -> A0 keyword search over first 500 chars + filename
  on       REF_CHUNKS=on, REF_CHUNKS_MAX_SLOTS=FETCH -> chunk index (S6), uncapped slots  [decision arm]
  on_prod  REF_CHUNKS=on, REF_CHUNKS_MAX_SLOTS=2     -> as shipped (2 chunk slots)        [informational]
Hits are collapsed to distinct docs (order kept) and cut at 10 docs.

Metrics reuse knowledge.eval._dcg (nDCG@10); S@k / MRR follow knowledge.eval.run's definitions;
CI = percentile bootstrap (seeded) as in knowledge.eval.run / unified_bench.run, plus a paired delta.

Run (from ~/.hermes/scripts):
  HF_HUB_OFFLINE=1 /usr/local/lib/hermes-agent/venv/bin/python3 -m knowledge.eval_refs \
      [--arms off,on,on_prod] [--out results.json]
"""
import argparse, json, os, random, sys

from knowledge.config import DB_DIR
from knowledge.eval import _dcg  # reuse, don't copy

FETCH = 40          # chunks/docs requested from the search path before doc-level dedup
MAX_DOCS = 10
ARMS = {
    "off": {"REF_CHUNKS": None, "REF_CHUNKS_MAX_SLOTS": None},
    "on": {"REF_CHUNKS": "on", "REF_CHUNKS_MAX_SLOTS": str(FETCH)},
    "on_prod": {"REF_CHUNKS": "on", "REF_CHUNKS_MAX_SLOTS": "2"},
}


def load_eval_set(db_dir=DB_DIR):
    rows = json.load(open(os.path.join(db_dir, "benchmark_references.json")))
    qrels = json.load(open(os.path.join(db_dir, "qrels_references.json")))
    return rows, qrels


def dedup_docs(hits, max_docs=MAX_DOCS):
    """Collapse ranked hits to ordered distinct doc ids."""
    seen, out = set(), []
    for h in hits or []:
        d = str(h.get("id"))
        if d not in seen:
            seen.add(d)
            out.append(d)
            if len(out) >= max_docs:
                break
    return out


def score_query(docs, rel, top_k=10):
    """rank (0-based first relevant doc, -1 if none), reciprocal rank, nDCG@10 vs graded rel {doc: grade}."""
    rank = next((i for i, d in enumerate(docs) if d in rel), -1)
    grades = [rel.get(d, 0) for d in docs[:top_k]]
    ideal = sorted(rel.values(), reverse=True)[:top_k]
    idcg = _dcg(ideal)
    ndcg = _dcg(grades) / idcg if idcg > 0 else 0.0
    return {"rank": rank, "rr": 1.0 / (rank + 1) if rank >= 0 else 0.0, "ndcg10": ndcg}


def run_arm(search_fn, rows, qrels, arm):
    """Evaluate one arm. search_fn(query, top_k) -> hits. Env is set for the duration only."""
    saved = {k: os.environ.get(k) for k in ARMS[arm]}
    try:
        for k, v in ARMS[arm].items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        out = []
        for r in rows:
            docs = dedup_docs(search_fn(r["query"], FETCH))
            s = score_query(docs, qrels[r["query"]])
            rel = qrels[r["query"]]
            gt_corpus = "reports" if any(d.startswith("reports/") for d in rel) else "references"
            out.append({"query": r["query"], "depth": r.get("depth"), "synth": r.get("synth"),
                        "gt_corpus": gt_corpus, "docs": docs[:5], **s})
        return out
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def default_search(query, top_k):
    from knowledge.unified_search import _search_references
    return _search_references(query, [], top_k=top_k)


def _vals(res, metric):
    if metric == "S@1":
        return [float(r["rank"] == 0) for r in res]
    if metric == "S@5":
        return [float(0 <= r["rank"] < 5) for r in res]
    if metric == "MRR":
        return [r["rr"] for r in res]
    if metric == "nDCG@10":
        return [r["ndcg10"] for r in res]
    raise KeyError(metric)


METRICS = ("S@1", "S@5", "MRR", "nDCG@10")


def bootstrap_ci(vals, n=2000, seed=42):
    rng = random.Random(seed)
    k = len(vals)
    boots = sorted(sum(rng.choices(vals, k=k)) / k for _ in range(n))
    return boots[int(n * 0.025)], boots[int(n * 0.975)]


def paired_delta_ci(a, b, n=10000, seed=0):
    """Mean(b - a) with percentile bootstrap CI over paired per-query values."""
    d = [y - x for x, y in zip(a, b)]
    k = len(d)
    rng = random.Random(seed)
    boots = sorted(sum(rng.choices(d, k=k)) / k for _ in range(n))
    return sum(d) / k, boots[int(n * 0.025)], boots[int(n * 0.975)]


def summarize(res, subset=lambda r: True):
    sub = [r for r in res if subset(r)]
    if not sub:
        return {"n": 0}
    out = {"n": len(sub)}
    for m in METRICS:
        v = _vals(sub, m)
        lo, hi = bootstrap_ci(v)
        out[m] = {"mean": round(sum(v) / len(v), 4), "ci": [round(lo, 4), round(hi, 4)]}
    return out


def splits(res):
    out = {"all": summarize(res), "deep": summarize(res, lambda r: r["depth"] == "deep"),
           "control": summarize(res, lambda r: r["depth"] == "control")}
    for t in sorted({r["synth"] for r in res if r["synth"]}):
        out["synth:" + t] = summarize(res, lambda r, t=t: r["synth"] == t)
    for g in sorted({r["gt_corpus"] for r in res if r.get("gt_corpus")}):
        out["gt:" + g] = summarize(res, lambda r, g=g: r.get("gt_corpus") == g)
        out["gt:%s/deep" % g] = summarize(res, lambda r, g=g: r.get("gt_corpus") == g and r["depth"] == "deep")
    return out


def compare(base, other, subset):
    """Paired S@5 delta (other - base) on a subset; also count of queries where other is worse."""
    pb = [r for r in base if subset(r)]
    po = [r for r in other if subset(r)]
    a, b = _vals(pb, "S@5"), _vals(po, "S@5")
    if not a:
        return {"n": 0}
    d, lo, hi = paired_delta_ci(a, b)
    return {"n": len(a), "delta_S@5": round(d, 4), "ci": [round(lo, 4), round(hi, 4)],
            "queries_worse": int(sum(1 for x, y in zip(a, b) if y < x)),
            "queries_better": int(sum(1 for x, y in zip(a, b) if y > x))}


def verdict(base, other, where=lambda r: True):
    """Pre-registered bar: deep S@5 delta CI lower bound > 0 AND control S@5 not worse by more than 1 query.
    `where` restricts the query population (e.g. references-only ground truth, supplementary)."""
    deep = compare(base, other, lambda r: where(r) and r["depth"] == "deep")
    ctrl = compare(base, other, lambda r: where(r) and r["depth"] == "control")
    control_net_loss = None
    if ctrl.get("n"):
        control_net_loss = -round(ctrl["delta_S@5"] * ctrl["n"])
    ok_deep = bool(deep.get("n")) and deep["ci"][0] > 0
    ok_ctrl = control_net_loss is not None and control_net_loss <= 1
    return {"deep": deep, "control": ctrl, "control_net_queries_lost": control_net_loss,
            "pass_deep": ok_deep, "pass_control": ok_ctrl, "PASS": ok_deep and ok_ctrl}


def main(argv=None, search_fn=default_search, db_dir=DB_DIR):
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="off,on,on_prod")
    ap.add_argument("--out", default=None)
    ap.add_argument("--db-dir", default=db_dir)
    args = ap.parse_args(argv)
    rows, qrels = load_eval_set(args.db_dir)
    arms = [a for a in args.arms.split(",") if a]
    per_arm = {a: run_arm(search_fn, rows, qrels, a) for a in arms}
    out = {"n_queries": len(rows), "arms": {a: splits(r) for a, r in per_arm.items()},
           "per_query": per_arm}
    if "off" in per_arm:
        out["verdict"] = {a: verdict(per_arm["off"], per_arm[a]) for a in arms if a != "off"}
        out["verdict_refs_only"] = {a: verdict(per_arm["off"], per_arm[a], lambda r: r.get("gt_corpus") == "references")
                                    for a in arms if a != "off"}
    for a in arms:
        for name, s in out["arms"][a].items():
            if s.get("n"):
                print(f"{a:8s} {name:28s} n={s['n']:3d} " + "  ".join(
                    f"{m} {s[m]['mean']:.3f} [{s[m]['ci'][0]:.2f},{s[m]['ci'][1]:.2f}]" for m in METRICS))
    for a, v in out.get("verdict", {}).items():
        print(f"VERDICT {a}: PASS={v['PASS']} deep={v['deep']} control={v['control']} "
              f"control_net_lost={v['control_net_queries_lost']}")
    for a, v in out.get("verdict_refs_only", {}).items():
        print(f"VERDICT(refs-only, supplementary) {a}: PASS={v['PASS']} deep={v['deep']} control={v['control']} "
              f"control_net_lost={v['control_net_queries_lost']}")
    if args.out:
        json.dump(out, open(args.out, "w"), indent=1)
    return out


if __name__ == "__main__":
    main()
