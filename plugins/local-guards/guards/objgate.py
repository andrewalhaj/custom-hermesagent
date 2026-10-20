"""objgate — AGENTS.md WHOLE-OBJECTIVE ROUTING GATE. An orchestrator turn that reads like "inventory /
surface everything" and is multi-part arms the gate for its session; write_file/patch are then blocked
until run/inventory.json for that turn resolves routing, or a routing tool (delegate/kanban) is used.
Fails open: Hermes vetoes on a raising pre_tool_call hook, so every error returns None.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

from ._common import HERMES_HOME, NOTIFICATION, block, enabled, is_multipart, is_routing, is_worker, user_text

INVENTORY_PATH = os.path.join(HERMES_HOME, "run", "inventory.json")
K = int(os.environ.get("HERMES_OBJGATE_K", "3"))

_INTENT = re.compile(
    r"inventory|surface|bring\s+.{0,20}\s+in|every\s+(dataset|field|gap|table|column)"
    r"|what.{0,5}(missing|unsurfaced|not\s+surfaced)|populate|fill\s+in",
    re.IGNORECASE,
)
# session_id and turn_id -> turn_hash of the objective turn; turn_id survives compression's session-id rotation.
_armed: dict[str, str] = {}


def _block_reason(args: dict, turn_hash: str) -> str:
    """Why this write is blocked under an armed objective; "" to let it through."""
    if str(args.get("path") or "").endswith("inventory.json"):
        return ""
    try:
        with open(INVENTORY_PATH, encoding="utf-8") as f:
            inv = json.load(f)
    except Exception:  # missing or unparseable
        return (f"produce inventory.json first. Write {INVENTORY_PATH} with turn_hash=\"{turn_hash}\", "
                "gaps[], chunks[], chunk_count, routing, reason.")
    stored = inv.get("turn_hash", "")  # a non-dict inventory raises here -> caller fails open
    if not stored:
        return f"inventory.json has no turn_hash (leftover from a previous objective); set turn_hash=\"{turn_hash}\"."
    if stored != turn_hash:
        return f"inventory.json is from a previous objective (hash mismatch); update it with turn_hash=\"{turn_hash}\"."
    if str(inv.get("routing", "")).strip().lower() == "inline" and str(inv.get("reason", "")).strip():
        return ""
    count = int(inv.get("chunk_count", 0))
    if count > K:
        return (f"chunk_count={count} > K={K}. Fan out via delegate_task (one per chunk) then integrate, "
                "or set routing=\"inline\" with a non-empty reason.")
    return ""


def _on_pre_llm_call(session_id: str = "", turn_id: str = "", user_message=None, platform: str = "", **_):
    # A delegated child is a chunk of an already-routed objective, like a kanban worker.
    if not enabled("HERMES_OBJGATE") or is_worker() or platform == "subagent":
        return None
    try:
        text = user_text(user_message)
        if not NOTIFICATION.search(text) and _INTENT.search(text) and is_multipart(text):
            h = hashlib.sha1(f"{turn_id}:{text[:200]}".encode()).hexdigest()[:12]
            _armed.update({k: h for k in (session_id, turn_id) if k})
            sys.stderr.write(f"[objgate] ARMED session={session_id or '?'} turn={turn_id or '?'} hash={h}\n")
    except Exception as exc:
        sys.stderr.write(f"[objgate] arm error (no-op): {type(exc).__name__}: {exc}\n")
    return None


def _on_pre_tool_call(tool_name: str = "", args=None, session_id: str = "", turn_id: str = "", **_):
    # turn_id is "<session_id at turn start>:<task>:<hex>", so its prefix still finds a gate armed before
    # turn-start compaction rotated the session. shortcut: relay turn ids use another format; not covered.
    try:
        turn_hash = _armed.get(session_id) or _armed.get(turn_id) or _armed.get(turn_id.split(":", 1)[0])
    except Exception as exc:  # e.g. turn_id=None
        sys.stderr.write(f"[objgate] guard error (no-op): {type(exc).__name__}: {exc}\n")
        return None
    if not turn_hash or not enabled("HERMES_OBJGATE"):
        return None
    if session_id:
        _armed[session_id] = turn_hash  # follow compression's new session id into the next turn
    try:
        args = args if isinstance(args, dict) else {}
        # shortcut: a write ahead of the routing call in one parallel batch is still blocked; batch-scan if that bites.
        if is_routing(tool_name, args):
            for k in [k for k, v in list(_armed.items()) if v == turn_hash]:  # incl. the pre-compression session id
                _armed.pop(k, None)  # parallel routing calls may race
            sys.stderr.write(f"[objgate] cleared session={session_id or '?'} by {tool_name}\n")
            return None
        reason = _block_reason(args, turn_hash) if tool_name in ("write_file", "patch") else ""
    except Exception as exc:
        sys.stderr.write(f"[objgate] guard error (no-op): {type(exc).__name__}: {exc}\n")
        return None
    if not reason:
        return None
    sys.stderr.write(f"[objgate] BLOCKED tool={tool_name} session={session_id or '?'} hash={turn_hash}\n")
    return block(f"[Whole-objective gate] Write blocked: {reason}")


HOOKS = {"pre_llm_call": _on_pre_llm_call, "pre_tool_call": _on_pre_tool_call}
