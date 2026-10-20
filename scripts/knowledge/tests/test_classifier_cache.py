"""Classifier cache: key includes the threshold; one sqlite connection per process."""
import importlib
import os
import sys

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

clf = importlib.import_module("knowledge.classifier")


def test_threshold_changes_cache_key():
    assert clf._cache_hash("abc") != clf._cache_hash("abc", 0.2)
    assert clf._cache_hash("abc", 0.2) != clf._cache_hash("abc", 0.3)
    assert clf._cache_hash("abc", None) == clf._cache_hash("abc")


def test_cache_connection_reused(monkeypatch, tmp_path):
    monkeypatch.setattr(clf, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(clf, "CACHE_DB", str(tmp_path / "c.db"))
    clf._reset_caches()
    assert clf._cache_conn() is clf._cache_conn()
    clf._cache_put("h", ["x"])
    assert clf._cache_get("h") == ["x"]
    clf._reset_caches()
