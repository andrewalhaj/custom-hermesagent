"""knowledge.eval holds both benchmarks: eval_benchmark (kbench's fast P@5/MRR) and run (v2: CIs, nDCG).

Run:  python -m pytest scripts/knowledge/tests/test_eval.py -v
"""
import json
import os
import sys

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from knowledge import eval as ev

QUERIES = [
    {"query": "gateway port", "expected_substring": "port 8787"},
    {"query": "dispatcher host", "expected_substring": "runs on edge-host"},
    {"query": "weather on mars", "expect_no_hit": True},
]
HITS = {
    "gateway port": [{"id": "aaaaaaaa", "text": "gateway listens on port 8787", "score": 1.0, "rerank_raw": 3.0}],
    "dispatcher host": [{"id": "x1234567", "text": "noise", "score": 1.0, "rerank_raw": 1.0},
                        {"id": "bbbbbbbb", "text": "dispatcher runs on EDGE-HOST", "score": 0.5, "rerank_raw": 0.5}],
    "weather on mars": [{"id": "cccccccc", "text": "unrelated", "score": 1.0, "rerank_raw": -4.0}],
}


def _setup(monkeypatch, tmp_path):
    (tmp_path / "benchmark_queries.json").write_text(json.dumps(QUERIES))
    monkeypatch.setattr(ev, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(ev, "search", lambda q, top_k: HITS[q])


def test_run_scores_mrr_and_abstention(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    out = ev.run(bootstrap_n=50)
    assert out["combined"]["n"] == 2
    assert out["combined"]["MRR"] == 0.75
    assert out["abstention"] == {"n": 1, "passed": 1, "details": out["abstention"]["details"]}


def test_eval_benchmark_reports_p5_and_mrr(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    r = ev.eval_benchmark(top_k=8)
    assert r["total"] == 3 and r["passed"] == 2
    assert r["MRR"] == 0.5  # (1 + 1/2 + 0) / 3 — kbench counts the abstention query as a miss, as before
