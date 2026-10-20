"""Helpers shared by native-hook guards (pre_tool_call blocks, transform_tool_result nudges).

Kept to what more than one guard needs; per-guard logic stays in its own module.
"""
from __future__ import annotations

import os
import re

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))

# Gateway-injected turns no human wrote; a long subagent result scores as multi-part and armed the old guards.
NOTIFICATION = re.compile(r"^\s*\[ASYNC DELEGATION|\[INTERNAL NOTIFICATION")
_ROUTING_TOOLS = frozenset({"delegate_task", "kanban_create", "kanban_list", "kanban_show", "kanban_complete",
                            "kanban_block", "kanban_comment", "kanban_unblock", "kanban_link", "kanban_heartbeat"})
_MULTIPART_KEYWORDS = (
    "also", "additionally", "as well", "while you're at it", "while you are at it", "another thing",
    "few things", "couple of things", "couple things", "few questions", "couple of questions",
    "on top of that", "in addition", "plus,", "second,", "secondly,", "third,", "thirdly,", "lastly,",
    "firstly,", "first,", "finally,", "and then", "and make sure", "and check", "and verify", "and confirm",
)
_IMPERATIVE_VERBS = frozenset(
    "check make verify confirm set get update add remove fix look find show tell list ensure review audit "
    "run test build deploy install configure enable disable create delete fetch pull push move copy rename "
    "restart start stop reload refresh sync backup restore".split()
)


def enabled(env_var: str) -> bool:
    """Guards default ON; same off-words the patches/ guards accepted."""
    return os.environ.get(env_var, "on").strip().lower() not in {"off", "0", "false", "no", "disabled"}


def is_worker() -> bool:
    """A spawned kanban worker has HERMES_KANBAN_TASK set; the orchestrator does not."""
    return bool(os.environ.get("HERMES_KANBAN_TASK", "").strip())


def block(message: str) -> dict:
    """pre_tool_call veto; Hermes ignores a block without a message, so require one."""
    if not message:
        raise ValueError("block() needs a non-empty message")
    return {"action": "block", "message": message}


def nudge(result, text: str):
    """transform_tool_result return: the result with text appended, or None for non-str results
    (None leaves the result untouched; Hermes takes the first str return)."""
    return f"{result}\n\n{text}" if isinstance(result, str) else None


def user_text(user_message) -> str:
    """pre_llm_call user_message (str or content-part list) as plain text."""
    if isinstance(user_message, list):
        return " ".join(p.get("text", "") for p in user_message if isinstance(p, dict) and p.get("type") == "text")
    return str(user_message or "")


def is_multipart(text: str) -> bool:
    """Any one of the patch-era signals (score >= 1 there): lists, 2+ questions, keywords, 2+ imperatives, long 'and' chains."""
    tl = text.lower()
    return (
        len(re.findall(r"(?:^|\n)\s*\d+[.)]\s+\S", text)) >= 2
        or len(re.findall(r"(?:^|\n)\s*[-*•]\s+\S", text)) >= 2
        or text.count("?") >= 2
        or any(kw in tl for kw in _MULTIPART_KEYWORDS)
        or sum(1 for s in re.split(r"[.!?\n]\s*", text) if s.split() and s.split()[0].lower().rstrip("s") in _IMPERATIVE_VERBS) >= 2
        or (len(text) > 150 and tl.count(" and ") >= 2)
    )


def is_routing(tool_name: str, args) -> bool:
    """A delegate/kanban tool, or a terminal `kanban create|swarm`: the agent is routing, not working inline."""
    return tool_name in _ROUTING_TOOLS or (
        tool_name == "terminal" and isinstance(args, dict)
        and bool(re.search(r"kanban\s+(swarm|create)", str(args.get("command", "")))))
