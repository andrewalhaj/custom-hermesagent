"""Regression tests: knowledge search must never raise or return None when an
internal layer fails (t_c67fa2c0 — memory_search 'NoneType' object is not iterable).

Run:  python -m pytest scripts/knowledge/tests/test_none_guards.py -v
  or: python scripts/knowledge/tests/test_none_guards.py   (standalone, no pytest)
"""
import os
import sys

# Make `import knowledge.*` work regardless of cwd.
_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import importlib

from knowledge.search import search
search_mod = importlib.import_module("knowledge.search")
usearch_mod = importlib.import_module("knowledge.unified_search")


# ── search() hardening ────────────────────────────────────────────────

def test_single_search_returns_none_does_not_crash(monkeypatch):
    """If _single_search leaks None (RPC failure under load), search() -> []."""
    monkeypatch.setattr(search_mod, "_single_search", lambda *a, **k: None)
    out = search("anything at all", top_k=5)
    assert out == [] or isinstance(out, list)
    assert out is not None


def test_cosine_rerank_empty_and_none_hits():
    """_cosine_rerank tolerates [] and None input."""
    assert search_mod._cosine_rerank([], "q", top_k=5) == []
    assert search_mod._cosine_rerank(None, "q", top_k=5) == []


def test_rrf_merge_tolerates_none():
    """_rrf_merge must not iterate None."""
    assert search_mod._rrf_merge(None) == []
    assert search_mod._rrf_merge([None]) == []
    merged = search_mod._rrf_merge([[{"id": "1", "text": "x", "score": 0.5}]])
    assert isinstance(merged, list) and len(merged) == 1


def test_search_top_level_never_raises(monkeypatch):
    """Any internal explosion -> [] rather than propagating."""
    def _boom(*a, **k):
        raise RuntimeError("simulated pipeline failure")
    monkeypatch.setattr(search_mod, "_search_impl", _boom)
    out = search("query that triggers failure")
    assert out == []


# ── unified_search() hardening ────────────────────────────────────────

def test_unified_search_knowledge_returns_none(monkeypatch):
    """_search_knowledge leaking None must not break the merge/dedup path."""
    monkeypatch.setattr(usearch_mod, "_search_knowledge", lambda *a, **k: None)
    monkeypatch.setattr(usearch_mod, "_search_skills", lambda *a, **k: [])
    monkeypatch.setattr(usearch_mod, "_search_references", lambda *a, **k: [])
    monkeypatch.setattr(usearch_mod, "_search_kanban", lambda *a, **k: [])
    out = usearch_mod.unified_search("anything", top_k=5)
    assert isinstance(out, list)
    assert out is not None


def test_unified_search_missing_keys(monkeypatch):
    """Rows missing title/score keys must not crash the dedup/sort path."""
    monkeypatch.setattr(
        usearch_mod, "_search_knowledge",
        lambda *a, **k: [{"store": "knowledge"}, {"store": "knowledge", "title": "x"}],
    )
    monkeypatch.setattr(usearch_mod, "_search_skills", lambda *a, **k: [])
    monkeypatch.setattr(usearch_mod, "_search_references", lambda *a, **k: [])
    monkeypatch.setattr(usearch_mod, "_search_kanban", lambda *a, **k: [])
    out = usearch_mod.unified_search("anything", top_k=5)
    assert isinstance(out, list)


def test_unified_search_top_level_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("simulated unified failure")
    monkeypatch.setattr(usearch_mod, "_unified_search_impl", _boom)
    assert usearch_mod.unified_search("q") == []


# ── standalone runner (no pytest required) ────────────────────────────

if __name__ == "__main__":
    class _MP:
        """Minimal monkeypatch shim so the file runs without pytest."""
        def __init__(self):
            self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)
            self._undo.clear()

    import inspect
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        mp = _MP()
        try:
            if "monkeypatch" in inspect.signature(fn).parameters:
                fn(mp)
            else:
                fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1
        finally:
            mp.undo()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
