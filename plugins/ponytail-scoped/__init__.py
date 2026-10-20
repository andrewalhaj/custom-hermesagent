"""Ponytail, injected only once the session shows a coding signal, with a code-only scope preamble."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()
VENDOR = HERMES_HOME / "vendor" / "ponytail" / "__init__.py"
SCOPE = Path(__file__).resolve().parent / "SCOPE.md"
MARKER = "PONYTAIL MODE ACTIVE — level: "

# Tunable signal patterns.
CODE_EXT = r"(?:py|js|ts|tsx|jsx|sh|go|rs|toml|sql|html|css)"  # no json/yaml: config edits aren't code
EDIT_TOOLS = {"patch", "write_file", "apply_patch"}
RUN_TOOLS = {"terminal", "execute_code"}
RUN_COMMANDS = (
    "git commit", "git diff", "git merge", "pytest", "python -m unittest",
    "npm test", "npm run", "cargo", "go test", "make ",
)
PATH_KEYS = ("path", "file_path", "filepath")
EDIT_PATH_RE = re.compile(rf"\.{CODE_EXT}(?![\w.])")
USER_INTENT_RE = re.compile(
    r"\b(fix|refactor|implement|patch|write|add|debug|test)\b.*"
    r"\b(code|function|script|bug|test|route|endpoint|class|module|repo|branch|PR)\b",
    re.IGNORECASE,
)
USER_PATH_RE = re.compile(rf"{re.escape(str(Path.home()))}/[\w.-]+/\S*\.{CODE_EXT}(?![\w.])")
FENCE_RE = re.compile(r"```\w+")


def _text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return " ".join(p.get("text", "") for p in message if isinstance(p, dict))
    return ""


def _tool_calls(history: Any):
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        for call in msg.get("tool_calls") or []:
            fn = (call or {}).get("function") or {}
            args = fn.get("arguments") or ""
            if not isinstance(args, str):
                args = json.dumps(args)
            yield fn.get("name"), args


def _edits_code(args: str) -> bool:
    try:
        parsed = json.loads(args)
        paths = [parsed[k] for k in PATH_KEYS if isinstance(parsed.get(k), str)]
    except (ValueError, AttributeError):
        paths = []
    # No explicit path (e.g. a raw patch body): fall back to scanning the arguments.
    return any(EDIT_PATH_RE.search(p) for p in paths) if paths else bool(EDIT_PATH_RE.search(args))


def coding_signal(user_message: Any, history: Any) -> bool:
    for name, args in _tool_calls(history):
        if name in EDIT_TOOLS and _edits_code(args):
            return True
        if name in RUN_TOOLS and any(c in args for c in RUN_COMMANDS):
            return True
    text = _text(user_message)
    return bool(USER_INTENT_RE.search(text) or USER_PATH_RE.search(text) or FENCE_RE.search(text))


def _has_marker(history: Any) -> bool:
    return any(
        isinstance(m, dict) and isinstance(m.get("api_content"), str) and MARKER in m["api_content"]
        for m in history or []
    )


def _make_hook(inner):
    def _pre_llm_call(user_message: Any = None, conversation_history: Any = None, **kw: Any):
        kw.update(user_message=user_message, conversation_history=conversation_history)
        if _has_marker(conversation_history):
            return inner(**kw)
        if not coding_signal(user_message, conversation_history):
            return None
        result = inner(**kw)
        if result and result.get("context"):
            result["context"] = SCOPE.read_text(encoding="utf-8").strip() + "\n\n" + result["context"]
        return result

    return _pre_llm_call


class _Proxy:
    """Passes everything to the real ctx except the vendor's pre_llm_call hook, which it captures."""

    def __init__(self, ctx: Any):
        self._ctx = ctx
        self.inner = None

    def register_hook(self, name: str, fn: Any) -> None:
        if name == "pre_llm_call":
            self.inner = fn
        else:
            self._ctx.register_hook(name, fn)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)


def _load_vendor():
    spec = importlib.util.spec_from_file_location("ponytail_vendor", VENDOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def register(ctx: Any) -> None:
    try:
        vendor = _load_vendor()
        proxy = _Proxy(ctx)
        vendor.register(proxy)
    except Exception as exc:  # never crash the gateway
        log.warning("ponytail-scoped: vendor ponytail failed to load (%s); registering nothing", exc)
        return
    if proxy.inner:
        ctx.register_hook("pre_llm_call", _make_hook(proxy.inner))
