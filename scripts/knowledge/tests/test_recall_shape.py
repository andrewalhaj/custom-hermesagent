"""recall_shape.is_recall (shared with recall_gate) + RECALL_WIDE / digest cap in the supabase prefetch."""
import importlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from knowledge.recall_shape import is_recall

us_mod = importlib.import_module("knowledge.unified_search")


@pytest.mark.parametrize("text", [
    "Catch me up on the watchdog", "what did we decide about qwen?", "where were we", "remind me what we did",
    "what changed last night", "quick recap please",
])
def test_is_recall_positive(text):
    assert is_recall(text)


_BENCH_PATH = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "knowledge_db" / "benchmark_recall.json"
_BENCH = json.loads(_BENCH_PATH.read_text()) if _BENCH_PATH.exists() else []


@pytest.mark.parametrize("text", [q["question"] for q in _BENCH])
def test_is_recall_benchmark_questions(text):
    assert is_recall(text)


@pytest.mark.parametrize("text", [
    "add a retry to the embed call", "what is RLS", "how do I restart the gateway", "write a test for build_text",
    "what's the price of a Mac Studio", "explain hnsw", "ok proceed", "can you address this as well",
    "What is a watchdog timer?", "remind me to buy milk", "restart the gateway"])
def test_is_recall_negative(text):
    assert not is_recall(text)


def test_is_recall_override():
    assert not is_recall("catch me up, NO SESSION SEARCHES")
    assert not is_recall("recap, no lookup")


# ── prefetch ─────────────────────────────────────────────────────────────────

class _Base:
    pass


def _plugin():
    stub = MagicMock()
    stub.MemoryProvider = _Base
    path = Path(__file__).resolve().parents[3] / "plugins" / "supabase" / "__init__.py"
    with patch.dict(sys.modules, {"agent": MagicMock(), "agent.memory_provider": stub}):
        spec = importlib.util.spec_from_file_location("_rw_supabase_plugin", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def _hit(i, score, prov="extracted"):
    return {"id": i, "text": f"fact {i}", "score": score, "provenance": prov}


@pytest.fixture
def run(monkeypatch):
    """prefetch(query) with unified_search / action search mocked; returns (calls, output)."""
    for k in ("RECALL_WIDE", "SESSION_STORE", "DIGEST_MAX_SLOTS"):
        monkeypatch.delenv(k, raising=False)
    mod = _plugin()

    def go(query, main=(), action=()):
        calls = {}

        def fake_us(q, top_k=10, stores=None, exclude_session_id=''):
            calls.update(top_k=top_k, stores=stores)
            if exclude_session_id:
                calls["exclude_session_id"] = exclude_session_id
            return list(main)

        def fake_action(q, *prompt):
            calls["prompt"] = prompt[0] if prompt else mod._ACTION_PROMPT
            return list(action)

        monkeypatch.setattr(us_mod, "unified_search", fake_us)
        monkeypatch.setattr(mod, "_planned_action_hits", fake_action)
        return calls, mod.SupabaseMemoryProvider().prefetch(query)
    go.mod = mod
    return go


RECALL = "catch me up on the watchdog"


def test_flag_off_unchanged(run, monkeypatch):
    monkeypatch.setenv("SESSION_STORE", "true")
    calls, _ = run(RECALL, main=[_hit(1, .9)])
    assert calls == {"top_k": 5, "stores": None, "prompt": run.mod._ACTION_PROMPT}


def test_flag_on_recall_widens(run, monkeypatch):
    monkeypatch.setenv("RECALL_WIDE", "true")
    calls, _ = run(RECALL, main=[_hit(1, .9)])
    assert calls["top_k"] == 8
    assert calls["prompt"] == run.mod._RECALL_ACTION_PROMPT
    assert calls["stores"] is None  # SESSION_STORE off


def test_flag_on_recall_adds_sessions_store(run, monkeypatch):
    monkeypatch.setenv("RECALL_WIDE", "true")
    monkeypatch.setenv("SESSION_STORE", "true")
    calls, _ = run(RECALL, main=[_hit(1, .9)])
    assert calls["stores"] == ["knowledge", "skills", "references", "kanban", "sessions"]


def test_flag_on_non_recall_unchanged(run, monkeypatch):
    monkeypatch.setenv("RECALL_WIDE", "true")
    monkeypatch.setenv("SESSION_STORE", "true")
    calls, _ = run("restart the gateway", main=[_hit(1, .9)])
    assert calls == {"top_k": 5, "stores": None, "prompt": run.mod._ACTION_PROMPT}


def test_action_hits_capped_by_digest_max_slots(run, monkeypatch):
    monkeypatch.setenv("DIGEST_MAX_SLOTS", "1")
    _, out = run("q", main=[_hit(1, .9, "digest")],
                 action=[_hit(2, .8, "digest"), _hit(3, .7)])
    assert "fact 1" in out and "fact 3" in out and "fact 2" not in out


def test_action_hits_uncapped_when_unset(run):
    _, out = run("q", main=[_hit(1, .9, "digest")], action=[_hit(2, .8, "digest")])
    assert "fact 1" in out and "fact 2" in out


def test_action_hit_without_provenance_is_classified(run, monkeypatch):
    monkeypatch.setenv("DIGEST_MAX_SLOTS", "0")
    raw = {"id": 9, "text": "Session: x | 2026-10-01 summary", "score": .8}  # raw search() hit, digest by shape
    _, out = run("q", main=[_hit(1, .9)], action=[raw])
    assert "fact 1" in out and "Session: x" not in out


def test_recall_prompt_reaches_ollama(monkeypatch):
    mod = _plugin()
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent.update(json.loads(req.data))
        return io.BytesIO(b'{"response": ""}')

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("knowledge.config.env", lambda k, d="": "http://x")
    assert mod._planned_action_hits("hello", mod._RECALL_ACTION_PROMPT) == []
    assert sent["prompt"] == mod._RECALL_ACTION_PROMPT + "hello"


def test_session_hit_keeps_800_chars_knowledge_still_250(run):
    long = "x" * 700
    _, out = run("anything", main=[{"id": 1, "text": long, "score": .9, "provenance": "session"},
                                   {"id": 2, "text": "k" * 700, "score": .8, "provenance": "extracted"}])
    assert long in out and "k" * 250 in out and "k" * 251 not in out
