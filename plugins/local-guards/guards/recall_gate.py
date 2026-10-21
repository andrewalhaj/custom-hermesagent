"""recall_gate — AGENTS.md PRE-TASK RECALL GATE. On a recall-shaped user message ("what did we decide", "catch me up")
the turn must run BOTH memory_search and session_search/chat_history_lookup: every LLM call in the turn gets a
reminder until both have run, and a final reply that still lacks one is replaced with a withheld notice.
Opt-in: off unless HERMES_RECALL_GATE is set (and not an off-word). HERMES_RECALL_GATE=log detects and logs only
(never injects or withholds). Text starting with "[" (gateway notifications) is never a human message. Every error is a no-op.
"""
from __future__ import annotations

import os
import re
import sys

from ._common import enabled, is_worker, user_text

try:  # single source lives in scripts/knowledge; the copy below is the fallback when scripts/ is not on sys.path
    from knowledge.recall_shape import is_recall as _is_recall
except ImportError:
    _RECALL = re.compile(  # keep in sync with scripts/knowledge/recall_shape.py (narrow subset)
        r"what\s+changed|what\s+did\s+we\s+(?:change|decide|do)\b|where\s+were\s+we|remind\s+me(?!\s+to\b)"
        r"|catch\s+me\s+up|\brecap\b|\blast\s+(?:night|time|session)\b", re.IGNORECASE)
    _OVERRIDE = re.compile(r"NO SESSION SEARCHES|no lookup", re.IGNORECASE)  # Andrew's explicit skip

    def _is_recall(text):
        return bool(_RECALL.search(text)) and not _OVERRIDE.search(text)
_REMINDER = ("[recall-gate] This is a recall question. Before answering, run BOTH memory_search AND "
             "session_search (chat_history_lookup); answer from the union. The reply will be withheld until "
             "both have run this turn. Use short keyword queries (2-4 words) for session_search; FTS5 ANDs every term.")
_WITHHELD = ("[recall-gate] Reply withheld: memory_search ran = {m}, session_search ran = {s}. "
             "Ask again and I will look it up first.")
_MAX_SESSIONS = 256

# session_id -> {"turn", "armed", "mem", "ses"}; looked up by turn_id after compression rotates the id.
_sessions: dict[str, dict] = {}


def _on() -> bool:
    return "HERMES_RECALL_GATE" in os.environ and enabled("HERMES_RECALL_GATE")


def _log_only() -> bool:
    return os.environ.get("HERMES_RECALL_GATE", "").strip().lower() == "log"


def _state(session_id: str, turn_id: str):
    return _sessions.get(session_id) or next(
        (s for s in _sessions.values() if turn_id and s["turn"] == turn_id), None)


def _satisfied(st: dict) -> bool:
    return st["mem"] and st["ses"]


def _on_pre_llm_call(session_id: str = "", turn_id: str = "", user_message=None, platform: str = "", **_):
    if not session_id or not turn_id or not _on() or is_worker() or platform == "subagent":
        return None
    try:
        st = _state(session_id, turn_id)
        if st is None or st["turn"] != turn_id:  # first LLM call of a new turn
            text = user_text(user_message).lstrip()
            st = {"turn": turn_id, "mem": False, "ses": False,
                  "armed": not text.startswith("[") and _is_recall(text)}
            if len(_sessions) >= _MAX_SESSIONS:
                _sessions.pop(next(iter(_sessions)))
        _sessions[session_id] = st  # follow compression's new session id
        return _REMINDER if st["armed"] and not _satisfied(st) and not _log_only() else None
    except Exception as exc:
        sys.stderr.write(f"[recall-gate] turn error (no-op): {type(exc).__name__}: {exc}\n")
        return None


def _on_pre_tool_call(tool_name: str = "", args=None, session_id: str = "", turn_id: str = "", **_):
    if not _on():
        return None
    try:
        st = _state(session_id, turn_id)
        if st is not None:
            # Deferred tools (chat_history_lookup) arrive as tool_call{calls:[{name}]}; match on the names inside.
            names = tool_name + " " + " ".join(str(c.get("name", "")) for c in (args or {}).get("calls", []) if isinstance(c, dict))
            st["mem"] |= "memory_search" in names
            st["ses"] |= "session_search" in names or "chat_history_lookup" in names
    except Exception as exc:
        sys.stderr.write(f"[recall-gate] tool error (no-op): {type(exc).__name__}: {exc}\n")
    return None


def _on_transform_llm_output(session_id: str = "", turn_id: str = "", **_):
    if not _on():
        return None
    try:
        st = _state(session_id, turn_id)
        if st is None or st["turn"] != turn_id or not st["armed"] or _satisfied(st):
            return None
        log = _log_only()
        sys.stderr.write(f"[recall-gate] withheld reply session={session_id or '?'} turn={turn_id}{' mode=log' if log else ''}\n")
        if log:
            return None
        return _WITHHELD.format(m="yes" if st["mem"] else "no", s="yes" if st["ses"] else "no")
    except Exception as exc:
        sys.stderr.write(f"[recall-gate] output error (no-op): {type(exc).__name__}: {exc}\n")
        return None


HOOKS = {"pre_llm_call": _on_pre_llm_call, "pre_tool_call": _on_pre_tool_call,
         "transform_llm_output": _on_transform_llm_output}
