"""Step 1.2: distill picks up every ended session and never skips one past a failure.

Run: python -m pytest scripts/tests/test_session_distill.py -v
"""
import json, os, sqlite3, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

import session_distill as sd
from session_state import SLOW_RETRIEVAL_FLAGS

NOW = 2_000_000_000.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / 'state.db'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE sessions (id TEXT, source TEXT, title TEXT, ended_at REAL,'
              ' message_count INT, end_reason TEXT)')
    c.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)')
    c.commit()
    c.close()
    monkeypatch.setattr(sd, 'STATE_DB', str(db))
    monkeypatch.setattr(sd, 'WATERMARK_FILE', str(tmp_path / 'wm.json'))
    # Start the watermark just before the fixture sessions.
    sd.save_watermark(NOW - 100, set(), {})
    return db


def add_session(db, sid, ended_at, end_reason, source='subagent'):
    c = sqlite3.connect(db)
    c.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)', (sid, source, sid, ended_at, 40, end_reason))
    for i in range(20):
        c.execute('INSERT INTO messages (session_id, role, content) VALUES (?,?,?)',
                  (sid, 'user' if i % 2 else 'assistant', f'I decided to fix thing {i}. Deployed it.'))
    c.commit()
    c.close()


def watermark():
    return json.load(open(sd.WATERMARK_FILE))


def test_non_reset_end_reasons_qualify(env):
    add_session(env, 'a', NOW, 'agent_close')
    add_session(env, 'b', NOW + 1, 'cli_close', source='cli')
    add_session(env, 'c', NOW + 2, 'startup_orphan_reap', source='kanban')
    add_session(env, 'open', None, None)
    add_session(env, 'cron_x', NOW + 3, 'cron_complete', source='cron')
    got = [s['id'] for s in sd.get_qualifying_sessions(NOW - 100, set())]
    assert got == ['a', 'b', 'c']


def test_failed_store_holds_watermark_so_session_is_retried(env, monkeypatch):
    add_session(env, 'a', NOW, 'agent_close')
    add_session(env, 'b', NOW + 1, 'agent_close')
    monkeypatch.setattr(sd, 'store_digest', lambda d, tags=sd.TAGS: d.startswith('Session: b'))
    assert sd.main() == 1
    wm = watermark()
    assert wm['last_processed_ts'] < NOW and 'a' not in wm['processed_session_ids']
    assert [s['id'] for s in sd.get_qualifying_sessions(wm['last_processed_ts'], set(wm['processed_session_ids']))][0] == 'a'


def test_progress_is_saved_per_session(env, monkeypatch):
    # A cron timeout kills the run mid-loop; work already stored must not be redone.
    add_session(env, 'a', NOW, 'agent_close')
    add_session(env, 'b', NOW + 1, 'agent_close')
    calls = []

    def store(d, tags=sd.TAGS):
        calls.append(d)
        if len(calls) == 2:
            raise KeyboardInterrupt  # stands in for the runner's SIGKILL
        return True
    monkeypatch.setattr(sd, 'store_digest', store)
    with pytest.raises(KeyboardInterrupt):
        sd.main()
    assert watermark()['last_processed_ts'] == NOW


def test_backlog_is_capped_per_run(env, monkeypatch):
    for i in range(sd.MAX_ENDED_PER_RUN + 3):
        add_session(env, f's{i:02d}', NOW + i, 'agent_close')
    monkeypatch.setattr(sd, 'store_digest', lambda d, tags=sd.TAGS: True)
    sd.main()
    assert watermark()['last_processed_ts'] == NOW + sd.MAX_ENDED_PER_RUN - 1


def test_guard_rejection_is_final_but_outage_is_retried(monkeypatch):
    import knowledge
    for flag in SLOW_RETRIEVAL_FLAGS:
        monkeypatch.setenv(flag, '')  # store_digest overwrites these; monkeypatch restores them

    def reject(*a, **k):
        raise ValueError('[cold-store-guard] write rejected: junk')
    monkeypatch.setattr(knowledge, 'store', reject)
    assert sd.store_digest('x') is True

    def outage(*a, **k):
        raise ValueError('embedder returned no vector')
    monkeypatch.setattr(knowledge, 'store', outage)
    assert sd.store_digest('x') is False


def test_shared_timestamp_group_split_by_cap_is_finished_next_run(env, monkeypatch):
    # One orphan reap stamps many sessions with the same ended_at; the cap splits the group.
    n = sd.MAX_ENDED_PER_RUN + 5
    for i in range(n):
        add_session(env, f's{i:02d}', NOW, 'startup_orphan_reap')
    stored = []
    monkeypatch.setattr(sd, 'store_digest', lambda d, tags=sd.TAGS: stored.append(d) or True)
    sd.main()
    sd.main()
    assert len(stored) == n and len(set(stored)) == n


def test_failure_inside_shared_timestamp_group_is_retried(env, monkeypatch):
    add_session(env, 'a', NOW, 'agent_close')
    add_session(env, 'b', NOW, 'agent_close')
    monkeypatch.setattr(sd, 'store_digest', lambda d, tags=sd.TAGS: d.startswith('Session: a'))
    assert sd.main() == 1
    wm = watermark()
    assert [s['id'] for s in sd.get_qualifying_sessions(wm['last_processed_ts'], set(wm['processed_session_ids']))] == ['b']


def test_trim_drops_oldest_ids(env):
    ids = dict.fromkeys(f'old{i}' for i in range(sd.MAX_WATERMARK_IDS))
    ids['newest'] = None
    sd.save_watermark(NOW, ids, {})
    _, loaded, _ = sd.load_watermark()
    assert 'newest' in loaded and 'old0' not in loaded and len(loaded) == sd.MAX_WATERMARK_IDS


def test_digest_needs_some_signal():
    blank = [{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'ok'}]
    assert sd.distill_session({'title': 't'}, blank) is None
    opener = [{'role': 'user', 'content': 'tell me about the garden'}, {'role': 'assistant', 'content': 'ok'}]
    assert sd.distill_session({'title': 't'}, opener).startswith('Session: t | unknown-date')
