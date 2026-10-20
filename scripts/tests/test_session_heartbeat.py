"""Step 1.3: heartbeat never advances its watermark past facts it failed to store.

Run: python -m pytest scripts/tests/test_session_heartbeat.py -v
"""
import json, os, sqlite3, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

import session_heartbeat as hb


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / 'state.db'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE sessions (id TEXT, source TEXT, started_at REAL, ended_at REAL)')
    c.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)')
    c.execute("INSERT INTO sessions VALUES ('s', 'cli', 1, NULL)")
    c.commit()
    c.close()
    monkeypatch.setattr(hb, 'STATE_DB', str(db))
    monkeypatch.setattr(hb, 'WATERMARK_FILE', str(tmp_path / 'wm.json'))
    monkeypatch.setattr(hb, 'AUDIT_LOG', str(tmp_path / 'audit.md'))
    monkeypatch.setattr(hb, 'search', lambda *a, **k: [])
    # One fact per line keeps the test independent of the junk-filter heuristics.
    monkeypatch.setattr(hb, 'extract_facts',
                        lambda text: [(l, 'decision') for l in text.splitlines() if l.strip()])
    return db


def add_message(db, lines):
    c = sqlite3.connect(db)
    c.execute("INSERT INTO messages (session_id, role, content) VALUES ('s', 'assistant', ?)",
              ('\n'.join(f'Decided to keep fact number {n} for the heartbeat test' for n in lines),))
    c.commit()
    c.close()


def stored_into(monkeypatch, fail=None):
    got = []

    def store(fact, **kw):
        if fail:
            raise fail
        got.append(fact)
    monkeypatch.setattr(hb, 'store', store)
    return got


def watermark():
    return json.load(open(hb.WATERMARK_FILE)) if os.path.exists(hb.WATERMARK_FILE) else {}


def test_embedder_down_holds_watermark_and_retries(env, monkeypatch):
    add_message(env, [1, 2])
    stored_into(monkeypatch, fail=ConnectionError('embedder down'))
    hb.main()
    assert watermark().get('s', 0) == 0

    got = stored_into(monkeypatch)
    hb.main()
    assert len(got) == 2
    assert watermark()['s'] == 1


def test_guard_rejection_still_advances(env, monkeypatch):
    add_message(env, [1])
    stored_into(monkeypatch, fail=ValueError('[cold-store-guard] write rejected'))
    hb.main()
    assert watermark()['s'] == 1


def test_facts_past_the_cap_are_deferred_not_dropped(env, monkeypatch):
    monkeypatch.setattr(hb, 'MAX_FACTS_PER_SESSION', 3)
    add_message(env, [1, 2])
    add_message(env, [3, 4])
    add_message(env, [5])
    got = stored_into(monkeypatch)
    hb.main()
    assert len(got) == 2 and watermark()['s'] == 1
    hb.main()
    assert len(got) == 5
    assert watermark()['s'] == 3


def test_bad_backend_json_is_retried_not_skipped(env, monkeypatch):
    add_message(env, [1])
    stored_into(monkeypatch, fail=json.JSONDecodeError('bad gateway html', '<html>', 0))
    hb.main()
    assert watermark().get('s', 0) == 0
