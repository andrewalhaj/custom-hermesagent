#!/usr/bin/env python3
"""Quick retrieval benchmark over knowledge_db/benchmark_queries.json (P@5 = hit in top_k, MRR).
  kbench.py eval [--alert]   one run; --alert prints only on regression (cron-friendly)
  kbench.py sweep [k ...]    fixed top_k sweep with latency (default 3 5 8 13 21)
Retrieval flags come from the environment (RERANK=false kbench.py eval). knowledge.eval.run is the
rigorous benchmark (CIs, nDCG); this one is the fast smoke check. A failed run always exits 1.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from knowledge.eval import eval_benchmark  # noqa: E402

THRESHOLD = 0.75


def _run(top_k):
    r = eval_benchmark(top_k=top_k)
    if 'error' in r:
        sys.exit(f"kbench: {r['error']} ({r.get('path')})")
    return r


def main(argv):
    cmd, args = (argv[0] if argv else 'eval'), argv[1:]
    if cmd == 'eval':
        r = _run(8)
        if '--alert' in args:
            if r['p@5'] < THRESHOLD:
                print(f"⚠️ Knowledge P@5 regression: {r['p@5']:.2f} (threshold {THRESHOLD}), "
                      f"{r['passed']}/{r['total']} passed. Diagnose: python3 ~/.hermes/scripts/kbench.py eval")
            return
        print(f"P@5={r['p@5'] * 100:.1f}% MRR={r['MRR']} passed={r['passed']}/{r['total']}")
        for m in r['results']:
            if not m['found']:
                print(f"  MISS: {m['query'][:60]!r}")
    elif cmd == 'sweep':
        for k in [int(a) for a in args] or [3, 5, 8, 13, 21]:
            t = time.time()
            r = _run(k)
            print(f"k={k}  P@k={r['p@5'] * 100:5.1f}%  MRR={r['MRR']:.4f}  "
                  f"avg {(time.time() - t) / r['total']:.3f}s/query")
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
