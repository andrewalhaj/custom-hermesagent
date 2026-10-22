"""recall_gate native guard: recall-shaped turns must run memory_search AND session_search before a reply passes.
Run: .venv/bin/python -m pytest plugins/local-guards/tests
"""
import sys

import pytest

from test_loader import _load_plugin

RECALL = "Catch me up, what did we decide about the watchdog?"
PLAIN = "What is a watchdog timer?"


@pytest.fixture
def rg(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setenv("HERMES_RECALL_GATE", "on")
    for name in [n for n in sys.modules if n.startswith("lg_under_test.guards")]:
        del sys.modules[name]
    _load_plugin()
    from lg_under_test.guards import recall_gate as mod
    return mod


def llm(rg, text=RECALL, session="s1", turn_id="t1", platform="cli"):
    return rg._on_pre_llm_call(session_id=session, turn_id=turn_id, user_message=text, platform=platform)


def tool(rg, name, session="s1", turn_id="t1"):
    return rg._on_pre_tool_call(tool_name=name, args={}, session_id=session, turn_id=turn_id)


def out(rg, session="s1", turn_id="t1"):
    return rg._on_transform_llm_output(response_text="answer", session_id=session, turn_id=turn_id)


def test_registers_hooks(rg):
    assert set(rg.HOOKS) == {"pre_llm_call", "pre_tool_call", "transform_llm_output"}


def test_off_by_default_and_when_flag_off(rg, monkeypatch):
    for setup in (lambda: monkeypatch.delenv("HERMES_RECALL_GATE"), lambda: monkeypatch.setenv("HERMES_RECALL_GATE", "off")):
        setup()
        assert llm(rg) is None and tool(rg, "memory_search") is None and out(rg) is None


@pytest.mark.parametrize("text", [PLAIN, "What is left padding in CSS?", "what's left margin", "Remind me to rotate the key", "anything else?"])
def test_non_recall_message_is_untouched(rg, text):
    assert llm(rg, text) is None and out(rg) is None


@pytest.mark.parametrize("text", ["what changed?", "Where were we", "what's left on the list", "what about the nginx fix from last week",
                                  "Recap please", "remind me what we did", "anything else left?", "what did we do last night"])
def test_recall_shapes_arm_the_gate(rg, text):
    assert llm(rg, text).startswith("[recall-gate] This is a recall question")


def test_injects_until_both_searches_then_stops(rg):
    assert llm(rg)
    tool(rg, "read_file")
    assert llm(rg)  # after a non-search tool call
    tool(rg, "memory_search")
    assert llm(rg)  # one is not enough
    tool(rg, "session_search")
    assert llm(rg) is None and out(rg) is None


def test_chat_history_lookup_via_deferred_tool_call_wrapper(rg):
    assert llm(rg)
    tool(rg, "memory_search")
    rg._on_pre_tool_call(tool_name="tool_call", args={"calls": [{"name": "chat_history_lookup", "arguments": {}}]},
                         session_id="s1", turn_id="t1")
    assert llm(rg) is None and out(rg) is None


def test_chat_history_lookup_counts_as_session_search(rg):
    llm(rg)
    tool(rg, "mcp__x__memory_search")
    tool(rg, "chat_history_lookup")
    assert out(rg) is None


@pytest.mark.parametrize("names, m, s", [(["memory_search"], "yes", "no"), (["session_search"], "no", "yes"), ([], "no", "no")])
def test_partial_searches_withhold_reply(rg, names, m, s, capsys):
    llm(rg)
    for n in names:
        tool(rg, n)
    assert out(rg) == (f"[recall-gate] Reply withheld: memory_search ran = {m}, session_search ran = {s}. "
                       "Ask again and I will look it up first.")
    assert "withheld" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["on", "log"])
@pytest.mark.parametrize("text", ["[ASYNC DELEGATION complete] what changed", "[IMPORTANT: Background process done] catch me up",
                                  "[CONTEXT COMPACTION] where were we"])
def test_bracketed_notifications_never_arm(rg, monkeypatch, mode, text):
    monkeypatch.setenv("HERMES_RECALL_GATE", mode)
    assert llm(rg, text) is None and out(rg) is None


def test_log_mode_records_but_never_blocks(rg, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_RECALL_GATE", "log")
    assert llm(rg) is None  # no reminder
    assert out(rg) is None  # no withheld notice
    err = capsys.readouterr().err
    assert "withheld reply" in err and "mode=log" in err


def test_true_mode_keeps_blocking(rg, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_RECALL_GATE", "true")
    assert llm(rg) and out(rg).startswith("[recall-gate] Reply withheld")
    assert "mode=log" not in capsys.readouterr().err


def test_override_phrase_disarms(rg):
    assert llm(rg, RECALL + " NO SESSION SEARCHES") is None and out(rg) is None
    assert llm(rg, "recap, no lookup", turn_id="t2") is None and out(rg, turn_id="t2") is None


def test_new_turn_resets_state(rg):
    llm(rg)
    tool(rg, "memory_search")
    tool(rg, "session_search")
    assert llm(rg, turn_id="t2")  # t1 satisfied, t2 starts fresh
    assert out(rg, turn_id="t2").startswith("[recall-gate] Reply withheld")


def test_follows_compression_session_id(rg):
    llm(rg)
    tool(rg, "memory_search", session="s2")  # rotated id, same turn
    tool(rg, "session_search", session="s2")
    assert out(rg, session="s2") is None


def test_worker_and_subagent_skip(rg, monkeypatch):
    assert llm(rg, platform="subagent") is None and out(rg) is None
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    assert llm(rg, turn_id="t9") is None and out(rg, turn_id="t9") is None


def test_state_is_capped(rg):
    for i in range(rg._MAX_SESSIONS + 20):
        llm(rg, session=f"s{i}", turn_id=f"t{i}")
    assert len(rg._sessions) <= rg._MAX_SESSIONS


def test_import_failure_fails_open(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "knowledge.recall_shape", None)  # makes the import raise ImportError
    for name in [n for n in sys.modules if n.startswith("lg_under_test.guards")]:
        del sys.modules[name]
    _load_plugin()
    from lg_under_test.guards import recall_gate as mod
    assert mod._is_recall(RECALL) is False
    assert "gate disabled" in capsys.readouterr().err
