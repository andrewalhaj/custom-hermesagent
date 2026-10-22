import pytest
from test_loader import _load_plugin

FACT = "What's the latest Hyper-V Server version and does it support nested virtualization?"
CHAT = "ok go ahead with that"


@pytest.fixture
def fg(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_FACT_GATE", "on")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    _load_plugin()
    from lg_under_test.guards import fact_gate as mod
    monkeypatch.setattr(mod, "_LOG", str(tmp_path / "fg.jsonl"))
    mod._sessions.clear()
    return mod


def llm(fg, text=FACT, session="s1", turn_id="t1", platform="cli"):
    return fg.HOOKS["pre_llm_call"](session_id=session, turn_id=turn_id, user_message=text, platform=platform)


def tool(fg, name, session="s1", turn_id="t1"):
    return fg.HOOKS["pre_tool_call"](tool_name=name, args={}, session_id=session, turn_id=turn_id)


def out(fg, session="s1", turn_id="t1"):
    return fg.HOOKS["transform_llm_output"](session_id=session, turn_id=turn_id)


@pytest.mark.parametrize("q,armed", [
    (FACT, True),
    ("How much does a Windows Server 2025 Datacenter licence cost per core?", True),
    ("Does TIA Portal v20 support Windows 11?", True),
    ("what is the default port for the kb daemon", True),
    (CHAT, False),
    ("I think we should restructure the README", False),
    ("[ASYNC DELEGATION BATCH COMPLETE] what version is this", False),
    ("thanks, looks good", False),
])
def test_trigger(fg, q, armed):
    assert fg.is_fact_question(q) is armed


def test_reminder_until_lookup_then_clean_reply(fg):
    assert llm(fg) and "withheld until" in llm(fg)
    tool(fg, "web_search")
    assert llm(fg) is None
    assert out(fg) is None


def test_withholds_reply_with_no_lookup(fg):
    llm(fg)
    assert out(fg) and "withheld" in out(fg)
    assert "WITHHELD" in open(fg._LOG).read()


def test_any_lookup_tool_counts(fg):
    for name in ("memory_search", "read_file", "terminal", "mcp__context7__query_docs", "tool_call"):
        fg._sessions.clear()
        llm(fg)
        tool(fg, name)
        assert out(fg) is None, name


def test_chat_turn_untouched(fg):
    assert llm(fg, CHAT) is None and out(fg) is None


def test_log_mode_never_withholds(fg, monkeypatch):
    monkeypatch.setenv("HERMES_FACT_GATE", "log")
    assert llm(fg) is None
    assert out(fg) is None
    assert "WITHHELD" in open(fg._LOG).read()


def test_subagent_and_worker_skipped(fg, monkeypatch):
    assert llm(fg, platform="subagent") is None and out(fg) is None
    monkeypatch.setenv("HERMES_KANBAN_TASK", "card-1")
    assert llm(fg, session="s2", turn_id="t2") is None


def test_follows_session_id_rotation(fg):
    llm(fg, session="s1", turn_id="t1")
    assert out(fg, session="s1-compacted", turn_id="t1") and "withheld" in out(fg, session="s1-compacted", turn_id="t1")
