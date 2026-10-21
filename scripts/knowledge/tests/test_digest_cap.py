"""DIGEST_MAX_SLOTS (unified_search): cap digest-provenance hits in the top_k window."""
import importlib
import os
import sys

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

us_mod = importlib.import_module("knowledge.unified_search")


def _hit(store, name, score, prov="extracted"):
    return {"store": store, "id": name, "title": name, "score": score, "provenance": prov}


def _digests(n, start=1.0):
    return [_hit("knowledge", f"d{i}", start - i * 0.01, "digest") for i in range(n)]


def _facts(n, start):
    return [_hit("knowledge", f"f{i}", start - i * 0.01) for i in range(n)]


REFS = [_hit("reference", "r0", 0.5), _hit("reference", "r1", 0.4)]


def _run(monkeypatch, knowledge, refs=(), cap=None, reserved=None, top_k=5):
    monkeypatch.setattr(us_mod, "_search_knowledge", lambda q, top_k=5: list(knowledge))
    monkeypatch.setattr(us_mod, "_search_skills", lambda q, s, top_k=5: [])
    monkeypatch.setattr(us_mod, "_search_references", lambda q, s, top_k=5: list(refs))
    monkeypatch.setattr(us_mod, "_search_kanban", lambda q, top_k=5: [])
    for var, val in (("DIGEST_MAX_SLOTS", cap), ("REF_RESERVED_SLOTS", reserved)):
        if val is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, str(val))
    return [h["id"] for h in us_mod._unified_search_impl("q", top_k=top_k)]


MIXED = _digests(3) + _facts(4, 0.9)  # d0 d1 d2 f0 f1 f2 f3 in score order


def test_unset_empty_invalid_negative_are_identity(monkeypatch):
    base = _run(monkeypatch, MIXED)
    assert base == ["d0", "d1", "d2", "f0", "f1"]
    for bad in ("", "abc", "-1"):
        assert _run(monkeypatch, MIXED, cap=bad) == base


def test_cap_1_promotes_two_facts(monkeypatch):
    assert _run(monkeypatch, MIXED, cap=1) == ["d0", "f0", "f1", "f2", "f3"]


def test_cap_0_drops_all_digests_from_window(monkeypatch):
    assert _run(monkeypatch, _digests(2) + _facts(5, 0.9), cap=0) == ["f0", "f1", "f2", "f3", "f4"]


def test_not_enough_non_digests_falls_back_to_digests(monkeypatch):
    out = _run(monkeypatch, _digests(3) + _facts(3, 0.9), cap=0)
    assert out == ["d0", "d1", "f0", "f1", "f2"]  # only 3 facts exist: best digests fill the rest
    assert _run(monkeypatch, _digests(5), cap=0) == ["d0", "d1", "d2", "d3", "d4"]


def test_cap_at_or_above_digest_count_is_identity(monkeypatch):
    assert _run(monkeypatch, MIXED, cap=3) == _run(monkeypatch, MIXED)


def test_composes_with_ref_reserved_slots(monkeypatch):
    out = _run(monkeypatch, MIXED, REFS, cap=1, reserved=2)
    assert out == ["d0", "f0", "f1", "r0", "r1"]
    assert sum(i.startswith("d") for i in out) <= 1
