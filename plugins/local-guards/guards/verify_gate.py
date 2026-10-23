"""verify_gate — "look before you change": a mutating tool call on a file/unit/branch/fact inside the managed tree is
blocked unless something in this session already read it (read_file/cat/grep/ls/git log/systemctl status/memory_search
or an earlier tool result that listed the path). Runs before write_gate's approval prompt and cannot be armed around.
Content edits (write_file, patch, sed -i, redirects) need the file itself read; existence ops (rm, mv, systemctl, git
branch -D) accept the file or a parent dir. Creating a path that does not exist yet needs no read.
HERMES_VERIFY_GATE=off disables. Every guard error is logged and lets the call through (optional tier; promote to
_REQUIRED after a clean 48 h). Blocks and errors go to logs/verify_gate.jsonl.
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
from ._common import HERMES_HOME, block, enabled

_ROOTS = (HERMES_HOME, os.path.expanduser("~/.config/systemd"), os.path.expanduser("~/hermes-dispatcher"))
_EXEMPT = tuple(os.path.join(HERMES_HOME, d) for d in ("cache", "logs", "image_cache", "run", "sessions"))
_LOG = os.path.join(HERMES_HOME, "logs", "verify_gate.jsonl")
_PATH = r"(?:~|\$HOME|/)[\w.@~$/+-]*|[\w.@+-]+(?:/[\w.@+-]+)+"  # absolute, ~, or relative with a slash
# verb -> (kind, regex capturing the path tokens that follow)
_CONTENT = re.compile(r"(?:\bsed\s+-i\S*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+|\btee\s+(?:-a\s+)?|(?<!\d)>{1,2}\s*|\btruncate\s+(?:-s\s*\S+\s+)?)(" + _PATH + ")")
_EXIST = re.compile(r"\b(?:rm|mv|rmdir|unlink|chmod|chown)\s+(?:-\S+\s+)*(" + _PATH + r")(?:\s+(" + _PATH + "))?")
_UNIT = re.compile(r"\bsystemctl\b(?:\s+--user)?\s+(?:restart|stop|disable|enable|mask|unmask|reset-failed)\s+(?:--\S+\s+)*([\w@.-]+)")
_BRANCH = re.compile(r"\bgit\b[^;&|\n]*\bbranch\b[^;&|\n]*\s-[dD]\s+(\S+)")
_NAME = r"((?:[\w$\"]+\.)*[\w$\"]+)"  # optionally schema-qualified/quoted
_SQL = (  # (regex, how); DELETE FROM is out of scope: the table name is trivially greppable
    (re.compile(r"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?[\w$\".]+\s+drop\s+column\s+(?:if\s+exists\s+)?" + _NAME, re.I), "ALTER DROP COLUMN"),
    *((re.compile(r"\bdrop\s+" + w + r"\s+(?:concurrently\s+)?(?:if\s+exists\s+)?" + _NAME, re.I), "DROP " + w.upper())
      for w in ("table", "index", "function", "view")),
    (re.compile(r"\btruncate\s+(?:table\s+)?(?:only\s+)?" + _NAME, re.I), "TRUNCATE"),
)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RESULT_PATH = re.compile(r"(?<![\w-])(/(?:root|home)/[\w.@+-]+(?:/[\w.@+-]+)+)")
_MAX_SESSIONS = 256
_seen: dict[str, dict] = {}  # session_id -> {"read": targets named in a call's args, "listed": paths seen only in results}


def _norm(tok: str) -> str:
    tok = tok.strip("'\"`,;:)")
    tok = os.path.expanduser(tok.replace("$HOME", "~"))
    return os.path.normpath(os.path.join(os.getcwd(), tok)) if not os.path.isabs(tok) else os.path.normpath(tok)


def _in_scope(p: str) -> bool:
    return p.startswith(_ROOTS) and not p.startswith(_EXEMPT) and not os.path.basename(p).startswith("tmp")


def _schema_targets(text: str) -> list[tuple[str, str, str]]:
    return [("schema", m.replace('"', "").rsplit(".", 1)[-1].lower(), how) for rx, how in _SQL for m in rx.findall(text)]


def _targets(tool_name: str, args: dict) -> list[tuple[str, str, str]]:
    """(kind, target, how) for every mutation this call performs."""
    out = []
    if tool_name in ("write_file", "patch") and args.get("path"):
        out.append(("content", _norm(str(args["path"])), tool_name))
    elif tool_name == "memory_remove" and args.get("fact_id"):
        out.append(("fact", str(args["fact_id"])[:8], tool_name))
    elif tool_name == "tool_call":
        for c in args.get("calls") or []:
            if isinstance(c, dict) and str(c.get("name", "")).endswith(("execute_sql", "apply_migration")):
                a = c.get("arguments") if isinstance(c.get("arguments"), dict) else {}
                out += _schema_targets(" ".join(str(a.get(k) or "") for k in ("query", "sql")))
    elif tool_name in ("terminal", "execute_code"):
        text = str(args.get("command") or args.get("code") or "")
        out += _schema_targets(text)
        out += [("content", _norm(m), "redirect/sed/tee") for m in _CONTENT.findall(text)]
        out += [("exist", _norm(m), "rm/mv") for pair in _EXIST.findall(text) for m in pair if m]
        out += [("unit", m, "systemctl") for m in _UNIT.findall(text)]
        out += [("branch", m, "git branch -D") for m in _BRANCH.findall(text)]
        for m in re.findall(r"open\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wa]", text):
            out.append(("content", _norm(m), "open(w)"))
    return out


def _verified(seen: dict, kind: str, target: str) -> bool:
    if kind in ("fact", "unit", "branch", "schema"):
        return target in seen["read"] or (kind != "schema" and target in seen["listed"])
    if kind == "content":
        return not os.path.exists(target) or target in seen["read"]  # new file: nothing to read first
    p = target
    while True:
        if p in seen["read"] or p in seen["listed"]:
            return True
        parent = os.path.dirname(p)
        if parent == p or not parent.startswith(_ROOTS):
            return False
        p = parent


def _log(event: str, **kw) -> None:
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        with open(_LOG, "a") as f:
            f.write(json.dumps({"ts": time.time(), "event": event, **kw}) + "\n")
    except Exception:
        pass


def _on_pre_tool_call(tool_name: str = "", args=None, session_id: str = "", **_):
    if not enabled("HERMES_VERIFY_GATE") or not session_id:
        return None
    try:
        a = args if isinstance(args, dict) else {}
        seen = _seen.get(session_id) or {"read": set(), "listed": set()}
        missing = [(k, t, how) for k, t, how in _targets(tool_name, a)
                   if (k in ("fact", "unit", "branch", "schema") or _in_scope(t)) and not _verified(seen, k, t)]
        if not missing:
            return None
        k, t, how = missing[0]
        _log("BLOCK", session=session_id, tool=tool_name, kind=k, target=t, how=how)
        sys.stderr.write(f"[verify-gate] BLOCKED tool={tool_name} {k}={t} via {how} session={session_id}\n")
        hint = {"content": f"read_file('{t}') or cat/grep it", "exist": f"ls -la '{t}' (or its directory)",
                "unit": f"systemctl --user status {t}", "branch": f"git branch --merged / git log -1 {t}",
                "fact": f"memory_search returning id {t}",
                "schema": f"grep -rn '{t}' scripts plugins — read every consumer before dropping it"}[k]
        return block(f"[VERIFY GATE] {tool_name} would change {t} ({how}) but nothing in this session has read it. "
                     f"Look first — {hint} — then retry. This gate has no arm/bypass.")
    except Exception as exc:  # optional tier: a guard bug lets the call through, visibly
        _log("ERROR", session=session_id, tool=tool_name, error=f"{type(exc).__name__}: {exc}")
        sys.stderr.write(f"[verify-gate] guard error (no-op): {type(exc).__name__}: {exc}\n")
        return None


def _on_post_tool_call(tool_name: str = "", args=None, result=None, session_id: str = "", **kw):
    """Every completed call marks its argument targets as seen; listing-style results mark the paths they contain."""
    if not enabled("HERMES_VERIFY_GATE") or not session_id:
        return None
    if kw.get("status") == "blocked":  # a blocked write must not count as the read that unblocks its retry
        return None
    try:
        if session_id not in _seen and len(_seen) >= _MAX_SESSIONS:
            _seen.pop(next(iter(_seen)))
        st = _seen.setdefault(session_id, {"read": set(), "listed": set()})
        seen, listed = st["read"], st["listed"]
        a = args if isinstance(args, dict) else {}
        for key in ("path", "file_path", "pattern", "command", "code", "query"):
            v = a.get(key)
            if isinstance(v, str):
                seen.update(_norm(m) for m in re.findall(_PATH, v) if "/" in m)
                seen.update(_UNIT.findall(v))
                seen.update(re.findall(r"\bgit\b[^;&|\n]*\b(?:log|show|cherry|branch)\b[^;&|\n]*?\s(?!-)([\w./-]+)", v))
        for k, t, _how in _targets(tool_name, a):
            seen.add(t)
        # a grep/search naming an identifier is the read that unblocks a schema drop of it
        look = (str(a.get("command") or a.get("code") or "") if re.search(r"\b(?:grep|rg)\b", str(a.get("command") or a.get("code") or ""))
                else " ".join(str(a.get(k) or "") for k in ("query", "pattern")) if tool_name in ("memory_search", "search_files") else "")
        seen.update(i.lower() for i in _IDENT.findall(look))
        text = result if isinstance(result, str) else json.dumps(result, default=str) if result is not None else ""
        if text:
            listed.update(_norm(m) for m in _RESULT_PATH.findall(text[:200_000]))
            if tool_name == "memory_search" or "memory_search" in str(a.get("calls", "")):
                listed.update(m[:8] for m in re.findall(r"\b([0-9a-f]{8})[0-9a-f]{0,24}\b", text))
            if "systemctl" in str(a.get("command", "")):
                listed.update(re.findall(r"([\w@.-]+\.(?:service|timer|socket))", text))
            if "git" in str(a.get("command", "")):
                listed.update(re.findall(r"^[ *+]*([\w./-]+)\s", text, re.M))
    except Exception as exc:
        _log("ERROR", session=session_id, tool=tool_name, error=f"{type(exc).__name__}: {exc}")
    return None


HOOKS = {"pre_tool_call": _on_pre_tool_call, "post_tool_call": _on_post_tool_call}
