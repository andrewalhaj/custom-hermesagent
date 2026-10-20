"""Audit finding 2: one sector rule (search._sector_weight, top-down), applied once per hit.

Run:  python -m pytest scripts/knowledge/tests/test_unified_sector.py -v
"""
import os, sys

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import importlib

us = importlib.import_module("knowledge.unified_search")

PATH = ["infrastructure", "hosting", "deploy"]


def _run(monkeypatch, kb=(), skills=()):
    monkeypatch.setattr("knowledge.classifier.classify", lambda q: PATH)
    monkeypatch.setattr(us, "_search_knowledge", lambda q, top_k: [dict(h) for h in kb])
    monkeypatch.setattr(us, "_search_skills", lambda q, s, top_k: [dict(h) for h in skills])
    return us._unified_search_impl("q", stores=["knowledge", "skills"])


def test_knowledge_hit_not_boosted_again(monkeypatch):
    # search() already applied the sector boost; a second one stacked up to 13.7x.
    out = _run(monkeypatch, kb=[{"store": "knowledge", "title": "k", "score": 0.5, "sector_path": PATH}])
    assert out[0]["score"] == 0.5


def test_null_sector_path_does_not_empty_the_results(monkeypatch):
    # Rows stored with sector_path NULL used to raise in the merge and unified_search returned [].
    out = _run(monkeypatch, kb=[{"store": "knowledge", "title": "k", "score": 0.5, "sector_path": None}])
    assert [r["title"] for r in out] == ["k"]


def test_skill_boost_is_top_down(monkeypatch):
    # Same lower levels, different domain: no boost (the any-level rule gave 2.95x).
    out = _run(monkeypatch, skills=[{"store": "skill", "title": "s", "score": 0.2,
                                     "sector_path": ["development", "hosting", "deploy"]}])
    assert out[0]["score"] == 0.2
