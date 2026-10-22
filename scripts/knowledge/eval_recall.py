"""RECALL yardstick: does the REAL auto-prefetch block contain the answer to a recall-shaped question?

Input : knowledge_db/benchmark_recall.json  (question, answer_substrings, source, source_type, date_asked_context)
Path  : plugins/supabase/__init__.py SupabaseMemoryProvider.prefetch(question) -- the exact function the gateway
        calls every turn (unified_search top_k=5 + qwen2.5:14b planned-action search top_k=3, 3 s cap, filters,
        250-char truncation, dedupe). Imported by file path; nothing re-implemented.
Hit   : any answer_substring (lowercased, whitespace-collapsed) occurs in the returned block.
Stats : hit rate, percentile-bootstrap 95% CI (seeded, as knowledge.eval.run), split by source_type.

Env   : whatever is exported when you run it reaches prefetch (DIGEST_MAX_SLOTS, RECALL_WIDE, SESSION_STORE,
        REF_RESERVED_SLOTS, SUPABASE_PREFETCH_TIMEOUT_S, ...). Retrieval flags NOT exported are filled from the live
        gateway process (/proc/<MainPID>/environ, allowlist below) so a bare run measures "live defaults".
        --no-live-env disables that fill; an exported value (even empty) always wins.

Run (from anywhere):
  HF_HUB_OFFLINE=1 /usr/local/lib/hermes-agent/venv/bin/python ~/.hermes/scripts/knowledge/eval_recall.py \
      [--bench PATH] [--out results.json] [--no-live-env] [--boot 10000] [--seed 42] [--top 3]
Read-only: no writes except --out.
"""
import argparse, importlib.util, json, os, random, subprocess, sys, time

HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes")
BENCH = os.path.join(HERMES_HOME, "knowledge_db", "benchmark_recall.json")
PLUGIN = os.path.join(HERMES_HOME, "plugins", "supabase", "__init__.py")
AGENT_DIR = os.environ.get("HERMES_AGENT_DIR", "/usr/local/lib/hermes-agent")  # provides agent.memory_provider
# Retrieval-behaviour flags only (no secrets/URLs). Extend when new flags ship.
FLAGS = ("RERANK", "SECTOR_FILTER", "QUERY_EXPAND", "BFULL_QUERY_EXPAND", "METADATA_FILTER",
         "REF_CHUNKS", "REF_CHUNKS_MAX_SLOTS", "REF_RESERVED_SLOTS", "DIGEST_MAX_SLOTS", "PROV_LABELS",
         "SUPABASE_PREFETCH_TIMEOUT_S", "RECALL_WIDE", "SESSION_STORE")


def live_env():
    """Allowlisted flags from the running gateway ({} if it can't be read)."""
    try:
        pid = subprocess.run(["systemctl", "--user", "show", "hermes-gateway.service", "-p", "MainPID", "--value"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        raw = open(f"/proc/{int(pid)}/environ", "rb").read().split(b"\0")
    except Exception:
        return {}
    kv = (d.decode("utf-8", "replace").partition("=") for d in raw)
    return {k: v for k, _, v in kv if k in FLAGS}


def load_prefetch():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    for p in (AGENT_DIR, os.path.join(HERMES_HOME, "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location("hermes_supabase_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    prov = mod.SupabaseMemoryProvider()
    prov.initialize("eval-recall")
    return prov.prefetch


def norm(t):
    return " ".join(t.split()).lower()


def items(block):
    return [l[2:] for l in block.splitlines() if l.startswith("- [")]


def bootstrap_ci(hits, n=10000, seed=42):
    if not hits:
        return (0.0, 0.0)
    rng = random.Random(seed)
    k = len(hits)
    b = sorted(sum(rng.choices(hits, k=k)) / k for _ in range(n))
    return (b[int(n * 0.025)], b[min(n - 1, int(n * 0.975))])


def score(rows, prefetch, top=3):
    res = []
    for r in rows:
        t0 = time.time()
        block = prefetch(r["question"]) or ""
        dt = time.time() - t0
        nb, its = norm(block), items(block)
        subs = [norm(s) for s in r["answer_substrings"]]
        rank = next((i + 1 for i, it in enumerate(its) if any(s in norm(it) for s in subs)), None)
        res.append({"question": r["question"], "source": r["source"], "source_type": r["source_type"], "group": r.get("group"),
                    "hit": any(s in nb for s in subs), "rank": rank, "empty": not block, "n_items": len(its),
                    "latency_s": round(dt, 2), "top": [it[:140] for it in its[:top]], "block": block})
    return res


def judge(res):
    """Second scoring column: Jev's p(recalled block answers the question). Gold substring stays the primary metric;
    this scores the questions gold cannot (open-ended) and reports agreement with gold where both exist."""
    from knowledge import jev
    for x in res:
        try:
            x["jev"] = round(float(jev.ask({"question": x["question"]}, {"a": jev.recall_judge_question(x["block"])}, timeout=20)["a"]["noul"]), 3)
        except Exception as e:
            x["jev"] = None
            print(f"judge failed: {e}", file=sys.stderr)
    both = [x for x in res if x["jev"] is not None]
    agree = sum((x["jev"] >= 0.5) == x["hit"] for x in both)
    return {"n": len(both), "mean": round(sum(x["jev"] for x in both) / len(both), 4) if both else None,
            "agree_with_gold": round(agree / len(both), 4) if both else None,
            "judge_yes_gold_miss": [x["question"] for x in both if x["jev"] >= 0.5 and not x["hit"]],
            "judge_no_gold_hit": [x["question"] for x in both if x["jev"] < 0.5 and x["hit"]]}


def summarize(res, boot, seed):
    out = {}
    for name, sel in (("all", res), ("knowledge", [x for x in res if x["source_type"] == "knowledge"]),
                      ("session", [x for x in res if x["source_type"] == "session" and x.get("group") != "paraphrase"]),
                      ("paraphrase", [x for x in res if x.get("group") == "paraphrase"])):
        if not sel:
            continue
        h = [int(x["hit"]) for x in sel]
        lo, hi = bootstrap_ci(h, boot, seed)
        out[name] = {"n": len(h), "hits": sum(h), "rate": round(sum(h) / len(h), 4) if h else 0.0,
                     "ci95": [round(lo, 4), round(hi, 4)]}
    return out


def print_summary(s):
    for name in ("all", "knowledge", "session", "paraphrase"):
        if name in s:  # summarize() omits empty buckets
            x = s[name]
            print(f"{name:10s} hit {x['hits']}/{x['n']} = {x['rate']:.1%}  95% CI [{x['ci95'][0]:.1%}, {x['ci95'][1]:.1%}]")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bench", default=BENCH)
    ap.add_argument("--out")
    ap.add_argument("--no-live-env", action="store_true")
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--judge", action="store_true", help="add a Jev scoring column (offline, ~$0.001/question)")
    a = ap.parse_args(argv)

    rows = json.load(open(a.bench))
    assert rows and all({"question", "answer_substrings", "source", "source_type"} <= set(r) for r in rows), "bad bench"
    filled = {} if a.no_live_env else {k: v for k, v in live_env().items() if k not in os.environ}
    os.environ.update(filled)
    eff = {k: os.environ[k] for k in FLAGS if k in os.environ}
    print(f"flags (live-filled: {sorted(filled) or 'none'}): {eff}")
    prefetch = load_prefetch()

    res = score(rows, prefetch, a.top)
    s = summarize(res, a.boot, a.seed)
    print_summary(s)
    if a.judge:
        s["jev"] = j = judge(res)
        print(f"jev judge  mean p={j['mean']}  agree with gold {j['agree_with_gold']:.1%} (n={j['n']})  "
              f"judge-yes/gold-miss {len(j['judge_yes_gold_miss'])}  judge-no/gold-hit {len(j['judge_no_gold_hit'])}")
    for x in res:
        x.pop("block", None)  # not for the report file
    print(f"empty blocks: {sum(x['empty'] for x in res)}  median latency: "
          f"{sorted(x['latency_s'] for x in res)[len(res) // 2]}s")
    for i, x in enumerate(res, 1):
        print(f"\n[{i:2d}] {'HIT ' if x['hit'] else 'MISS'} {x['source_type'][:4]} rank={x['rank']} "
              f"{x['latency_s']}s  {x['question']}\n     gt={x['source']}")
        for j, t in enumerate(x["top"], 1):
            print(f"     {j}. {t}")
    if a.out:
        json.dump({"flags": eff, "live_filled": sorted(filled), "summary": s, "results": res},
                  open(a.out, "w"), indent=1)
    return s


if __name__ == "__main__":
    main()
