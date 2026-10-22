"""Shadow store gate in session_heartbeat: logs verdicts, never changes the write path."""
import importlib
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
os.environ["HERMES_HOME"] = _ROOT  # heartbeat resolves its scripts dir from here
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest


@pytest.fixture(autouse=True)
def _withhold_on(monkeypatch):
    monkeypatch.setenv("JEV_WITHHOLD", "on")
    jev_mod = importlib.import_module("knowledge.jev")
    monkeypatch.setattr(jev_mod, "WITHHOLD", jev_mod.re.compile(r"10\.99\.|acme", jev_mod.re.I))  # tests describe the filter; the gateway env may have it off

hb = importlib.import_module("session_heartbeat")
store_gate = importlib.import_module("knowledge.store_gate")

FACTS = [("fact below threshold", "t1"), ("fact at threshold", "t2"), ("fact above threshold", "t3")]


@pytest.fixture(autouse=True)
def local_backend(monkeypatch):
    # the test shell inherits the gateway's STORE_GATE_BACKEND; these tests describe the local path unless they say otherwise
    monkeypatch.setattr(store_gate, "BACKEND", "local")
    monkeypatch.setattr(store_gate, "THRESHOLD", 0.802)


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


def test_score_round_trips_json_protocol_via_subprocess(monkeypatch, tmp_path):
    stub = tmp_path / "stub.sh"
    stub.write_text(f"#!/bin/sh\n{sys.executable} -c \"import sys,json; n=len(sys.stdin.read().splitlines()); print(json.dumps([0.25]*n))\"\n")
    stub.chmod(0o755)
    monkeypatch.setattr(store_gate, "SCORER_PYTHON", str(stub))
    assert store_gate.score(["a", 'b "quoted"\nline']) == [0.25, 0.25]


def test_score_raises_on_nonzero_exit(monkeypatch, tmp_path):
    stub = tmp_path / "stub.sh"
    stub.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    stub.chmod(0o755)
    monkeypatch.setattr(store_gate, "SCORER_PYTHON", str(stub))
    with pytest.raises(RuntimeError, match="exited 3.*boom"):
        store_gate.score(["a"])


def test_jev_backend_maps_noul_to_p_drop(monkeypatch):
    import io
    monkeypatch.setattr(store_gate, "BACKEND", "jev")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    sent = []
    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_urlopen(req, timeout):
        sent.append(json.loads(req.data))
        return Resp(json.dumps({"answers": {"store_gate": {"noul": 0.81}}}).encode())
    monkeypatch.setattr(importlib.import_module("knowledge.jev").urllib.request, "urlopen", fake_urlopen)
    assert store_gate.score(["a fact", "b fact"]) == pytest.approx([0.19, 0.19])
    assert [s["state"] for s in sent] == ["a fact", "b fact"] and sent[0]["model"] == importlib.import_module("knowledge.jev").MODEL
    assert "store_gate" in sent[0]["questions"]


def test_on_drops_above_threshold_and_logs(monkeypatch, env):
    stored, _, log = env
    monkeypatch.setenv("STORE_GATE", "on")
    monkeypatch.setattr(store_gate, "score", lambda texts: [0.1, 0.802, 0.99])
    hb.process_session(None, "s1", "cli", {})
    assert stored == ["fact below threshold"]  # the two at/above 0.802 never reach store()
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert [(r["verdict"], r["stored"], r["guard_rejected"]) for r in rows] == [
        ("keep", True, False), ("drop", False, False), ("drop", False, False)]
    assert rows[1]["fact"] == "fact at threshold"  # replayable by scripts/jev_restore.py


def test_on_fails_open_when_gate_errors(monkeypatch, env):
    stored, audits, log = env
    monkeypatch.setenv("STORE_GATE", "on")
    def boom(texts): raise RuntimeError("vendor down")
    monkeypatch.setattr(store_gate, "score", boom)
    hb.process_session(None, "s1", "cli", {})
    assert len(stored) == 3 and not log.exists() and "vendor down" in audits[0]


def test_jev_withholds_work_tenant_rows(monkeypatch):
    monkeypatch.setattr(store_gate, "BACKEND", "jev")
    jev = importlib.import_module("knowledge.jev")
    calls = []
    monkeypatch.setattr(jev, "ask", lambda state, q, timeout=0: (calls.append(state) or {"store_gate": {"noul": 0.2}}))
    assert store_gate.score(["ACME-LS01 is at 10.99.0.34", "plain fact"]) == pytest.approx([0.0, 0.8])
    assert calls == ["plain fact"]
