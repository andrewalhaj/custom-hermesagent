"""objgate native guard: an inventory-shaped multi-part objective arms it (pre_llm_call); writes are
blocked (pre_tool_call) until run/inventory.json resolves routing. Ported from patches/test_objgate.py.
Run: .venv/bin/python -m pytest plugins/local-guards/tests
"""
import json
import sys

import pytest

from test_loader import _load_plugin

ARMED_TEXT = ("Inventory every dataset in the warehouse and surface all the gaps. "
              "Also bring them all in and populate the missing fields.")
NOINTENT_TEXT = "Please fix the broken login flow and also restyle the dashboard buttons to match the new palette."


@pytest.fixture
def og(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_OBJGATE", raising=False)
    for name in [n for n in sys.modules if n.startswith("lg_under_test.guards")]:
        del sys.modules[name]
    _load_plugin()
    from lg_under_test.guards import objgate as mod
    monkeypatch.setattr(mod, "INVENTORY_PATH", str(tmp_path / "inventory.json"))
    mod.OUT = str(tmp_path / "output.md")
    return mod


def turn(og, text=ARMED_TEXT, session="s1", turn_id="t1"):
    og._on_pre_llm_call(session_id=session, turn_id=turn_id, user_message=text)


def call(og, tool="write_file", session="s1", turn_id="t1", **args):
    return og._on_pre_tool_call(tool_name=tool, args=args or {"path": og.OUT, "content": "x"}, session_id=session,
                                turn_id=turn_id)


def inventory(og, obj):
    with open(og.INVENTORY_PATH, "w") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))


def test_registers_both_hooks(og):
    assert set(og.HOOKS) == {"pre_llm_call", "pre_tool_call"}


def test_no_inventory_blocks_and_names_the_turn_hash(og):
    turn(og)
    r = call(og)
    assert r["action"] == "block" and "produce inventory.json first" in r["message"]
    assert og._armed["s1"] in r["message"]  # the agent can write the right turn_hash without computing it


def test_stale_hash_blocks(og):
    turn(og)
    inventory(og, {"turn_hash": "stale9999xxx", "chunk_count": 1})
    assert "previous objective" in call(og)["message"]


def test_missing_hash_blocks(og):
    turn(og)
    inventory(og, {"chunk_count": 1})
    assert "no turn_hash" in call(og)["message"]


def test_over_k_without_inline_blocks(og):
    turn(og)
    inventory(og, {"turn_hash": og._armed["s1"], "chunk_count": 5, "routing": "fanout"})
    assert "chunk_count=5 > K=3" in call(og)["message"]


def test_over_k_inline_with_reason_passes(og):
    turn(og)
    inventory(og, {"turn_hash": og._armed["s1"], "chunk_count": 5, "routing": "inline", "reason": "one file"})
    assert call(og, "patch", path=og.OUT, old_string="a", new_string="b") is None


def test_within_k_passes(og):
    turn(og)
    inventory(og, {"turn_hash": og._armed["s1"], "chunk_count": 2})
    assert call(og) is None


@pytest.mark.parametrize("tool,args", [
    ("delegate_task", {"goal": "chunk 1"}),
    ("kanban_create", {"title": "x"}),
    ("terminal", {"command": "hermes kanban swarm plan.yaml"}),
])
def test_routing_tool_clears(og, tool, args):
    turn(og)
    assert call(og, tool, **args) is None
    assert call(og) is None and "s1" not in og._armed


def test_worker_never_arms(og, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "worker-123")
    turn(og)
    assert call(og) is None and not og._armed


@pytest.mark.parametrize("text", [
    NOINTENT_TEXT,
    "[ASYNC DELEGATION BATCH COMPLETE] inventory every gap; also populate fields; also surface all.",
    "x [INTERNAL NOTIFICATION] inventory every gap; also populate fields; also surface all.",
])
def test_non_objective_text_never_arms(og, text):
    turn(og, text)
    assert call(og) is None and not og._armed


def test_multipart_list_user_message(og):
    turn(og, [{"type": "text", "text": ARMED_TEXT}, {"type": "image_url", "image_url": {}}])
    assert call(og)["action"] == "block"


def test_delegated_child_never_arms(og):
    og._on_pre_llm_call(session_id="child", turn_id="t1", user_message=ARMED_TEXT, platform="subagent")
    assert call(og, session="child") is None and not og._armed


def test_sessions_are_isolated(og):
    turn(og, session="s1")
    assert call(og, session="s2", turn_id="t2") is None


def test_survives_compression_session_rotation(og):
    turn(og, session="s1", turn_id="t1")
    assert call(og, session="s1-compressed", turn_id="t1") is not None
    assert call(og, session="s1-compressed", turn_id="t2") is not None  # next user turn
    assert call(og, tool="delegate_task", session="s1-compressed", turn_id="t2") is None
    assert not og._armed



def test_survives_turn_start_compaction(og):
    turn(og, session="s1", turn_id="s1:task:aaaa")
    # compaction rotated the session before any tool call; the new turn_id still carries the old session id
    assert call(og, session="s2", turn_id="s1:task:bbbb") is not None


def test_malformed_inventory_fails_open(og):
    turn(og)
    inventory(og, "[1, 2, 3]")
    assert call(og) is None


def test_inventory_write_itself_exempt(og):
    turn(og)
    assert call(og, path=og.INVENTORY_PATH, content="{}") is None


def test_non_write_tools_pass(og):
    turn(og)
    assert call(og, "read_file", path=og.OUT) is None


def test_disabled_by_env(og, monkeypatch):
    monkeypatch.setenv("HERMES_OBJGATE", "off")
    turn(og)
    assert call(og) is None


def test_none_turn_id_fails_open(og):
    assert og._on_pre_tool_call(tool_name="write_file", args={}, session_id="", turn_id=None) is None
