"""RG1: REF_RESERVED_SLOTS (unified_search) + SUPABASE_PREFETCH_TIMEOUT_S (supabase plugin)."""
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

us_mod = importlib.import_module("knowledge.unified_search")


def _hit(store, i, score):
    return {"store": store, "id": f"{store}{i}", "title": f"{store}-{i}", "score": score}


def _run(monkeypatch, knowledge, refs, top_k=5, n=None):
    monkeypatch.setattr(us_mod, "_search_knowledge", lambda q, top_k=5: list(knowledge))
    monkeypatch.setattr(us_mod, "_search_skills", lambda q, s, top_k=5: [])
    monkeypatch.setattr(us_mod, "_search_references", lambda q, s, top_k=5: list(refs))
    monkeypatch.setattr(us_mod, "_search_kanban", lambda q, top_k=5: [])
    if n is None:
        monkeypatch.delenv("REF_RESERVED_SLOTS", raising=False)
    else:
        monkeypatch.setenv("REF_RESERVED_SLOTS", str(n))
    return us_mod._unified_search_impl("q", top_k=top_k)


KNOW = [_hit("knowledge", i, 1.0 - i * 0.02) for i in range(8)]  # 1.00 .. 0.86
REFS = [_hit("reference", 0, 0.5), _hit("reference", 1, 0.4)]


def test_zero_is_identity(monkeypatch):
    base = _run(monkeypatch, KNOW, REFS)
    assert _run(monkeypatch, KNOW, REFS, n=0) == base
    assert not any(h["store"] == "reference" for h in base)


def test_promotes_refs_over_lowest_non_refs(monkeypatch):
    out = _run(monkeypatch, KNOW, REFS, n=2)
    assert len(out) == 5
    assert [h["id"] for h in out] == ["knowledge0", "knowledge1", "knowledge2", "reference0", "reference1"]
    assert [h["score"] for h in out] == sorted((h["score"] for h in out), reverse=True)


def test_fewer_refs_than_n_promotes_only_existing(monkeypatch):
    out = _run(monkeypatch, KNOW, REFS[:1], n=3)
    assert len(out) == 5
    assert [h["id"] for h in out].count("reference0") == 1
    assert sum(h["store"] == "reference" for h in out) == 1


def test_no_refs_unchanged(monkeypatch):
    assert _run(monkeypatch, KNOW, [], n=2) == _run(monkeypatch, KNOW, [])


def test_refs_already_in_window_not_displaced(monkeypatch):
    refs = [_hit("reference", 0, 0.99)]
    out = _run(monkeypatch, KNOW, refs, n=1)
    assert out == _run(monkeypatch, KNOW, refs)
    assert len(out) == 5


def test_n_clamped_to_top_k(monkeypatch):
    out = _run(monkeypatch, KNOW, REFS, top_k=2, n=99)
    assert [h["id"] for h in out] == ["reference0", "reference1"]


# ── prefetch timeout ─────────────────────────────────────────────────────────

class _Base:
    pass


def _load_supabase_plugin():
    stub = MagicMock()
    stub.MemoryProvider = _Base
    path = Path(__file__).resolve().parents[3] / "plugins" / "supabase" / "__init__.py"
    with patch.dict(sys.modules, {"agent": MagicMock(), "agent.memory_provider": stub}):
        spec = importlib.util.spec_from_file_location("_rg1_supabase_plugin", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("val,expected", [
    (None, 3.0), ("6", 6.0), ("20", 7.5), ("0.2", 1.0), ("garbage", 3.0),
])
def test_prefetch_timeout_env(monkeypatch, val, expected):
    mod = _load_supabase_plugin()
    if val is None:
        monkeypatch.delenv("SUPABASE_PREFETCH_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("SUPABASE_PREFETCH_TIMEOUT_S", val)
    assert mod._prefetch_timeout_s() == expected
