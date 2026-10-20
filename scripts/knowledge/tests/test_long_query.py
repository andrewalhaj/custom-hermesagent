"""Long user messages (>200 chars) must still get memory recall.

The plugin's prefetch passes the raw user message to search(); the query-sector
classifier refuses >200 chars, which used to make search() return [] silently.

Run:  python -m pytest scripts/knowledge/tests/test_long_query.py -v
"""
import importlib
import os
import sys

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

search_mod = importlib.import_module("knowledge.search")
classifier_mod = importlib.import_module("knowledge.classifier")

LONG = "Why does the dispatcher deploy keep failing after the docker restart? " * 8


def test_search_returns_hits_for_long_query(monkeypatch):
    hit = {"id": "1", "text": "dispatcher deploy fact", "score": 0.9, "sector_path": []}
    seen = []
    monkeypatch.setattr(classifier_mod, "classify", lambda q: seen.append(q) or [])
    monkeypatch.setattr(search_mod, "_single_search", lambda *a, **k: [dict(hit)])
    assert len(LONG) > 200
    out = search_mod.search(LONG, top_k=5)
    assert [h["id"] for h in out] == ["1"]
    assert seen and all(len(q) <= 200 for q in seen)


def test_sector_guard_still_refuses_long_text():
    # The guard protects fact text from keyword classification; it must stay.
    try:
        search_mod._detect_query_sector(LONG)
    except ValueError:
        return
    raise AssertionError("_detect_query_sector accepted >200 chars")
