"""fact_gate — "look before you answer": a user question that asks for a checkable external fact (price, licence
terms, version, release date, spec, limit, flag, "does X support Y") must run at least one lookup tool this turn.
Every LLM call in the turn carries a reminder until one has run; a final reply that still has none is withheld.
Which tool counts is deliberately broad (web, docs, memory, files, shell): the failure this closes is answering
from training memory with zero tools, not picking the wrong one. Off for subagents/workers and gateway notifications.
HERMES_FACT_GATE=off disables; =log detects and journals only. Every error is a no-op. Events -> logs/fact_gate.jsonl.
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
from ._common import HERMES_HOME, NOTIFICATION, enabled, is_worker, user_text

_LOG = os.path.join(HERMES_HOME, "logs", "fact_gate.jsonl")
_FACT = re.compile(
    r"\b(pric\w*|cost\w*|licen[cs]\w*|subscription|version|release[sd]?|changelog|eol|end[- ]of[- ]life|deprecat\w*"
    r"|support(?:s|ed)?\b|compatib\w*|requirement\w*|minimum|maximum|limit\w*|quota|spec\w*|benchmark\w*|latency"
    r"|cve[- ]?\d*|vulnerab\w*|patch(?:ed)?\s+(?:in|for)|default\s+(?:port|value|setting)|syntax|flag|cmdlet|command\s+(?:for|to)"
    r"|how\s+(?:many|much|long|big|fast)|when\s+(?:did|was|does|will)|what(?:'s|\s+is)\s+the\s+(?:latest|current|newest|max|min)"
    r"|is\s+there\s+a|does\s+\S+\s+(?:support|work|have|include|allow)|can\s+\S+\s+(?:run|handle|do)|which\s+(?:version|edition|sku|tier))",
    re.IGNORECASE)
_SELF = re.compile(r"\b(our|we|us|my|hermes|gateway|dispatcher|this\s+(?:box|machine|system|repo)|you)\b", re.IGNORECASE)
_LOOKUP = ("web_search", "web_extract", "memory_search", "chat_history_lookup", "session_search", "read_file",
           "search_files", "terminal", "execute_code", "browser", "mcp__", "tool_call", "skill_view", "vision_analyze")
_REMINDER = ("[fact-gate] This asks for a checkable fact. Look it up before answering (web_search/web_extract or vendor "
             "docs for external facts; memory_search/read_file/terminal for our own system) and cite what you used. "
             "The reply is withheld until at least one lookup has run this turn.")
_WITHHELD = "[fact-gate] Reply withheld: this asked for a checkable fact and no lookup ran. Ask again and I will look first."
_MAX_SESSIONS = 256
_sessions: dict[str, dict] = {}  # session_id -> {"turn", "armed", "looked"}


def _on() -> bool:
    return enabled("HERMES_FACT_GATE")


def _log_only() -> bool:
    return os.environ.get("HERMES_FACT_GATE", "").strip().lower() == "log"


def is_fact_question(text: str) -> bool:
    t = text.strip()
    if len(t) < 12 or t.startswith("[") or NOTIFICATION.search(t):
        return False
    asks = "?" in t or re.match(r"(?i)(what|which|how|when|does|is|are|can|do|did|will|should|tell me|find|look up|check)\b", t)
    return bool(asks and _FACT.search(t))


def _state(session_id: str, turn_id: str):
    return _sessions.get(session_id) or next((s for s in _sessions.values() if turn_id and s["turn"] == turn_id), None)


def _log(event: str, **kw) -> None:
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        with open(_LOG, "a") as f:
            f.write(json.dumps({"ts": time.time(), "event": event, **kw}) + "\n")
    except Exception:
        pass


def _on_pre_llm_call(session_id: str = "", turn_id: str = "", user_message=None, platform: str = "", **_):
    if not session_id or not turn_id or not _on() or is_worker() or platform == "subagent":
        return None
    try:
        st = _state(session_id, turn_id)
        if st is None or st["turn"] != turn_id:
            text = user_text(user_message)
            st = {"turn": turn_id, "looked": False, "armed": is_fact_question(text)}
            if st["armed"]:
                _log("ARMED", session=session_id, turn=turn_id, self_ref=bool(_SELF.search(text)), q=text[:160])
            if len(_sessions) >= _MAX_SESSIONS:
                _sessions.pop(next(iter(_sessions)))
        _sessions[session_id] = st
        return _REMINDER if st["armed"] and not st["looked"] and not _log_only() else None
    except Exception as exc:
        sys.stderr.write(f"[fact-gate] turn error (no-op): {type(exc).__name__}: {exc}\n")
        return None


def _on_pre_tool_call(tool_name: str = "", args=None, session_id: str = "", turn_id: str = "", **_):
    if not _on():
        return None
    try:
        st = _state(session_id, turn_id)
        if st is not None and tool_name.startswith(_LOOKUP):
            st["looked"] = True
    except Exception as exc:
        sys.stderr.write(f"[fact-gate] tool error (no-op): {type(exc).__name__}: {exc}\n")
    return None


def _on_transform_llm_output(session_id: str = "", turn_id: str = "", **_):
    if not _on():
        return None
    try:
        st = _state(session_id, turn_id)
        if st is None or st["turn"] != turn_id or not st["armed"] or st["looked"]:
            return None
        _log("WITHHELD", session=session_id, turn=turn_id, mode="log" if _log_only() else "enforce")
        sys.stderr.write(f"[fact-gate] withheld reply session={session_id} turn={turn_id}\n")
        return None if _log_only() else _WITHHELD
    except Exception as exc:
        sys.stderr.write(f"[fact-gate] output error (no-op): {type(exc).__name__}: {exc}\n")
        return None


HOOKS = {"pre_llm_call": _on_pre_llm_call, "pre_tool_call": _on_pre_tool_call,
         "transform_llm_output": _on_transform_llm_output}
