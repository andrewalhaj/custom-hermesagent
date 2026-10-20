"""Loader + guards/_common tests: native HOOKS register, broken guards are isolated,
a missing required guard aborts startup. Run: .venv/bin/python -m pytest plugins/local-guards/tests
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "lg_under_test", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeCtx:
    def __init__(self):
        self.hooks = []

    def register_hook(self, name, cb):
        self.hooks.append((name, cb))


@pytest.fixture
def plugin():
    return _load_plugin()


@pytest.fixture
def fake_guards(tmp_path, monkeypatch):
    pkg = tmp_path / "fake_guards"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "_helper.py").write_text("raise RuntimeError('helpers must not be loaded as guards')\n")
    (pkg / "good.py").write_text("HOOKS = {'pre_tool_call': lambda **kw: None}\n")
    (pkg / "broken.py").write_text("raise ImportError('boom')\n")
    (pkg / "no_hooks.py").write_text("X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    import fake_guards
    yield fake_guards
    for name in [n for n in sys.modules if n.startswith("fake_guards")]:
        del sys.modules[name]


def test_native_registers_hooks_and_isolates_failures(plugin, fake_guards):
    ctx = FakeCtx()
    armed, total = plugin._arm_native(ctx, fake_guards)
    assert armed == {"good"}
    assert total == 3  # _helper skipped; broken + no_hooks counted as failures
    assert [name for name, _ in ctx.hooks] == ["pre_tool_call"]


def test_transform_hooks_chain_into_one_registration(plugin, fake_guards):
    # Hermes keeps only the first str a transform_tool_result hook returns, so ours must compose.
    for name, body in (("t1", "lambda result=None, **kw: result + ' +a'"), ("t2", "lambda result=None, **kw: None"),
                       ("t3", "lambda result=None, **kw: 1 / 0"), ("t4", "lambda result=None, **kw: result + ' +b'")):
        (Path(fake_guards.__path__[0]) / f"{name}.py").write_text(f"HOOKS = {{'transform_tool_result': {body}}}\n")
    ctx = FakeCtx()
    plugin._arm_native(ctx, fake_guards)
    transforms = [cb for name, cb in ctx.hooks if name == "transform_tool_result"]
    assert len(transforms) == 1
    assert transforms[0](tool_name="terminal", result="out") == "out +a +b"  # t3's error is isolated
    assert transforms[0](tool_name="terminal", result={"not": "str"}) is None  # nothing changed -> leave it


def test_register_aborts_when_required_guard_missing(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_arm_native", lambda ctx, pkg: ({"other"}, 2))  # write_gate failed to import
    with pytest.raises(RuntimeError, match="write_gate"):
        plugin.register(FakeCtx())
    assert plugin._REGISTERED is False


def test_register_arms_required_guard_and_is_idempotent(plugin, monkeypatch):
    calls = []
    monkeypatch.setattr(plugin, "_arm_native", lambda ctx, pkg: calls.append(1) or ({"write_gate"}, 1))
    plugin.register(FakeCtx())
    plugin.register(FakeCtx())
    assert plugin._REGISTERED is True and calls == [1]


def test_real_guards_all_arm_natively(plugin):
    from lg_under_test import guards
    armed, total = plugin._arm_native(FakeCtx(), guards)
    assert armed >= {"write_gate", "objgate", "routing", "verification"} and len(armed) == total


def test_common_helpers(plugin, monkeypatch):
    from lg_under_test.guards import _common as c
    monkeypatch.delenv("HERMES_X_GUARD", raising=False)
    assert c.enabled("HERMES_X_GUARD")
    for off in ("off", "0", "False", " no ", "disabled"):
        monkeypatch.setenv("HERMES_X_GUARD", off)
        assert not c.enabled("HERMES_X_GUARD")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "")
    assert not c.is_worker()
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_123")
    assert c.is_worker()
    assert c.block("no") == {"action": "block", "message": "no"}
    with pytest.raises(ValueError):
        c.block("")
    assert c.nudge("out", "hint") == "out\n\nhint"
    assert c.nudge({"not": "str"}, "hint") is None
