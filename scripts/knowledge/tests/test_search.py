"""search.py scoring paths: rerank falls back remote → cosine (no local cross-encoder),
and one timestamp parser feeds both recency and temporal decay.

Run:  python -m pytest scripts/knowledge/tests/test_search.py -v
"""
import importlib
import pytest
import os
import sys
import types

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

search_mod = importlib.import_module("knowledge.search")


@pytest.fixture(autouse=True)
def _withhold_on(monkeypatch):
    monkeypatch.setenv("JEV_WITHHOLD", "on")
    jev_mod = importlib.import_module("knowledge.jev")
    monkeypatch.setattr(jev_mod, "WITHHOLD", jev_mod.re.compile(r"10\.99\.|acme", jev_mod.re.I))  # the shell inherits the gateway env, which has it off


def test_rerank_falls_back_to_cosine_when_remote_down(monkeypatch):
    # A local CrossEncoder that would invert the order must never be consulted.
    fake_st = types.ModuleType("sentence_transformers")

    class _CE:
        def __init__(self, *a, **k):
            pass

        def predict(self, pairs):
            import numpy as np
            return np.array([1.0 if 'beta' in t else 0.0 for _, t in pairs])

    fake_st.CrossEncoder = _CE
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    def down(*a, **k):
        raise OSError("studio unreachable")

    monkeypatch.setattr(search_mod, "_remote_rerank", down)
    vecs = {"alpha": [1.0, 0.0], "alpha fact": [1.0, 0.0], "beta fact": [0.0, 1.0]}
    monkeypatch.setattr(search_mod, "embed", lambda texts: [vecs[t] for t in texts])
    hits = [{"id": "b", "text": "beta fact", "score": 0.9},
            {"id": "a", "text": "alpha fact", "score": 0.8}]
    out = search_mod._cosine_rerank(hits, "alpha", top_k=2)
    assert [h["id"] for h in out] == ["a", "b"]


def test_age_days_parses_iso_epoch_and_junk():
    now = 1_000_000.0
    assert search_mod._age_days(now - 86400, now) == 1.0
    assert abs(search_mod._age_days("1970-01-12T13:46:40Z", now) - 0.0) < 1e-6
    assert search_mod._age_days("not a date", now) is None
    assert search_mod._age_days(None, now) is None


def test_jev_rerank_orders_by_noul_and_falls_back(monkeypatch):
    import importlib, math
    jev = importlib.import_module("knowledge.jev")
    monkeypatch.setattr(search_mod, "env", lambda k, default="": "jev" if k == "RERANK_BACKEND" else default)
    hits = [{"id": "a", "text": "alpha fact", "score": 0.1}, {"id": "b", "text": "beta fact", "score": 0.9}]
    monkeypatch.setattr(jev, "ask", lambda state, q, timeout=5: {"c0": {"noul": 0.9}, "c1": {"noul": 0.2}})
    bge_called = []
    monkeypatch.setattr(search_mod, "_remote_rerank", lambda h, q, k: bge_called.append(q) or h[:k])
    out = search_mod._cosine_rerank([dict(h) for h in hits], "alpha", top_k=2)
    assert [h["id"] for h in out] == ["a", "b"] and out[0]["score"] == 1.0 and out[1]["score"] == 0.0
    assert out[0]["rerank_raw"] > 0 > out[1]["rerank_raw"] and bge_called == []
    # work-tenant text anywhere in the request: never sent, bge ranks it
    sent = []
    monkeypatch.setattr(jev, "ask", lambda state, q, timeout=5: sent.append(state) or {})
    search_mod._cosine_rerank([dict(h) for h in hits] + [{"id": "c", "text": "ACME-LS01 at 10.99.0.34", "score": 0.5}], "alpha", top_k=2)
    assert sent == [] and bge_called == ["alpha"]
    # vendor failure: bge
    def boom(*a, **k): raise RuntimeError("vendor down")
    monkeypatch.setattr(jev, "ask", boom)
    search_mod._cosine_rerank([dict(h) for h in hits], "beta", top_k=2)
    assert bge_called == ["alpha", "beta"]
    # backend unset: jev never consulted
    monkeypatch.setattr(search_mod, "env", lambda k, default="": default)
    monkeypatch.setattr(jev, "ask", lambda *a, **k: pytest.fail("jev asked with backend unset"))
    search_mod._cosine_rerank([dict(h) for h in hits], "gamma", top_k=2)
    assert bge_called[-1] == "gamma"
