"""write_gate — AGENTS.md WRITE GATE actions (terminal / write_file / patch / execute_code) go to Hermes's
native approval prompt (pre_tool_call {"action": "approve"}, hermes_cli/plugins.py _resolve_block_from_details):
approved runs, denied/timeout is blocked. With no human to answer (cron, -q, unattended, yolo, approvals off —
where the native gate auto-resolves) the call is blocked instead. An armed grant skips both: batch mode for
"go ahead with all" sessions; the agent arms it only after Andrew's greenlight (an honesty mechanism, not a
sandbox). A classifier error fails open (no-op).
CLI: python3 <this file> arm "<approval note>" [--ttl SECONDS] | disarm | status
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

try:
    from ._common import HERMES_HOME, block, enabled
except ImportError:  # run as a script for the CLI: guards/ is sys.path[0]
    from _common import HERMES_HOME, block, enabled

_GRANT_PATH = os.path.join(HERMES_HOME, ".write_gate_grant")
_AUDIT_LOG = os.path.join(HERMES_HOME, "references", "write-gate-audit.log")
_CLI = os.path.abspath(__file__)
# Arming is the greenlight step, so its own invocation + note is never classified; chained commands still are.
# any path spelling (~, relative, old patches/), but only as a python argv[1] so `sed -i … write_gate.py arm` stays gated
_ARM_CALL = re.compile(r"""\bpython3?\s+\S*write_gate\.py\s+arm\s+("[^"$`]*"|'[^']*')""")  # no $(...)/`...` in the note

# Not gated: memories/ (autonomous by doctrine) and references/ (except patch-guard/).
_GATED_PATH_LITERALS = (
    "/etc/",
    os.path.join(HERMES_HOME, "config.yaml"),
    os.path.join(HERMES_HOME, ".env"),
    os.path.join(HERMES_HOME, "AGENTS.md"),
    os.path.join(HERMES_HOME, "SOUL.md"),
    os.path.join(HERMES_HOME, "skills/"),
    os.path.join(HERMES_HOME, "profiles/"),
    os.path.join(HERMES_HOME, "cron/"),
    os.path.join(HERMES_HOME, "patches/"),
    # Code that runs inside the agent, and the gate's own grant + audit trail: a plain echo into the grant file
    # would arm the gate with no ARM audit line. The sanctioned arm call writes the grant from python and is
    # stripped from the command text by _ARM_CALL before classification, so it needs no exemption here.
    os.path.join(HERMES_HOME, "scripts/"),
    os.path.join(HERMES_HOME, "plugins/"),  # incl. the guards' own home, so the gate cannot be edited away unarmed
    os.path.join(HERMES_HOME, "vendor/"),
    _GRANT_PATH,
    _AUDIT_LOG,
    os.path.join(HERMES_HOME, "references/patch-guard/"),
    os.path.expanduser("~/.config/systemd/"),
    "/usr/local/lib/hermes-agent/",
)

_GATED_TERMINAL_PATTERNS = [
    re.compile(r"systemctl\s+(-\S+\s+)*(restart|stop|start|enable|disable)\b"),
    re.compile(r"docker\s+(compose\s+)?(restart|stop|start|rm|up|down)\b"),
    re.compile(r"\bapt(-get)?\s+(-\S+\s+)*(install|remove|purge)\b"),
    re.compile(r"\bpip3?\s+(install|uninstall)\b"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"kill\s+-(9|KILL|SIGKILL)\b"),
    re.compile(r"chmod\s+0?777\b"),
    re.compile(r"chown\s+-R\b"),
    # ── git guardrails (Andrew greenlit 2026-10-15) ──
    # force push (--force / -f / combined short flags / +refspec); --force-with-lease allowed
    re.compile(r"\bgit\b[^;&|\n]*\bpush\b[^;&|\n]*(\s--force(?!-with-lease)\b|\s-[a-zA-Z]*f[a-zA-Z]*(?=\s|$)|\s\+\S)"),
    # push to main/master by explicit refspec: `origin main`, `HEAD:main`, `:main`
    re.compile(r"\bgit\b[^;&|\n]*\bpush\b[^;&|\n]*\s(\S*:)?(refs/heads/)?(main|master)(?=\s|$|[;&|])"),
    re.compile(r"\bgit\b[^;&|\n]*\bpush\b[^;&|\n]*\s(--delete|-d)(?=\s)"),
    # hook bypass
    re.compile(r"\bgit\b[^;&|\n]*\b(commit|push|merge|rebase|am)\b[^;&|\n]*\s--no-verify\b"),
    re.compile(r"\bgit\b[^;&|\n]*\bcommit\b[^;&|\n]*\s-[a-zA-Z]*n[a-zA-Z]*(?=\s|$)"),
    re.compile(r"\bgit\b[^;&|\n]*\breset\b[^;&|\n]*\s--hard\b"),
    re.compile(r"\bgit\b[^;&|\n]*\bclean\b[^;&|\n]*\s(-[a-zA-Z]*f|--force\b)"),
    re.compile(r"\bgit\b[^;&|\n]*\bbranch\b[^;&|\n]*\s(-D|--delete\s+--force|-d\s+-f|--force\s+--delete)(?=\s|$)"),
    re.compile(r"\bscp\b"),
    re.compile(
        r"\bssh\b.*(\b(systemctl|docker\s+(restart|stop|start|rm|compose)"
        r"|sed\s+-i|apt|reboot|shutdown|>>|>)\b|(?<![\w-])(rm|tee)\s)"
    ),
]

# Short forms of the list above, caught inside shell/code text where paths are rarely absolute: files by bare name,
# dirs as ".hermes/<dir>/" (bare "scripts/" or "plugins/" would gate any repo's own dirs); the distinctive
# patch-guard / local-guards names stay bare so `cd ~/.hermes/plugins && ...` is still seen.
_HOME_NAME = os.path.basename(HERMES_HOME.rstrip("/"))
_GATED_PATH_LITERALS_FOR_REDIRECT = tuple(dict.fromkeys(
    ("/.env" if rel == ".env" else f"{_HOME_NAME}/{rel}/" if lit.endswith("/") else rel)
    if not (rel := os.path.relpath(lit, HERMES_HOME)).startswith("..") else lit
    for lit in _GATED_PATH_LITERALS)) + ("patch-guard", "local-guards")

_EXEC_WRITE_INDICATOR = re.compile(
    r"write_file\s*\("
    r"|\bpatch\s*\("                                # hermes patch tool (not .patch())
    r"|open\s*\([^)]{0,200}['\"][wa][b+]?['\"]"     # open(..., 'w'/'a'/'wb'/'ab')
    r"|\bshutil\."
    r"|\bos\.remove\s*\("
    r"|\bos\.unlink\s*\("
    r"|>>"
    r"|\btee\s"
    r"|\bsed\s+-i\b"
    r"|\bsudo\s"
)

# V4A patch mode carries its targets in the body, not in `path`.
# Same leniency as tools/patch_parser.py _OP_MARKERS, incl. Move (both sides).
_V4A_FILE = re.compile(r"^\*\*\*\s*(?:(?:Update|Add|Delete)\s+File:\s*(.+)|Move\s+File:\s*(.+?)\s*->\s*(.+))$", re.M)


def _has_write_redirect(cmd: str) -> bool:
    """True for a real shell write; fd-dups (2>&1) and /dev/null sinks do not count."""
    if re.search(r"\btee\s+|\bsed\s+-i\b|\bcp\s+|\bmv\s+|\bsudo\s|(?<![\w-])rm\s+", cmd):
        return True
    s = re.sub(r"\d*>>?\s*&\s*\d", " ", cmd)
    s = re.sub(r"(\d+|&)?>>?\s*/dev/null", " ", s)
    return bool(re.search(r">>?", s))


def _is_gated_path(file_path) -> bool:
    if not file_path or not isinstance(file_path, str):
        return False
    try:
        norm = os.path.realpath(os.path.expanduser(file_path))
    except ValueError:  # embedded NUL: unresolvable, so treat as gated rather than fail open
        return True
    if os.path.isdir(norm):
        norm += "/"
    return any(norm.startswith(os.path.realpath(gate).rstrip("/") + ("/" if gate.endswith("/") else ""))
               for gate in _GATED_PATH_LITERALS)


def _command_reason(cmd) -> str:
    if not cmd or not isinstance(cmd, str):
        return ""
    cmd = _ARM_CALL.sub(" ", cmd)
    for pat in _GATED_TERMINAL_PATTERNS:
        m = pat.search(cmd)
        if m:
            return m.group(0)[:80]
    if _has_write_redirect(cmd):
        for lit in _GATED_PATH_LITERALS_FOR_REDIRECT:
            if lit in cmd:
                return f"redirect to gated path ({lit})"
    return ""


def _execute_code_reason(code) -> str:
    """Gated command anywhere in the code, or a gated path plus a write-intent token."""
    if not code or not isinstance(code, str):
        return ""
    for pat in _GATED_TERMINAL_PATTERNS:
        m = pat.search(code)
        if m:
            return m.group(0)[:80]
    hit = next((lit for lit in (*_GATED_PATH_LITERALS_FOR_REDIRECT, *_GATED_PATH_LITERALS) if lit in code), None)
    if hit and _EXEC_WRITE_INDICATOR.search(code):
        return f"write to gated path ({hit})"
    return ""


def _reason(tool_name: str, args: dict) -> str:
    """Why this call is gated; "" if it is not."""
    if tool_name == "terminal":
        return _command_reason(args.get("command"))
    if tool_name == "execute_code":
        return _execute_code_reason(args.get("code"))
    if tool_name in ("write_file", "patch"):
        body = args.get("patch")
        targets = [args.get("path")] + ([p.strip() for m in _V4A_FILE.findall(body) for p in m if p] if isinstance(body, str) else [])
        hit = next((p for p in targets if _is_gated_path(p)), None)
        return os.path.basename(hit)[:60] if hit else ""
    return ""


def _read_grant() -> dict | None:
    """The grant if present, well-formed and unexpired; else None."""
    try:
        with open(_GRANT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        expires = data.get("expires") if isinstance(data, dict) else None
        return data if isinstance(expires, (int, float)) and time.time() < expires else None
    except Exception:  # missing or corrupt = not armed
        return None


def _audit_log(event: str, detail: str) -> None:
    try:
        os.makedirs(os.path.dirname(_AUDIT_LOG), exist_ok=True)
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {event} {detail}\n")
    except Exception:
        pass  # audit is best-effort; the block itself must still happen


def _human_can_answer() -> bool:
    """Would the native prompt reach a person? Where it would not, it auto-approves (yolo, approvals.mode off,
    cron_mode/unattended_mode approve) — exactly what this gate must not allow. Mirrors tools/approval.py
    _run_approval_gate's bypass order.
    shortcut: reads core privates (_yolo_active/_presence/_get_approval_mode); if a core update renames them this
    returns False and every gated call blocks instead of prompting; switch to a public helper if core grows one."""
    try:
        from tools import approval, approval_context
        if approval._yolo_active() or approval_context._get_approval_mode() == "off":
            return False
        _, is_cli, is_gateway, is_ask = approval._presence()
        return bool(is_cli or is_gateway or is_ask)
    except Exception:
        return False


def _on_pre_tool_call(tool_name: str = "", args=None, session_id: str = "", **_):
    if not enabled("HERMES_WRITE_GATE"):
        return None
    try:
        reason = _reason(tool_name, args if isinstance(args, dict) else {})
    except Exception as exc:  # a classifier bug must not veto every tool call
        sys.stderr.write(f"[write-gate] guard error (no-op): {type(exc).__name__}: {exc}\n")
        return None
    if not reason:
        return None
    grant = _read_grant()
    if grant:
        sys.stderr.write(f"[write-gate] armed-pass tool={tool_name} detail={reason} note={grant.get('note', '?')}\n")
        return None
    if _human_can_answer():
        sys.stderr.write(f"[write-gate] APPROVE-REQ tool={tool_name} detail={reason} session={session_id or '?'}\n")
        _audit_log("APPROVE-REQ", f"{tool_name}: {reason}")
        a = args if isinstance(args, dict) else {}
        # rule_key hashes the exact args: core persists [a]lways in command_allowlist with no expiry, so it may only
        # ever re-approve this identical command/write, never a class like "any SKILL.md".
        digest = hashlib.sha256(json.dumps(a, sort_keys=True, default=str).encode()).hexdigest()[:16]
        target = str(a.get("command") or a.get("code") or a.get("path") or a.get("patch") or "")[:200]
        return {"action": "approve", "rule_key": f"write_gate:{tool_name}:{digest}",
                "message": f"[WRITE GATE] {tool_name}: {reason} (AGENTS.md gated action)\n{target}"}
    sys.stderr.write(f"[write-gate] BLOCKED tool={tool_name} detail={reason} session={session_id or '?'}\n")
    _audit_log("BLOCK", f"{tool_name}: {reason}")
    return block(
        f"[WRITE GATE] Blocked gated action: {tool_name}: {reason}. "
        f"No human is present to approve it (cron/unattended/yolo/approvals off), and it needs Andrew's greenlight "
        f"per AGENTS.md. Only if he already greenlit this batch: python3 {_CLI} arm \"<approval note>\" --ttl 600 "
        f"and retry; otherwise present what/risks/rollback and wait."
    )


HOOKS = {"pre_tool_call": _on_pre_tool_call}


def _cli_arm(note: str, ttl: int = 600) -> None:
    if not note or not note.strip():
        sys.exit("ERROR: note is required (describe what you're approving)")
    ttl = max(1, min(ttl, 3600))
    now = time.time()
    os.makedirs(os.path.dirname(_GRANT_PATH), exist_ok=True)
    with open(_GRANT_PATH, "w", encoding="utf-8") as f:
        json.dump({"armed_at": now, "expires": now + ttl, "note": note.strip()}, f, indent=2)
    _audit_log("ARM", f"ttl={ttl} note={note.strip()[:100]}")
    print(f"🔓 Write gate ARMED for {ttl}s: {note.strip()}")


def _cli_disarm() -> None:
    if os.path.exists(_GRANT_PATH):
        os.remove(_GRANT_PATH)
        _audit_log("DISARM", "manual")
        print("🔒 Write gate DISARMED")
    else:
        print("🔒 Write gate already disarmed (no grant file)")


def _cli_status() -> None:
    grant = _read_grant()
    if grant is None:
        print(f"🔒 Write gate: DISARMED (no valid grant)\n   Enabled: {enabled('HERMES_WRITE_GATE')}")
    else:
        print(f"🔓 Write gate: ARMED ({max(0, grant['expires'] - time.time()):.0f}s remaining)\n"
              f"   Note: {grant.get('note', '?')}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Hermes write gate CLI")
    sub = parser.add_subparsers(dest="cmd")
    arm_p = sub.add_parser("arm", help="Arm the gate")
    arm_p.add_argument("note", help="Approval note (what are you greenlighting?)")
    arm_p.add_argument("--ttl", type=int, default=600, help="Seconds (default 600, max 3600)")
    sub.add_parser("disarm", help="Disarm the gate")
    sub.add_parser("status", help="Show gate status")
    a = parser.parse_args()
    if a.cmd == "arm":
        _cli_arm(a.note, a.ttl)
    elif a.cmd == "disarm":
        _cli_disarm()
    elif a.cmd == "status":
        _cli_status()
    else:
        parser.print_help()
