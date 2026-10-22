"""Supersede judge (storage._knn_gate 'judged') and distill selector: flag off = old behaviour, failures fail open."""
import importlib, os, sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import pytest


@pytest.fixture(autouse=True)
def _withhold_on(monkeypatch):
    monkeypatch.setenv("JEV_WITHHOLD", "on")
    jev_mod = importlib.import_module("knowledge.jev")
    monkeypatch.setattr(jev_mod, "WITHHOLD", jev_mod.re.compile(r"10\.99\.|acme", jev_mod.re.I))  # tests describe the filter; the gateway env may have it off
storage = importlib.import_module("knowledge.storage")
jev = importlib.import_module("knowledge.jev")
distill = importlib.import_module("session_distill")


def _wire_knn(monkeypatch, cos):
    monkeypatch.setattr(storage, "_coldstore_log", lambda *a, **k: None)  # keep test verdicts out of the live audit log
    monkeypatch.setattr(storage, "_supa_select", lambda q: ([] if "body_hash" in q else [{"id": "old1", "vector": [1.0, 0.0], "text": "port is 8080"}]))
    monkeypatch.setattr(storage, "_supa_post", lambda *a, **k: [{"id": "old1"}])
    monkeypatch.setattr(storage, "_cosine", lambda a, b: cos)


def test_judge_fires_only_in_band_with_flag(monkeypatch):
    _wire_knn(monkeypatch, 0.85)
    monkeypatch.setattr(jev, "ask", lambda state, q, timeout=20: {"same_subject": {"noul": 0.95}, "supersedes": {"noul": 0.9}})
    monkeypatch.delenv("SUPERSEDE_JUDGE", raising=False)
    assert storage._knn_gate("port is 8081", [1.0, 0.0]) == (None, None)
    monkeypatch.setenv("SUPERSEDE_JUDGE", "true")
    assert storage._knn_gate("port is 8081", [1.0, 0.0]) == ("judged", "old1")
    monkeypatch.setattr(jev, "ask", lambda state, q, timeout=20: {"same_subject": {"noul": 0.95}, "supersedes": {"noul": 0.3}})
    assert storage._knn_gate("port is 8081", [1.0, 0.0]) == (None, None)


def test_judge_fails_closed_and_withholds(monkeypatch):
    _wire_knn(monkeypatch, 0.85); monkeypatch.setenv("SUPERSEDE_JUDGE", "true")
    def boom(*a, **k): raise RuntimeError("vendor down")
    monkeypatch.setattr(jev, "ask", boom)
    assert storage._knn_gate("port is 8081", [1.0, 0.0]) == (None, None)
    monkeypatch.setattr(jev, "ask", lambda *a, **k: {"same_subject": {"noul": 0.99}, "supersedes": {"noul": 0.99}})
    assert storage._knn_gate("ACME-LS01 moved to 10.99.0.35", [1.0, 0.0]) == (None, None)


def test_cosine_dedup_still_wins_above_line(monkeypatch):
    _wire_knn(monkeypatch, 0.95); monkeypatch.setenv("SUPERSEDE_JUDGE", "true")
    monkeypatch.setattr(jev, "ask", lambda *a, **k: pytest.fail("judge must not be asked above the cosine line"))
    assert storage._knn_gate("port is 8081", [1.0, 0.0]) == ("near", "old1")


def test_distill_selector_ranks_and_filters(monkeypatch):
    monkeypatch.setenv("DISTILL_JUDGE", "true")
    sent = {}
    def fake_ask(state, questions, timeout=60):
        sent.update(questions)
        return {q: {"noul": 0.9 if "greenlit" in str(questions[q]["instructions"]) else 0.2} for q in questions}
    monkeypatch.setattr(jev, "ask", fake_ask)
    dec, out, skip, dirs = distill._select_items("t", "o", {"Complete", "Both greenlit.", "10.99.0.34 is ACME-LS01"}, set(), set(), {"Fix it"})
    assert dec == ["Both greenlit.", "10.99.0.34 is ACME-LS01"]  # withheld item keeps neutral 0.5 and is never sent
    assert not any("10.99" in str(q["instructions"]) for q in sent.values())
    assert out == [] and skip == [] and dirs == []
    monkeypatch.delenv("DISTILL_JUDGE")
    assert distill._select_items("t", "o", {"b", "a"}, set(), set(), set())[0] == ["a", "b"]
    monkeypatch.setenv("DISTILL_JUDGE", "true")
    def boom(*a, **k): raise RuntimeError("vendor down")
    monkeypatch.setattr(jev, "ask", boom)
    assert distill._select_items("t", "o", {"b", "a"}, set(), set(), set())[0] == ["a", "b"]
