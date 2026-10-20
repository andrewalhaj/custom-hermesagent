"""eval_refs: doc-level scoring of the reference search path (fixture: 3 tiny docs, 3 queries).

Run:  cd ~/.hermes/scripts && /usr/local/lib/hermes-agent/venv/bin/python3 -m pytest knowledge/tests/test_eval_refs.py -v
"""
import json, os, sys

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from knowledge import eval_refs as er

DOCS = ["alpha", "beta", "reports/gamma"]
ROWS = [
    {"query": "q-alpha", "depth": "deep", "synth": "single_hop"},
    {"query": "q-beta", "depth": "deep", "synth": "multi_hop_specific"},
    {"query": "q-gamma", "depth": "control", "synth": "single_hop"},
]
QRELS = {"q-alpha": {"alpha": 3}, "q-beta": {"beta": 3, "alpha": 1}, "q-gamma": {"reports/gamma": 3}}


def fake_search(query, top_k):
    """off arm: keyword-ish and wrong on deep queries; on arm (REF_CHUNKS=on): returns chunk hits (dup docs)."""
    on = os.environ.get("REF_CHUNKS") == "on"
    if on:
        want = {"q-alpha": "alpha", "q-beta": "beta", "q-gamma": "reports/gamma"}[query]
        return [{"id": want}, {"id": want}, {"id": "beta"}]  # duplicate chunk hits must collapse
    return {"q-alpha": [{"id": "beta"}, {"id": "gamma"}],
            "q-beta": [{"id": "gamma"}],
            "q-gamma": [{"id": "reports/gamma"}]}[query]


def test_dedup_docs_keeps_order_and_caps():
    hits = [{"id": "a"}, {"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert er.dedup_docs(hits) == ["a", "b", "c"]
    assert er.dedup_docs(hits, max_docs=2) == ["a", "b"]


def test_score_query_graded_ndcg_and_rank():
    s = er.score_query(["x", "alpha", "beta"], {"beta": 3, "alpha": 1})
    assert s["rank"] == 1 and s["rr"] == 0.5
    assert 0 < s["ndcg10"] < 1
    best = er.score_query(["beta", "alpha"], {"beta": 3, "alpha": 1})
    assert best["rank"] == 0 and abs(best["ndcg10"] - 1.0) < 1e-9
    miss = er.score_query(["x"], {"beta": 3})
    assert miss["rank"] == -1 and miss["rr"] == 0.0 and miss["ndcg10"] == 0.0


def test_run_arm_sets_env_and_restores(monkeypatch):
    monkeypatch.setenv("REF_CHUNKS", "log")
    res = er.run_arm(fake_search, ROWS, QRELS, "on")
    assert os.environ["REF_CHUNKS"] == "log"          # restored
    assert [r["rank"] for r in res] == [0, 0, 0]
    assert res[0]["docs"] == ["alpha", "beta"]          # dup chunks collapsed
    off = er.run_arm(fake_search, ROWS, QRELS, "off")
    assert [r["rank"] for r in off] == [-1, -1, 0]
    assert os.environ["REF_CHUNKS"] == "log"


def test_verdict_on_beats_off(monkeypatch):
    monkeypatch.delenv("REF_CHUNKS", raising=False)
    off = er.run_arm(fake_search, ROWS, QRELS, "off")
    on = er.run_arm(fake_search, ROWS, QRELS, "on")
    v = er.verdict(off, on)
    assert v["deep"]["delta_S@5"] == 1.0 and v["deep"]["ci"][0] > 0
    assert v["control_net_queries_lost"] == 0 and v["PASS"] is True
    # reversed: on-as-baseline loses both deep queries -> bar fails
    assert er.verdict(on, off)["PASS"] is False


def test_main_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("REF_CHUNKS", raising=False)
    (tmp_path / "benchmark_references.json").write_text(json.dumps(ROWS))
    (tmp_path / "qrels_references.json").write_text(json.dumps(QRELS))
    out_path = tmp_path / "res.json"
    out = er.main(["--arms", "off,on,on_prod", "--out", str(out_path), "--db-dir", str(tmp_path)],
                  search_fn=fake_search)
    assert out["n_queries"] == 3
    assert out["arms"]["off"]["deep"]["S@5"]["mean"] == 0.0
    assert out["arms"]["on"]["all"]["S@5"]["mean"] == 1.0
    assert out["verdict"]["on"]["PASS"] is True
    assert "synth:single_hop" in out["arms"]["on"]
    assert json.load(open(out_path))["n_queries"] == 3


def test_gt_corpus_split_and_refs_only_verdict(tmp_path, monkeypatch):
    monkeypatch.delenv("REF_CHUNKS", raising=False)
    off = er.run_arm(fake_search, ROWS, QRELS, "off")
    assert [r["gt_corpus"] for r in off] == ["references", "references", "reports"]
    on = er.run_arm(fake_search, ROWS, QRELS, "on")
    assert "gt:reports" in er.splits(on) and "gt:references/deep" in er.splits(on)
    v = er.verdict(off, on, lambda r: r["gt_corpus"] == "references")
    assert v["deep"]["n"] == 2 and v["control"]["n"] == 0
