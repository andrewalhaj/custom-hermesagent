import importlib.util
import json
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "__init__.py"
spec = importlib.util.spec_from_file_location("ponytail_scoped", PLUGIN)
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

MARKER = "PONYTAIL MODE ACTIVE — level: full"


def call(name, **args):
    return {"role": "assistant", "tool_calls": [{"function": {"name": name, "arguments": json.dumps(args)}}]}


class Recorder:
    """Stand-in for the Hermes ctx: records what gets registered."""

    def __init__(self):
        self.hooks, self.commands, self.skills = {}, [], []

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_command(self, name, *a, **kw):
        self.commands.append(name)

    def register_skill(self, name, path):
        self.skills.append(name)


class CodingSignal(unittest.TestCase):
    def test_patch_on_py_is_signal(self):
        self.assertTrue(ps.coding_signal("ok", [call("patch", path="a/b.py")]))

    def test_write_file_on_md_is_not_signal(self):
        self.assertFalse(ps.coding_signal("ok", [call("write_file", path="notes.md", content="x.py")]))

    def test_terminal_pytest_is_signal(self):
        self.assertTrue(ps.coding_signal("ok", [call("terminal", command="cd x && pytest -q")]))

    def test_terminal_ls_is_not_signal(self):
        self.assertFalse(ps.coding_signal("ok", [call("terminal", command="ls -la")]))

    def test_user_message_fix_bug(self):
        self.assertTrue(ps.coding_signal("fix the bug in routes/auth.py", []))

    def test_user_message_code_path(self):
        self.assertTrue(ps.coding_signal(f"look at {Path.home()}/repo/src/app.ts", []))

    def test_user_message_fenced_block(self):
        self.assertTrue(ps.coding_signal("why?\n```python\nx=1\n```", []))

    def test_user_message_list_content(self):
        self.assertTrue(ps.coding_signal([{"type": "text", "text": "fix the bug"}], []))

    def test_non_code_messages(self):
        self.assertFalse(ps.coding_signal("explain vSphere licensing", []))
        self.assertFalse(ps.coding_signal("write a LinkedIn post", []))


class Hook(unittest.TestCase):
    def setUp(self):
        self.calls = 0

        def inner(**kw):
            self.calls += 1
            return {"context": f"{MARKER}\n\nrules"}

        self.hook = ps._make_hook(inner)

    def test_no_signal_returns_none_without_inner(self):
        self.assertIsNone(self.hook(user_message="explain vSphere", conversation_history=[]))
        self.assertEqual(self.calls, 0)

    def test_signal_prepends_scope(self):
        out = self.hook(user_message="ok", conversation_history=[call("patch", path="x.py")])
        self.assertEqual(self.calls, 1)
        self.assertTrue(out["context"].startswith(ps.SCOPE.read_text(encoding="utf-8").strip()))
        self.assertIn(MARKER, out["context"])

    def test_marker_present_delegates_without_scope(self):
        hist = [{"role": "user", "api_content": MARKER}]
        self.hook = ps._make_hook(lambda **kw: None)
        self.assertIsNone(self.hook(user_message="explain", conversation_history=hist))
        self.hook = ps._make_hook(lambda **kw: {"context": "new"})
        self.assertEqual(self.hook(user_message="explain", conversation_history=hist), {"context": "new"})


class Register(unittest.TestCase):
    def test_wires_vendor_with_our_hook(self):
        ctx = Recorder()
        ps.register(ctx)
        self.assertIn("pre_gateway_dispatch", ctx.hooks)
        self.assertIn("ponytail", ctx.commands)
        self.assertIn("ponytail", ctx.skills)
        self.assertNotEqual(ctx.hooks["pre_llm_call"].__module__, "ponytail_vendor")

    def test_vendor_import_failure_does_not_raise(self):
        orig = ps.VENDOR
        ps.VENDOR = Path("/nonexistent/ponytail/__init__.py")
        try:
            ctx = Recorder()
            ps.register(ctx)
            self.assertEqual(ctx.hooks, {})
        finally:
            ps.VENDOR = orig


if __name__ == "__main__":
    unittest.main()
