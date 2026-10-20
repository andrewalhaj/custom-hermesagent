"""memory_cap native guard: one line of turn context, once per crossing, when a hot memory store is
over its offload line (MEMORY.md ≥80%, USER.md ≥95% — Andrew 2026-10-19). Silent otherwise, never
repeats while the store stays over, re-arms after it drops back under. Replaces the reverted
every-tool-call memory_checkpoint nag with the "block or stay silent" shape.
"""
from __future__ import annotations

import os
import sys

from ._common import enabled, is_worker

THRESHOLDS = {"memory": 0.80, "user": 0.95}
# shortcut: one flag per store for the process, not per session — one human, one pair of files.
_fired: dict[str, bool] = {}


def _usage() -> dict[str, tuple[int, int]]:
    """{target: (chars, limit)} read from disk with the configured caps (the store the header shows)."""
    from tools.memory_tool import load_on_disk_store  # upstream; present wherever this plugin loads
    store = load_on_disk_store()
    return {"memory": (store._char_count("memory"), store.memory_char_limit),
            "user": (store._char_count("user"), store.user_char_limit)}


def check(usage: dict[str, tuple[int, int]]) -> str:
    """The nudge line for stores that just crossed their line, "" if none did (pure; tests call this)."""
    lines = []
    for target, (chars, limit) in usage.items():
        over = limit > 0 and chars / limit >= THRESHOLDS[target]
        if over and not _fired.get(target):
            lines.append(f"{'MEMORY.md' if target == 'memory' else 'USER.md'} at {chars * 100 // limit}% "
                         f"({chars:,}/{limit:,}): offload before continuing (memory_add → memory_search verify → trim).")
        _fired[target] = over
    return " ".join(lines)


def _on_pre_llm_call(session_id: str = "", platform: str = "", **_):
    if not session_id or not enabled("HERMES_MEMORY_CAP") or is_worker() or platform == "subagent":
        return None
    try:
        line = check(_usage())
    except Exception as exc:  # noqa: BLE001 — a guard that cannot read the store stays silent
        sys.stderr.write(f"[memory_cap] no-op: {type(exc).__name__}: {exc}\n")
        return None
    return {"context": f"[memory_cap] {line}"} if line else None


HOOKS = {"pre_llm_call": _on_pre_llm_call}
