import os
import pytest
from test_loader import _load_plugin


@pytest.fixture
def vg(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_VERIFY_GATE", "on")
    _load_plugin()
    from lg_under_test.guards import verify_gate as mod
    home = tmp_path / "hermes"
    (home / "skills").mkdir(parents=True)
    (home / "cache").mkdir()
    monkeypatch.setattr(mod, "_ROOTS", (str(home),))
    monkeypatch.setattr(mod, "_EXEMPT", (str(home / "cache"),))
    monkeypatch.setattr(mod, "_LOG", str(tmp_path / "vg.jsonl"))
    mod._seen.clear()
    return mod, home


def pre(mod, tool, session="s1", **args):
    return mod.HOOKS["pre_tool_call"](tool_name=tool, args=args, session_id=session)


def post(mod, tool, result=None, session="s1", **args):
    mod.HOOKS["post_tool_call"](tool_name=tool, args=args, result=result, session_id=session)


def test_write_to_unread_existing_file_blocks(vg):
    mod, home = vg
    f = home / "skills" / "a.md"; f.write_text("x")
    r = pre(mod, "write_file", path=str(f), content="y")
    assert r and r["action"] == "block" and "has read it" in r["message"]


def test_write_after_read_passes(vg):
    mod, home = vg
    f = home / "skills" / "a.md"; f.write_text("x")
    post(mod, "read_file", result="x", path=str(f))
    assert pre(mod, "write_file", path=str(f), content="y") is None


def test_new_file_needs_no_read(vg):
    mod, home = vg
    assert pre(mod, "write_file", path=str(home / "skills" / "new.md"), content="y") is None


def test_rm_accepts_parent_listing_but_sed_does_not(vg):
    mod, home = vg
    f = home / "skills" / "a.md"; f.write_text("x")
    post(mod, "terminal", result=f"{f}\n", command=f"ls -la {home / 'skills'}")
    assert pre(mod, "terminal", command=f"rm {f}") is None
    assert pre(mod, "terminal", command=f"sed -i 's/x/y/' {f}")["action"] == "block"


def test_out_of_scope_and_exempt_paths_ignored(vg):
    mod, home = vg
    c = home / "cache" / "t.txt"; c.write_text("x")
    assert pre(mod, "terminal", command=f"rm {c}") is None
    assert pre(mod, "terminal", command="rm /etc/hosts") is None


def test_unit_branch_and_fact_targets(vg):
    mod, _ = vg
    assert pre(mod, "terminal", command="systemctl --user restart foo.service")["action"] == "block"
    post(mod, "terminal", result="● foo.service - x", command="systemctl --user status foo.service")
    assert pre(mod, "terminal", command="systemctl --user restart foo.service") is None
    assert pre(mod, "terminal", command="git branch -D feat/x")["action"] == "block"
    post(mod, "terminal", result="  feat/x\n* master\n", command="git branch --merged master")
    assert pre(mod, "terminal", command="git branch -D feat/x") is None
    assert pre(mod, "memory_remove", fact_id="abcdef0123456789")["action"] == "block"
    post(mod, "memory_search", result="[abcdef01] some fact", query="some fact")
    assert pre(mod, "memory_remove", fact_id="abcdef0123456789") is None


def test_sessions_are_isolated(vg):
    mod, home = vg
    f = home / "skills" / "a.md"; f.write_text("x")
    post(mod, "read_file", result="x", path=str(f), session="s1")
    assert pre(mod, "write_file", path=str(f), content="y", session="s2")["action"] == "block"


def test_guard_error_lets_call_through_and_logs(vg, monkeypatch):
    mod, home = vg
    monkeypatch.setattr(mod, "_targets", lambda *a: 1 / 0)
    assert pre(mod, "write_file", path=str(home / "skills" / "a.md"), content="y") is None
    assert "ERROR" in open(mod._LOG).read()


def test_off_switch(vg, monkeypatch):
    mod, home = vg
    f = home / "skills" / "a.md"; f.write_text("x")
    monkeypatch.setenv("HERMES_VERIFY_GATE", "off")
    assert pre(mod, "write_file", path=str(f), content="y") is None


def test_blocked_write_does_not_unblock_its_retry(vg):
    mod, home = vg
    f = home / "skills" / "a.md"; f.write_text("x")
    assert pre(mod, "write_file", path=str(f), content="y")["action"] == "block"
    mod.HOOKS["post_tool_call"](tool_name="write_file", args={"path": str(f), "content": "y"}, result=None, session_id="s1", status="blocked")
    assert pre(mod, "write_file", path=str(f), content="y")["action"] == "block"
