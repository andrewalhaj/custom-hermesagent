"""search.py scoring paths: rerank falls back remote → cosine (no local cross-encoder),
and one timestamp parser feeds both recency and temporal decay.

Run:  python -m pytest scripts/knowledge/tests/test_search.py -v
"""
import importlib
import os
import sys
import types

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

search_mod = importlib.import_module("knowledge.search")


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
