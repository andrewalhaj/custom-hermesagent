#!/usr/bin/env python3
"""Auto-retrieval prototype — the per-turn RAG engine, standalone & measurable.

Calls knowledge.py's REAL hybrid search() (vector + BM25 RRF + MMR + graph),
NOT a reimplementation. Measures per-turn latency and tests the relevance floor
so off-topic queries can stay silent. NOT wired into the gateway — this is the
offline harness for the "should we wire per-turn auto-RAG?" decision.

KEY FINDINGS (2026-06-08, full writeup in references/auto-retrieval-architecture.md):
- Retrieval is CUE-DRIVEN by design; there is NO per-turn auto-injection hook.
  The gateway `agent:start` hook is fire-and-forget (return discarded) and
  context_prompt is assembled BEFORE it fires — so a clean hook CANNOT inject.
  True per-turn auto-RAG needs a gated CORE PATCH at the context-assembly seam.
- Latency is cheap (~90-240ms warm; first call ~2.2s = one-time model load).
- Relevance is the real cost: on-topic 0.86-0.88, but off-topic ("tell me a joke")
  still scores ~0.68 — the floor must be ~0.80 (high precision, low recall), and
  at a small corpus most turns aren't topical so naive injection is mostly noise.
- v1 BUG to avoid: passing a raw string to tbl.search() → degenerate constant
  0.50 scores. The real path is embed([query]) → tbl.search(qvec). DON'T
  reimplement search; import knowledge.py and call its search().

Re-run this after the corpus grows to re-measure precision/recall and re-tune the floor.
"""
import sys, os, time, importlib.util

HERMES = os.path.expanduser("~/.hermes")
spec = importlib.util.spec_from_file_location("knowledge", f"{HERMES}/scripts/knowledge.py")
K = importlib.util.module_from_spec(spec)
sys.modules["knowledge"] = K
spec.loader.exec_module(K)

RELEVANCE_FLOOR = 0.80   # ~0.80 needed for clean on/off-topic separation
TOP_K = 3

def retrieve(message):
    t0 = time.time()
    results = K.search(message, top_k=TOP_K)
    return results, time.time() - t0

if __name__ == "__main__":
    queries = sys.argv[1:] or [
        "what models does the swarm use",
        "how do I restart the gateway safely",
        "what's the weather like today",   # should stay silent at floor 0.80
        "honcho peer resolution pin",
        "tell me a joke",                   # should stay silent
    ]
    print(f"FLOOR={RELEVANCE_FLOOR} TOP_K={TOP_K}\n")
    lat = []
    for q in queries:
        results, elapsed = retrieve(q)
        lat.append(elapsed)
        print(f"Q: {q!r}  ({elapsed*1000:.0f}ms)")
        for r in (results or [])[:TOP_K]:
            if isinstance(r, dict):
                score = r.get("score", r.get("_relevance_score", "?"))
                txt = str(r.get("text", ""))[:100].replace("\n", " ")
                kept = "INJECT" if (isinstance(score, (int, float)) and score >= RELEVANCE_FLOOR) else "  drop"
                print(f"   [{kept} {score}] {txt}")
        print()
    print(f"=== latency avg={sum(lat)/len(lat)*1000:.0f}ms max={max(lat)*1000:.0f}ms ===")
