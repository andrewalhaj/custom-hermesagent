"""routing — the one AGENTS.md routing nudge for the orchestrator (replaces the kanban_checkpoint, kanban_phase,
delegation_nudge, task_routing and delegation_checkpoint patches). Appended to a tool result: the first one of
a turn whose user message is multi-part/phased, and again every WRITE_STEP writes / TERMINAL_STEP terminal calls
without a routing action. Advisory; every error returns None.
"""
from __future__ import annotations

import os
import re
import sys

from ._common import NOTIFICATION, enabled, is_multipart, is_routing, is_worker, nudge, user_text

WRITE_STEP = int(os.environ.get("HERMES_ROUTING_WRITES", "6"))
TERMINAL_STEP = int(os.environ.get("HERMES_ROUTING_TERMINAL", "30"))

_INLINE = re.compile(r"\binline\b|just do it|do it now|\bjust go\b|go ahead|\bproceed\b|quick fix"
                     r"|(?:skip|no|don't use) kanban", re.IGNORECASE)
_PHASE = re.compile(r"\b(?:phases?|stages?|step by step|multi.?step|rollout|migrat\w*|refactor|overhaul|rebuild"
                    r"|redesign|rework|rewrite|rearchitect|from scratch|ground up)\b", re.IGNORECASE)
_SEQUENCE = re.compile(r"\b(?:first|then|next|after that|finally|lastly|subsequently)\b", re.IGNORECASE)
_NUDGE = (
    "[Routing: {why}. HERMES_KANBAN_TASK is empty, so you are the board orchestrator. Partition the whole "
    "surface first, then route: 2+ independent chunks -> delegate_task fan-out now (no proposal needed); "
    "cross-session or multi-hour work -> kanban_create cards (kanban_list(status='running') first, never stack "
    "one worker); a genuinely sequential chain -> say why in one line and proceed.]"
)

# session_id -> {"turn", "why", "writes", "terminal"}; looked up by turn_id after compression rotates the id.
# shortcut: entries live for the process (a few ints per session); prune if the gateway ever runs for months.
_sessions: dict[str, dict] = {}


def _signal(text: str) -> str:
    """Why this user message is board-shaped, or "" if it is not (or the user asked for inline)."""
    if len(text.strip()) < 20 or NOTIFICATION.search(text) or _INLINE.search(text):
        return ""
    if re.search(r"\bplan to\b", text, re.IGNORECASE):  # AGENTS.md: "plan to X and Y" = explicit fan-out signal
        return 'the message says "plan to"'
    if len(re.findall(r"https?://\S+", text)) >= 2:
        return "the message has 2+ URLs to work through"
    if is_multipart(text) or re.search(r"\bvs\.?\b|\bversus\b", text, re.IGNORECASE):
        return "the message looks multi-part"
    if _PHASE.search(text) or len(_SEQUENCE.findall(text)) >= 2:
        return "the message looks multi-phase"
    return ""


def _on_pre_llm_call(session_id: str = "", turn_id: str = "", user_message=None, platform: str = "", **_):
    # Workers and delegate children are already-routed chunks; only the orchestrator gets state (and so nudges).
    if not session_id or not enabled("HERMES_ROUTING") or is_worker() or platform == "subagent":
        return None
    try:
        st = _sessions.setdefault(session_id, {"writes": 0, "terminal": 0})
        st.update(turn=turn_id, why=_signal(user_text(user_message)))
    except Exception as exc:
        sys.stderr.write(f"[routing] turn error (no-op): {type(exc).__name__}: {exc}\n")
    return None


def _on_transform_tool_result(tool_name: str = "", args=None, result=None, session_id: str = "",
                              turn_id: str = "", **_):
    if not enabled("HERMES_ROUTING"):
        return None
    try:
        st = _sessions.get(session_id) or next(
            (s for s in _sessions.values() if turn_id and s.get("turn") == turn_id), None)
        if st is None:
            return None
        _sessions[session_id] = st  # follow compression's new session id
        if is_routing(tool_name, args):
            st.update(why="", writes=0, terminal=0)
            return None
        st["writes"] += tool_name in ("write_file", "patch")
        st["terminal"] += tool_name == "terminal"
        why = st["why"] or (
            f"{st['writes']} file writes / {st['terminal']} terminal calls since the last delegate/kanban action"
            if st["writes"] >= WRITE_STEP or st["terminal"] >= TERMINAL_STEP else "")
        if not why:
            return None
        out = nudge(result, _NUDGE.format(why=why))
        if out is not None:
            st.update(why="", writes=0, terminal=0)
            sys.stderr.write(f"[routing] nudged session={session_id or '?'} tool={tool_name}: {why}\n")
        return out
    except Exception as exc:
        sys.stderr.write(f"[routing] guard error (no-op): {type(exc).__name__}: {exc}\n")
        return None


HOOKS = {"pre_llm_call": _on_pre_llm_call, "transform_tool_result": _on_transform_tool_result}
