"""verification native guard: epistemic footer on every delegate_task brief (pre_tool_call modify) and one
absence-check nudge per turn on absence-shaped terminal output. Replaces patches/verification_checkpoint.py.
Run: .venv/bin/python -m pytest plugins/local-guards/tests
"""
import copy
import sys

import pytest

from test_loader import _load_plugin


@pytest.fixture
def vf(monkeypatch):
    monkeypatch.delenv("HERMES_VERIFICATION_CHECKPOINT", raising=False)
    for name in [n for n in sys.modules if n.startswith("lg_under_test.guards")]:
        del sys.modules[name]
    _load_plugin()
    from lg_under_test.guards import verification as mod
    return mod


def pre(vf, tool="delegate_task", **args):
    return vf._on_pre_tool_call(tool_name=tool, args=args, session_id="s1")


def term(vf, out, tool="terminal", session="s1", turn_id="t1"):
    return vf._on_transform_tool_result(tool_name=tool, args={"command": "x"}, result=out,
                                        session_id=session, turn_id=turn_id)


def test_registers_both_hooks(vf):
    assert set(vf.HOOKS) == {"pre_tool_call", "transform_tool_result"}


def test_footer_appended_to_goal(vf):
    out = pre(vf, goal="Find the unit")
    assert out["action"] == "modify"
    assert out["args"]["goal"].startswith("Find the unit\n\n[Epistemic contract")


def test_footer_prefers_context_over_goal(vf):
    out = pre(vf, goal="g", context="c")
    assert out["args"] == {"context": "c" + vf.FOOTER}


def test_footer_reaches_every_batch_task_without_mutating_input(vf):
    args = {"tasks": [{"goal": "a"}, {"goal": "b", "context": "ctx"}, "junk"]}
    before = copy.deepcopy(args)
    out = vf._on_pre_tool_call(tool_name="delegate_task", args=args)
    assert args == before
    assert out["args"]["tasks"] == [{"goal": "a" + vf.FOOTER}, {"goal": "b", "context": "ctx" + vf.FOOTER}, "junk"]


@pytest.mark.parametrize("tool, args", [
    ("delegate_task", {"goal": "already\n\n[Epistemic contract (auto-appended): x]"}),
    ("delegate_task", {}),
    ("terminal", {"goal": "not a delegate"}),
])
def test_footer_noop(vf, tool, args):
    assert pre(vf, tool, **args) is None


@pytest.mark.parametrize("out", [
    "Unit foo.service could not be found.", "bash: rg: command not found", "ls: x: No such file or directory",
    '{"output": "inactive", "exit_code": 3, "error": null}', "fatal: not a git repository",
])
def test_absence_output_nudges_once_per_turn(vf, out):
    got = term(vf, out)
    assert got.startswith(out + "\n\n[Absence check:")
    assert term(vf, out) is None
    assert term(vf, out, turn_id="t2").startswith(out)


@pytest.mark.parametrize("out, tool", [
    ("active\nall good", "terminal"),
    ("file not found", "read_file"),
    ({"output": "not found"}, "terminal"),
])
def test_absence_noop(vf, out, tool):
    assert term(vf, out, tool=tool) is None


def test_disabled_env_turns_both_hooks_off(vf, monkeypatch):
    monkeypatch.setenv("HERMES_VERIFICATION_CHECKPOINT", "off")
    assert pre(vf, goal="g") is None
    assert term(vf, "command not found") is None


def test_modify_args_are_partial_and_consistent(vf):
    # core shallow-merges modify args into the originals: both shapes send only what changed
    assert pre(vf, goal="g", toolsets=["x"])["args"] == {"goal": "g" + vf.FOOTER}
    assert set(pre(vf, tasks=[{"goal": "a"}], toolsets=["x"])["args"]) == {"tasks"}
