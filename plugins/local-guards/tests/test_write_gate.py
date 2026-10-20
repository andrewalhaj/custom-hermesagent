"""write_gate native guard: gated calls go to Hermes's native approval prompt (block when no human can
answer), pass with a valid grant.
Ported from patches/test_write_gate*.py; grant/audit files are redirected to tmp_path.
Run: .venv/bin/python -m pytest plugins/local-guards/tests
"""
import json
import subprocess
import sys
import time

import pytest

from test_loader import PLUGIN_DIR, FakeCtx, _load_plugin

HOME = "/srv/hermes-test-home"  # gated literals are built from HERMES_HOME at import; need not exist


@pytest.fixture
def wg(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", HOME)
    monkeypatch.delenv("HERMES_WRITE_GATE", raising=False)
    for name in [n for n in sys.modules if n.startswith("lg_under_test.guards")]:
        del sys.modules[name]  # re-read HERMES_HOME
    _load_plugin()
    from lg_under_test.guards import write_gate as mod
    monkeypatch.setattr(mod, "_GRANT_PATH", str(tmp_path / "grant"))
    monkeypatch.setattr(mod, "_AUDIT_LOG", str(tmp_path / "audit.log"))
    monkeypatch.setattr(mod, "_human_can_answer", lambda: False)  # default: the no-human block path
    return mod


def call(wg, tool, **args):
    return wg._on_pre_tool_call(tool_name=tool, args=args, session_id="s1")


def arm(wg, ttl):
    with open(wg._GRANT_PATH, "w") as f:
        json.dump({"armed_at": time.time(), "expires": time.time() + ttl, "note": "test"}, f)


@pytest.mark.parametrize("tool,args", [
    ("terminal", {"command": "systemctl --user restart hermes-gateway"}),
    ("terminal", {"command": "ssh root@vps-a docker restart mycontainer"}),
    ("terminal", {"command": "git push --force origin feat/x"}),
    ("terminal", {"command": "echo x >> ~/.hermes/config.yaml"}),
    ("write_file", {"path": f"{HOME}/config.yaml", "content": "x"}),
    ("write_file", {"path": f"{HOME}/plugins/local-guards/guards/write_gate.py", "content": ""}),
    ("patch", {"path": f"{HOME}/skills/x/SKILL.md", "old_string": "a", "new_string": "b"}),
    ("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** Update File: {HOME}/AGENTS.md\n@@\n-a\n+b\n*** End Patch"}),
    ("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** Move File: /tmp/x.md -> {HOME}/skills/a/SKILL.md\n*** End Patch"}),
    ("patch", {"mode": "patch", "patch": f"*** Begin Patch\n***Update  File:{HOME}/plugins/local-guards/guards/write_gate.py\n*** End Patch"}),
    ("terminal", {"command": "sed -i 's/block/x/' ~/.hermes/plugins/local-guards/guards/write_gate.py"}),
    ("execute_code", {"code": f"terminal('echo x >> {HOME}/config.yaml.probe')"}),
    ("execute_code", {"code": f"write_file('{HOME}/skills/x/SKILL.md', 'overwrite')"}),
    ("execute_code", {"code": "terminal('systemctl --user restart hermes-gateway.service')"}),
])
def test_gated_call_requests_native_approval(wg, monkeypatch, tool, args):
    monkeypatch.setattr(wg, "_human_can_answer", lambda: True)
    res = call(wg, tool, **args)
    assert res["action"] == "approve" and res["message"].startswith("[WRITE GATE]")
    assert res["rule_key"].startswith(f"write_gate:{tool}:")  # [s]ession/[a]lways scope to this gated action
    assert "APPROVE-REQ" in open(wg._AUDIT_LOG).read()


def test_approval_scope_is_the_exact_action_and_shows_target(wg, monkeypatch):
    monkeypatch.setattr(wg, "_human_can_answer", lambda: True)
    a = call(wg, "patch", path=f"{HOME}/skills/a/SKILL.md", old_string="x", new_string="y")
    b = call(wg, "patch", path=f"{HOME}/skills/b/SKILL.md", old_string="x", new_string="y")
    assert a["rule_key"] != b["rule_key"]  # an [a]lways on one skill must not approve another
    r1 = call(wg, "terminal", command="systemctl --user restart hermes-gateway.service")
    r2 = call(wg, "terminal", command="systemctl --user restart other.service")
    assert r1["rule_key"] != r2["rule_key"] and "hermes-gateway.service" in r1["message"]
    assert f"{HOME}/skills/a/SKILL.md" in a["message"]


@pytest.mark.parametrize("tool,args", [
    ("terminal", {"command": "systemctl --user restart hermes-gateway"}),
    ("write_file", {"path": f"{HOME}/config.yaml", "content": "x"}),
])
def test_gated_call_without_human_is_blocked_and_audited(wg, tool, args):
    res = call(wg, tool, **args)
    assert res["action"] == "block" and res["message"].startswith("[WRITE GATE]")
    assert "guards/write_gate.py arm" in res["message"]  # points at the live CLI, not patches/
    assert "BLOCK" in open(wg._AUDIT_LOG).read()


def test_no_human_when_core_says_yolo_or_absent(wg, monkeypatch):
    import types
    monkeypatch.undo()  # drop the fixture's _human_can_answer stub; this test drives the real one
    approval = types.SimpleNamespace(_yolo_active=lambda: False, _presence=lambda: (None, False, True, False))
    ctx = types.SimpleNamespace(_get_approval_mode=lambda: "manual")
    tools = types.SimpleNamespace(approval=approval, approval_context=ctx)
    for name, mod in (("tools", tools), ("tools.approval", approval), ("tools.approval_context", ctx)):
        monkeypatch.setitem(sys.modules, name, mod)
    assert wg._human_can_answer()  # gateway present
    approval._presence = lambda: (None, False, False, False)
    assert not wg._human_can_answer()  # cron / -q / unattended: native would auto-resolve
    approval._presence = lambda: (None, True, False, False)
    ctx._get_approval_mode = lambda: "off"
    assert not wg._human_can_answer()
    ctx._get_approval_mode = lambda: "manual"
    approval._yolo_active = lambda: True
    assert not wg._human_can_answer()
    approval._yolo_active = lambda: 1 / 0  # core private API drifted: fall back to the block
    assert not wg._human_can_answer()


@pytest.mark.parametrize("tool,args", [
    ("write_file", {"path": f"{HOME}/memories/MEMORY.md", "content": "x"}),
    ("terminal", {"command": "journalctl --user -u hermes-gateway -n 50"}),
    ("terminal", {"command": "ssh root@vps-a docker ps"}),
    ("terminal", {"command": "grep x ~/.hermes/config.yaml 2>/dev/null"}),
    ("terminal", {"command": "git push --force-with-lease origin feat/x"}),
    ("execute_code", {"code": f"print(read_file('{HOME}/config.yaml'))"}),
    ("execute_code", {"code": "import subprocess; subprocess.run(['grep', '-r', 'foo', '.'])"}),
    ("read_file", {"path": f"{HOME}/config.yaml"}),
    ("patch", {"mode": "patch", "patch": "*** Begin Patch\n*** Update File: /tmp/x.py\n*** End Patch"}),
])
def test_ungated_call_passes(wg, tool, args):
    assert call(wg, tool, **args) is None


@pytest.mark.parametrize("note,blocked", [
    ('"Andrew ok: restart gateway -> apply cfg"', False),
    ("'approved rm of stale cron in config.yaml'", False),
    ('"ok: systemctl --user restart hermes-gateway"', False),
    ('"x" --ttl 60; sed -i s/a/b/ {cli}', True),  # chained edit of the guard stays gated
    ('"x" && systemctl --user restart hermes-gateway', True),
    ('"$(sed -i s/a/b/ {cli})"', True),  # command substitution in the note still runs
])
def test_arm_command_does_not_block_itself(wg, note, blocked):
    cmd = f"python3 {wg._CLI} arm " + note.format(cli=wg._CLI)
    assert (call(wg, "terminal", command=cmd) is not None) == blocked



@pytest.mark.parametrize("cli", ["~/.hermes/plugins/local-guards/guards/write_gate.py",
                                 "plugins/local-guards/guards/write_gate.py"])
def test_arm_command_any_path_spelling_does_not_block_itself(wg, cli):
    assert call(wg, "terminal", command=f'python3 {cli} arm "ok: restart gateway -> apply cfg"') is None
    assert call(wg, "terminal", command=f'sed -i s/a/b/ {cli} arm "x"') is not None  # not a python call: still gated


def test_valid_grant_passes_expired_grant_blocks(wg, monkeypatch):
    monkeypatch.setattr(wg, "_human_can_answer", lambda: True)  # armed = batch mode: no prompt even with a human
    arm(wg, 600)
    assert call(wg, "terminal", command="systemctl --user restart x") is None
    monkeypatch.setattr(wg, "_human_can_answer", lambda: False)
    arm(wg, 600)
    assert call(wg, "terminal", command="systemctl --user restart x") is None
    arm(wg, -1)
    assert call(wg, "terminal", command="systemctl --user restart x")["action"] == "block"
    open(wg._GRANT_PATH, "w").write("{corrupt")
    assert call(wg, "terminal", command="systemctl --user restart x")["action"] == "block"


def test_classifier_error_fails_open(wg, monkeypatch):
    monkeypatch.setattr(wg, "_reason", lambda *a: 1 / 0)
    assert call(wg, "terminal", command="systemctl --user restart x") is None


def test_kill_switch(wg, monkeypatch):
    monkeypatch.setenv("HERMES_WRITE_GATE", "off")
    assert call(wg, "terminal", command="systemctl --user restart x") is None


def test_fires_through_loader_registration(wg):
    ctx = FakeCtx()
    import lg_under_test.guards as guards
    lg = sys.modules["lg_under_test"]
    armed, _ = lg._arm_native(ctx, guards)
    assert "write_gate" in armed
    cbs = [cb for name, cb in ctx.hooks if name == "pre_tool_call" and cb.__module__.endswith("write_gate")]
    assert cbs and cbs[0](tool_name="write_file", args={"path": f"{HOME}/.env"})["action"] == "block"


def test_cli_arm_status_disarm(tmp_path):
    cli = [sys.executable, str(PLUGIN_DIR / "guards" / "write_gate.py")]
    env = {"HERMES_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    run = lambda *a: subprocess.run(cli + list(a), env=env, capture_output=True, text=True, check=True).stdout
    assert "ARMED for 5s" in run("arm", "unit test", "--ttl", "5")
    assert json.loads((tmp_path / ".write_gate_grant").read_text())["note"] == "unit test"
    assert "ARMED" in run("status")
    assert "DISARMED" in run("disarm") and not (tmp_path / ".write_gate_grant").exists()


@pytest.mark.parametrize("cmd", [
    f"echo '{{\"expires\": 9999999999}}' > {HOME}/.write_gate_grant",
    "echo x >> ~/.hermes/references/write-gate-audit.log",
    f"cp x {HOME}/scripts/a.py && cp y {HOME}/plugins/p.py",
    f"echo x > {HOME}/vendor/v.py",
    "ssh h rm -rf /srv",
    "systemctl --user --no-block restart x",
    "apt-get -y install x",
    "chmod 0777 x",
    "git clean --force",
    "git push origin HEAD:refs/heads/main",
    "pkill -f gateway",
    "kill -KILL 123",
    "sudo tee ~/.hermes/config.yaml",
])
def test_regex_gaps_are_gated(wg, cmd):
    assert call(wg, "terminal", command=cmd) is not None


def test_raw_grant_write_is_gated_but_sanctioned_arm_is_not(wg, tmp_path):
    assert call(wg, "terminal", command=f"echo x > {HOME}/.write_gate_grant")["action"] == "block"
    assert call(wg, "write_file", path=f"{HOME}/.write_gate_grant", content="x")["action"] == "block"
    assert call(wg, "terminal", command=f'python3 {wg._CLI} arm "ok: batch"') is None
    done = subprocess.run([sys.executable, wg._CLI, "arm", "ok: batch", "--ttl", "5"], capture_output=True, text=True,
                          env={"HERMES_HOME": str(tmp_path)})
    assert done.returncode == 0 and (tmp_path / ".write_gate_grant").exists(), done.stderr


def test_redirect_list_derives_from_gated_literals(wg):
    for short in ("skills/", "profiles/", "cron/", "patches/", "scripts/", "plugins/", "vendor/"):
        assert f"hermes-test-home/{short}" in wg._GATED_PATH_LITERALS_FOR_REDIRECT
    assert "config.yaml" in wg._GATED_PATH_LITERALS_FOR_REDIRECT


@pytest.mark.parametrize("cmd", ["grep -rm 1 x ~/.hermes/config.yaml", "ssh h grep -rm 1 x", "grep -rm 1 x"])
def test_grep_rm_flag_is_not_flagged(wg, cmd):
    assert call(wg, "terminal", command=cmd) is None


def test_nul_in_path_is_gated(wg):
    assert call(wg, "write_file", path="/tmp/a\x00b", content="x")["action"] == "block"
