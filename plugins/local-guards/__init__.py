"""local-guards — arm Hermes guards at gateway/CLI startup.

Every guards/*.py exporting HOOKS registers via ctx.register_hook (patches/ monkeypatches retired in P3).
A broken optional guard never blocks startup; a missing _REQUIRED guard does.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
import sys

logger = logging.getLogger(__name__)

# Module-level idempotency sentinel: prevents double-apply if register() is
# called more than once in the same process (e.g. force-reload).
_REGISTERED = False
# Guards that MUST arm; otherwise register() raises so the gateway refuses to start.
_REQUIRED: frozenset = frozenset({"write_gate"})


def _emit(msg: str) -> None:
    """Write one log line to stderr AND the logging system (both required)."""
    print(msg, file=sys.stderr, flush=True)
    logger.info("%s", msg)


def _chain(callbacks: list):
    """Hermes keeps only the first str a transform_tool_result hook returns and gives each hook the original
    result, so separate registrations would drop all but one nudge; run ours in sequence on one result."""
    def _transform(result=None, **kw):  # noqa: ANN001
        out = result
        for cb in callbacks:
            try:
                new = cb(result=out, **kw)
            except Exception as exc:  # noqa: BLE001
                _emit(f"[local-guards] transform error (skipped): {type(exc).__name__}: {exc}")
                continue
            out = new if isinstance(new, str) else out
        return None if out is result else out
    return _transform


def _arm_native(ctx, package) -> tuple[set[str], int]:  # noqa: ANN001
    """Register HOOKS of every public module in package; return (armed names, total)."""
    armed: set[str] = set()
    total = 0
    transforms: list = []
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_"):
            continue
        total += 1
        try:
            hooks = importlib.import_module(f"{package.__name__}.{info.name}").HOOKS
            for hook_name, callback in hooks.items():
                if hook_name == "transform_tool_result":
                    transforms.append(callback)
                else:
                    ctx.register_hook(hook_name, callback)
        except Exception as exc:  # noqa: BLE001
            _emit(f"[local-guards] {info.name} FAILED: {type(exc).__name__}: {exc}")
            continue
        _emit(f"[local-guards] {info.name} armed (native: {', '.join(hooks)})")
        armed.add(info.name)
    if transforms:
        ctx.register_hook("transform_tool_result", _chain(transforms))
    return armed, total


def register(ctx) -> None:  # noqa: ANN001
    """Plugin entry point; a no-op on second call in the same process."""
    global _REGISTERED
    if _REGISTERED:
        return
    from . import guards

    armed, total = _arm_native(ctx, guards)
    _emit(f"[local-guards] {len(armed)}/{total} armed")
    missing = _REQUIRED - armed
    if missing:
        raise RuntimeError(f"[local-guards] required guard(s) not armed: {sorted(missing)} — aborting startup")
    _REGISTERED = True  # only after every guard was processed and required ones armed
