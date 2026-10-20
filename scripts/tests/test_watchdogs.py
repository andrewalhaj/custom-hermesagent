"""infra_watchdog / skill_desc_reconcile: the fixed behaviours of step 4.4.

Run: python -m pytest scripts/tests/test_watchdogs.py -v
"""
import os, sys
from datetime import datetime, timedelta

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import infra_watchdog  # noqa: E402
import skill_desc_reconcile as recon  # noqa: E402


# ── infra_watchdog ───────────────────────────────────────────────────────────

def test_auth_failures_counts_recent_local_time_lines():
    now = datetime(2026, 10, 16, 3, 0, 0)
    log = "\n".join([
        f"{(now - timedelta(minutes=10)):%Y-%m-%d %H:%M:%S},1 WARN Primary provider auth failed",
        f"{(now - timedelta(hours=3)):%Y-%m-%d %H:%M:%S},1 WARN Primary provider auth failed",
        f"{now:%Y-%m-%d %H:%M:%S},1 INFO unrelated",
    ])
    assert infra_watchdog.auth_failures(log, now) == 1


def test_log_hits_counts_any_needle():
    now = datetime(2026, 10, 16, 3, 0, 0)
    line = f"{(now - timedelta(minutes=5)):%Y-%m-%d %H:%M:%S},1 WARNING [supabase-memory] prefetch timed out after 3s"
    assert infra_watchdog.log_hits("\n".join([line] * 3), now, "prefetch timed out") == 3


def test_signature_ignores_changing_numbers():
    a = infra_watchdog.signature(["Primary disk 91% full", "MEMORY.md at 93% (2790/3000)"])
    b = infra_watchdog.signature(["Primary disk 92% full", "MEMORY.md at 94% (2820/3000)"])
    assert a == b
    assert a != infra_watchdog.signature(["Primary disk 91% full"])


def test_cron_health_surfaces_every_non_ok_status_with_its_error():
    jobs = [
        {"name": "Backup", "last_status": "error", "last_error": "Script timed out after 600s"},
        {"name": "Digest", "last_status": "delivery_failed", "last_delivery_error": "chat not found"},
        {"name": "Fine", "last_status": "ok"},
        {"name": "Off", "enabled": False, "last_status": "error"},
        {"name": "Infra Watchdog (hourly)", "last_status": "error"},
    ]
    alerts = infra_watchdog.cron_health(jobs)
    assert alerts == ["Cron 'Backup': last run error: Script timed out after 600s",
                      "Cron 'Digest': last run delivery_failed: chat not found"]


# ── skill_desc_reconcile ─────────────────────────────────────────────────────

def _skill(root, name, desc):
    d = root / name
    d.mkdir(parents=True)
    (d / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: {desc}\n---\nbody\n')
    return d / 'SKILL.md'


def test_reconcile_never_touches_core():
    assert all(not r.startswith('/usr/local/lib/hermes-agent') for r in recon.ROOTS)


def test_check_lists_offenders_and_is_silent_when_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(recon, 'ROOTS', [str(tmp_path)])
    _skill(tmp_path, 'short', 'Short and fine.')
    recon.main(['--check'])
    assert capsys.readouterr().out == ''
    _skill(tmp_path, 'long', 'x ' * 40)
    recon.main(['--check'])
    out = capsys.readouterr().out
    assert 'long' in out and 'short' not in out


def test_apply_writes_valid_yaml_for_quotes_and_backslashes(tmp_path, monkeypatch):
    monkeypatch.setattr(recon, 'ROOTS', [str(tmp_path)])
    desc = 'Use when a path like C:\\temp or a "quoted" word matters for the skill trigger text here'
    f = _skill(tmp_path, 'esc', f"'{desc}'")
    recon.main(['--apply'])
    fm = yaml.safe_load(f.read_text().split('---')[1])
    assert fm['description'] == recon.compact(desc, 'esc')
    assert not list(tmp_path.rglob('*.bak*'))
