"""unified_bench scoring: rank = first result whose title/content holds a needle; MRR + Success@k.

Run:  python -m pytest scripts/knowledge/tests/test_unified_bench.py -v
"""
import json, os, sys

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from knowledge import unified_bench as ub


def test_run_scores_rank_mrr_and_misses():
    results = {"a": [{"content": "Port 8787 here"}],
               "b": [{"title": "noise"}, {"title": "x", "content": "edge-host"}],
               "c": [{"content": "nothing"}]}
    out = ub.run(lambda q, top_k: results[q], [("a", ["port 8787"]), ("b", ["edge-host"]), ("c", ["zz"])],
                 bootstrap_n=50)
    assert (out["n"], out["success@1"], out["MRR"]) == (3, 0.3333, 0.5)
    assert out["misses"] == ["c"]


def test_load_queries_skips_abstention_and_adds_factbench_direct(tmp_path):
    (tmp_path / "benchmark_queries.json").write_text(json.dumps(
        [{"query": "q1", "expected_substring": "Foo"}, {"query": "q2", "expect_no_hit": True}]))
    (tmp_path / "factbench.json").write_text(json.dumps(
        [{"needles": ["bar"], "direct": "d1", "indirect": "i1"}]))
    assert ub.load_queries(str(tmp_path)) == [("q1", ["foo"]), ("d1", ["bar"])]
