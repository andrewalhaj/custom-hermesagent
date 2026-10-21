"""Shadow store gate in session_heartbeat: logs verdicts, never changes the write path."""
import importlib
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
os.environ["HERMES_HOME"] = _ROOT  # heartbeat resolves its scripts dir from here
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest

hb = importlib.import_module("session_heartbeat")
store_gate = importlib.import_module("knowledge.store_gate")

FACTS = [("fact below threshold", "t1"), ("fact at threshold", "t2"), ("fact above threshold", "t3")]


@pytest.fixture
def env(monkeypatch, tmp_path):
    stored, audits = [], []
    monkeypatch.setattr(hb, "SHADOW_LOG", str(tmp_path / "logs" / "shadow.jsonl"))
    monkeypatch.setattr(hb, "get_new_messages", lambda *a: [{"id": 7, "role": "assistant", "content": "x"}])
    monkeypatch.setattr(hb, "extract_facts", lambda c: FACTS)
    monkeypatch.setattr(hb, "MIN_FACT_LENGTH", 1)
    monkeypatch.setattr(hb, "is_duplicate", lambda f: False)
    monkeypatch.setattr(hb, "store", lambda fact, **kw: stored.append(fact))
    monkeypatch.setattr(hb, "audit", audits.append)
    return stored, audits, tmp_path / "logs" / "shadow.jsonl"


def test_logs_verdicts_and_still_stores(monkeypatch, env):
    stored, _, log = env
    monkeypatch.setenv("STORE_GATE", "log")
    monkeypatch.setattr(store_gate, "score", lambda texts: [0.801, 0.802, 0.99])
    hb.process_session(None, "s1", "cli", {})
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert [r["verdict"] for r in rows] == ["keep", "drop", "drop"]
    assert rows[2] == {**rows[2], "session_id": "s1", "message_id": 7, "tag": "t3",
                       "fact": "fact above threshold", "p_drop": 0.99, "stored": True, "guard_rejected": False}
    assert "ts" in rows[0]
    assert stored == [f for f, _ in FACTS]  # drop verdicts are still stored


def test_guard_rejection_is_logged(monkeypatch, env):
    _, _, log = env
    monkeypatch.setenv("STORE_GATE", "log")
    monkeypatch.setattr(store_gate, "score", lambda texts: [0.1, 0.1, 0.1])
    def guard(fact, **kw): raise ValueError("[cold-store-guard] no")
    monkeypatch.setattr(hb, "store", guard)
    hb.process_session(None, "s1", "cli", {})
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert all(r["stored"] is False and r["guard_rejected"] is True for r in rows)


def test_env_unset_never_scores(monkeypatch, env):
    stored, _, log = env
    monkeypatch.delenv("STORE_GATE", raising=False)
    def boom(texts): raise AssertionError("score called")
    monkeypatch.setattr(store_gate, "score", boom)
    hb.process_session(None, "s1", "cli", {})
    assert len(stored) == 3 and not log.exists()


def test_score_failure_audits_and_stores(monkeypatch, env):
    stored, audits, log = env
    monkeypatch.setenv("STORE_GATE", "log")
    def boom(texts): raise RuntimeError("model gone")
    monkeypatch.setattr(store_gate, "score", boom)
    hb.process_session(None, "s1", "cli", {})
    assert len(stored) == 3 and not log.exists()
    assert len(audits) == 1 and "model gone" in audits[0]
