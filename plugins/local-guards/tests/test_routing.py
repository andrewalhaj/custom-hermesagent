"""routing native guard: one advisory nudge: on the first non-routing tool result of a turn whose user
message is multi-part / multi-phase, and every WRITE_STEP inline writes without routing.
Replaces the kanban_checkpoint / kanban_phase / delegation_nudge / task_routing / delegation_checkpoint patches.
Run: .venv/bin/python -m pytest plugins/local-guards/tests
"""
import sys

import pytest

from test_loader import _load_plugin

MULTI = "Fix the login redirect bug. Also update the README and check the nginx config."
PLAIN = "What time does the backup cron run tonight?"


@pytest.fixture
def rt(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_ROUTING", raising=False)
    for name in [n for n in sys.modules if n.startswith("lg_under_test.guards")]:
        del sys.modules[name]
    _load_plugin()
    from lg_under_test.guards import routing as mod
    return mod


def turn(rt, text=MULTI, session="s1", turn_id="t1", platform="cli"):
    return rt._on_pre_llm_call(session_id=session, turn_id=turn_id, user_message=text, platform=platform)


def result(rt, tool="read_file", session="s1", turn_id="t1", out="ok", **args):
    return rt._on_transform_tool_result(tool_name=tool, args=args, result=out, session_id=session, turn_id=turn_id)


def test_registers_both_hooks(rt):
    assert set(rt.HOOKS) == {"pre_llm_call", "transform_tool_result"}


def test_multipart_message_nudges_first_tool_result_once(rt):
    assert turn(rt) is None  # never injects into the prompt itself
    first = result(rt)
    assert first.startswith("ok\n\n[Routing:") and "multi-part" in first
    assert result(rt) is None  # once per message


@pytest.mark.parametrize("text, why", [
    ("Plan to migrate the dashboard and fix the auth flow", '"plan to"'),
    ("Read https://a.example/x and https://b.example/y for me", "URLs"),
    ("Do a full overhaul of the gateway logging setup please", "multi-phase"),
    ("Stop the worker, then drain the queue, then bring it back up", "multi-phase"),
])
def test_other_signals_nudge(rt, text, why):
    turn(rt, text)
    assert why in result(rt)


@pytest.mark.parametrize("text", [
    PLAIN,
    MULTI + " Just do it inline.",
    "[ASYNC DELEGATION result] 1. fixed x\n2. fixed y\n3. also z?",
])
def test_quiet_messages_do_not_nudge(rt, text):
    turn(rt, text)
    assert result(rt) is None


def test_routing_call_suppresses_the_turn(rt):
    turn(rt)
    assert result(rt, tool="delegate_task") is None
    assert result(rt) is None


def test_terminal_kanban_create_counts_as_routing(rt):
    turn(rt)
    assert result(rt, tool="terminal", command="hermes kanban create --title x") is None
    assert result(rt) is None


def test_worker_subagent_and_off_switch_are_silent(rt, monkeypatch):
    turn(rt, platform="subagent")
    assert result(rt) is None
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_123")
    turn(rt, session="s2")
    assert result(rt, session="s2") is None
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    monkeypatch.setenv("HERMES_ROUTING", "off")
    turn(rt, session="s3")
    assert result(rt, session="s3") is None


def test_inline_writes_nudge_every_step_until_routed(rt):
    turn(rt, PLAIN)
    for _ in range(rt.WRITE_STEP - 1):
        assert result(rt, tool="patch") is None
    assert "since the last delegate/kanban action" in result(rt, tool="patch")
    turn(rt, PLAIN, turn_id="t2")  # counter restarts after a nudge
    for _ in range(rt.WRITE_STEP - 1):
        assert result(rt, tool="write_file", turn_id="t2") is None
    result(rt, tool="delegate_task", turn_id="t2")  # routing resets the counter
    assert result(rt, tool="write_file", turn_id="t2") is None


def test_terminal_grind_nudges(rt):
    turn(rt, PLAIN)
    outs = [result(rt, tool="terminal", command="ls") for _ in range(rt.TERMINAL_STEP)]
    assert outs[-1] and all(o is None for o in outs[:-1])


def test_state_follows_compression_session_rotation(rt):
    turn(rt)
    assert "multi-part" in result(rt, session="s1-compressed")  # same turn_id, new session id
    turn(rt, turn_id="t2", session="s1-compressed")
    assert result(rt, session="s1-compressed", turn_id="t2")


def test_unknown_session_and_non_str_results_are_untouched(rt):
    assert result(rt, session="never-seen", turn_id="x") is None
    turn(rt)
    assert result(rt, out={"not": "a str"}) is None


def test_errors_fail_open(rt, monkeypatch):
    monkeypatch.setattr(rt, "_signal", lambda text: 1 / 0)
    assert turn(rt) is None
    assert result(rt) is None
