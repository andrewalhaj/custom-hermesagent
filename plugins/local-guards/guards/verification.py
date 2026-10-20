"""verification — after six stored-assertion mistakes on 2026-08-14: every delegate_task brief gets an epistemic-contract
footer (pre_tool_call modify), and the first absence-shaped terminal result of a turn gets a reverse-lookup nudge.
Replaces patches/verification_checkpoint.py. Advisory; every error returns None.
"""
from __future__ import annotations

import re
import sys

from ._common import enabled, nudge

FOOTER = (
    "\n\n[Epistemic contract (auto-appended): claims in this context are UNVERIFIED unless explicitly marked "
    "'probe-backed <date>'. Re-probe any load-bearing claim (ports, models, unit names, paths, ownership) before "
    "building on it. If the task's premise fails a probe, report the premise failure — do not work around it.]"
)
_NUDGE = (
    "[Absence check: 'not found by name' ≠ 'doesn't exist'. Before concluding something is unmanaged/down/absent, "
    "reverse-lookup from the live object: ss -tlnp → PID → systemctl status <PID> / ps -o unit= -p <PID>; ls the "
    "actual path; query the actual API. Enumerate with a full scan, not search top-K, before claiming 'N affected'.]"
)
_ABSENT = re.compile(r"could not be found|not[- ]found|No such unit|no such file or directory|\binactive\b|not installed"
                     r"|No such process|0 results|not a git repository|does not exist", re.IGNORECASE | re.MULTILINE)

# session_id -> turn_id of the last nudge (one per turn). shortcut: lives for the process, a string per session.
_nudged: dict[str, str] = {}


def _with_footer(task: dict) -> dict | None:
    """Copy of task with the footer on its context (else goal), or None if absent / already footered."""
    key = "context" if task.get("context") else "goal"
    text = task.get(key)
    if not isinstance(text, str) or not text or "[Epistemic contract" in text:
        return None
    return {**task, key: text + FOOTER}


def _on_pre_tool_call(tool_name: str = "", args=None, **_):
    if tool_name != "delegate_task" or not isinstance(args, dict) or not enabled("HERMES_VERIFICATION_CHECKPOINT"):
        return None
    try:
        if isinstance(args.get("tasks"), list):
            tasks = [(_with_footer(t) if isinstance(t, dict) else None) or t for t in args["tasks"]]
            return {"action": "modify", "args": {"tasks": tasks}} if tasks != args["tasks"] else None
        new = _with_footer(args)
        # modify args shallow-merge into the original, so send only the changed key (a full copy would clobber other hooks' edits)
        return {"action": "modify", "args": {k: v for k, v in new.items() if v != args.get(k)}} if new else None
    except Exception as exc:
        sys.stderr.write(f"[verification-checkpoint] footer error (no-op): {type(exc).__name__}: {exc}\n")
        return None


def _on_transform_tool_result(tool_name: str = "", result=None, session_id: str = "", turn_id: str = "", **_):
    if tool_name != "terminal" or not isinstance(result, str) or not enabled("HERMES_VERIFICATION_CHECKPOINT"):
        return None
    try:
        if _nudged.get(session_id) == turn_id or not _ABSENT.search(result[:4000]):  # absence shows up early
            return None
        _nudged[session_id] = turn_id
        sys.stderr.write(f"[verification-checkpoint] absence nudge session={session_id or '?'} turn={turn_id or '?'}\n")
        return nudge(result, _NUDGE)
    except Exception as exc:
        sys.stderr.write(f"[verification-checkpoint] guard error (no-op): {type(exc).__name__}: {exc}\n")
        return None


HOOKS = {"pre_tool_call": _on_pre_tool_call, "transform_tool_result": _on_transform_tool_result}
