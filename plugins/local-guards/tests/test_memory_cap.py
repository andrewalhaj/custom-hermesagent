"""memory_cap native guard: one context line when MEMORY.md crosses 80% or USER.md crosses 95%,
once per crossing, silent otherwise. Run: .venv/bin/python -m pytest plugins/local-guards/tests
"""
import sys

import pytest

from test_loader import _load_plugin


@pytest.fixture
def mc(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_MEMORY_CAP", raising=False)
    for name in [n for n in sys.modules if n.startswith("lg_under_test.guards")]:
        del sys.modules[name]
    _load_plugin()
    from lg_under_test.guards import memory_cap as mod
    mod._fired.clear()
    return mod


def test_silent_under_both_lines(mc):
    assert mc.check({"memory": (2399, 3000), "user": (2136, 2250)}) == ""  # 79.9% and 94.9%


def test_memory_fires_once_per_crossing_then_rearms(mc):
    hot = {"memory": (2584, 3000), "user": (1000, 2250)}
    assert "MEMORY.md at 86% (2,584/3,000)" in mc.check(hot)
    assert mc.check(hot) == ""                                  # still over: no repeat
    assert mc.check({"memory": (1788, 3000), "user": (1000, 2250)}) == ""  # dropped under
    assert "MEMORY.md" in mc.check(hot)                         # crossed again


def test_user_line_is_95_and_both_can_fire_together(mc):
    assert mc.check({"memory": (1000, 3000), "user": (2037, 2250)}) == ""  # 90%: Andrew's USER.md line is 95
    line = mc.check({"memory": (2900, 3000), "user": (2200, 2250)})
    assert "MEMORY.md at 96%" in line and "USER.md at 97%" in line


def test_hook_wraps_check_and_skips_workers(mc, monkeypatch):
    monkeypatch.setattr(mc, "_usage", lambda: {"memory": (2900, 3000), "user": (0, 2250)})
    assert mc._on_pre_llm_call(session_id="s1", platform="cli")["context"].startswith("[memory_cap] MEMORY.md")
    mc._fired.clear()
    assert mc._on_pre_llm_call(session_id="s1", platform="subagent") is None
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_123")
    assert mc._on_pre_llm_call(session_id="s1", platform="cli") is None


def test_unreadable_store_is_a_silent_noop(mc, monkeypatch):
    monkeypatch.setattr(mc, "_usage", lambda: (_ for _ in ()).throw(OSError("boom")))
    assert mc._on_pre_llm_call(session_id="s1", platform="cli") is None
