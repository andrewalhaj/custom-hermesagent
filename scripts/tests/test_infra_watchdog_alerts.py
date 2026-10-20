"""infra_watchdog.main(): the alert text, order and exit code, against a fully broken fake host.

Run: python -m pytest scripts/tests/test_infra_watchdog_alerts.py -v
"""
import json, os, re, sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import infra_watchdog  # noqa: E402


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _broken_host(tmp_path, monkeypatch):
    home = tmp_path / "home"
    now = datetime.now()
    stamp = f"{now:%Y-%m-%d %H:%M:%S},1"
    _write(home / "cron/jobs.json", json.dumps({"jobs": [
        {"name": "Backup", "last_status": "error", "last_error": "Script timed out after 600s"}]}))
    _write(home / "cache/delegation/live/a/task-1.log", "401 authentication\nfinal | status=failed\n")
    _write(home / "config.yaml", "memory:\n  memory_char_limit: 100\n  user_char_limit: 100\n")
    _write(home / "memories/MEMORY.md", "x" * 99)
    _write(home / "profiles/sister/config.yaml", "memory: {}\n")
    _write(home / "profiles/sister/memories/USER.md", "y" * 2200)
    _write(home / "logs/gateway.log", f"{stamp} WARN Primary provider auth failed\n")
    _write(home / "logs/agent.log", f"{stamp} WARNING prefetch timed out\n" * 3)
    _write(home / "plugins/local-guards/guards/alpha.py", "")

    def fake_sh(cmd, timeout=12):
        if "is-active hermes-gateway" in cmd: return 0, "inactive", ""
        if "is-active hermes-dispatcher" in cmd: return 3, "failed", ""
        if "is-active hermes-kb-daemon" in cmd: return 3, "failed", ""
        if "restart hermes-kb-daemon" in cmd: return 0, "", ""
        if cmd.startswith("ssh"): return 255, "", "no route"
        if cmd.startswith("df -P"): return 0, "95", ""
        if "MainPID" in cmd: return 0, str(os.getpid()), ""
        if "journalctl" in cmd: return 0, "", ""
        raise AssertionError(cmd)

    monkeypatch.setattr(infra_watchdog, "HERMES", str(home))
    monkeypatch.setattr(infra_watchdog, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(infra_watchdog, "sh", fake_sh)
    monkeypatch.setattr(infra_watchdog, "http_code", lambda url, timeout=8: 500)
    monkeypatch.setattr(infra_watchdog.time, "sleep", lambda s: None)
    monkeypatch.setenv("VPS_A_HOST", "203.0.113.9")
    monkeypatch.setenv("WALL_DASH_URL", "http://203.0.113.9:5051/")


def _run(capsys):
    with pytest.raises(SystemExit) as e:
        infra_watchdog.main()
    out = capsys.readouterr().out
    out = re.sub(r"\d{4}-\d\d-\d\d \d\d:\d\dZ", "<DATE>Z", out)
    return e.value.code, re.sub(r"since \d{4}-\d\d-\d\d \d\d:\d\d:\d\d", "since <T>", out)


GOLDEN = """\
⚠ INFRA WATCHDOG [P1] <DATE>Z — 13 issue(s):
  [P1] wall-dash nginx :5051 unreachable on the backup host
  [P1] Hermes gateway not active (state=inactive)
  [P1] hermes-dispatcher not active (state=failed) — webhooks/kanban dispatch down
  [P1] KB warm-search daemon socket dead and auto-restart FAILED (unit=failed) — knowledge.py search degraded to ~8s cold-start
  [P1] Primary disk 95% full
  [P1] Backup host (203.0.113.9) unreachable — disk probe failed (rc=255)
  [P1] Cron 'Backup': last run error: Script timed out after 600s
  [P1] Delegation dead: last 1 subagent run(s) failed on auth — check auth.json credential pool
  [P1] [default] MEMORY.md at 99% (99/100) — compaction overdue, store near reject
  [P1] [sister] USER.md at 98% (2200/2250) — compaction overdue, store near reject
  [P1] Anthropic bypass fallback: 1 auth failure(s) in last 65 min — primary provider auth failing
  [P1] RAG prefetch timed out 3x in last 65 min — turns ran without recall (check LH02 Ollama /api/ps, swarm workers)
  [P1] GUARD NOT ARMED IN GATEWAY: '[local-guards] alpha armed' absent in journal since <T>

Read-only detection. To act: ask me to run `infra-incident-triage` (diagnose → propose → your approval).
"""


def test_alert_text_is_unchanged(tmp_path, monkeypatch, capsys):
    _broken_host(tmp_path, monkeypatch)
    code, out = _run(capsys)
    assert code == 0
    assert out == GOLDEN


def test_missing_env_vars_are_findings_not_an_abort(tmp_path, monkeypatch, capsys):
    _broken_host(tmp_path, monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "no-env"))
    monkeypatch.setattr(infra_watchdog, "env", lambda k, d=None: None)
    code, out = _run(capsys)
    assert code == 0
    assert "[P1] WALL_DASH_URL is not set" in out
    assert "[P1] VPS_A_HOST is not set" in out
    assert "Cron 'Backup'" in out   # later checks still ran
