"""session_state: read-only state.db, atomic JSON save, slow retrieval flags off.

Run: python -m pytest scripts/tests/test_session_state.py -v
"""
import json, os, sqlite3, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

import session_state as ss


def test_connect_ro_reads_but_cannot_write(tmp_path):
    db = tmp_path / 'odd ?#dir' / 'state.db'
    db.parent.mkdir()
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE t (x)')
    c.execute('INSERT INTO t VALUES (1)')
    c.commit()
    c.close()
    ro = ss.connect_ro(str(db))
    assert ro.execute('SELECT x FROM t').fetchall() == [(1,)]
    with pytest.raises(sqlite3.OperationalError):
        ro.execute('INSERT INTO t VALUES (2)')


def test_save_json_replaces_atomically(tmp_path, monkeypatch):
    path = str(tmp_path / 'sub' / 'wm.json')
    ss.save_json(path, {'a': 1})

    def killed(*a, **k):
        raise KeyboardInterrupt  # simulates a kill mid-dump
    monkeypatch.setattr(json, 'dump', killed)
    with pytest.raises(KeyboardInterrupt):
        ss.save_json(path, {'a': 2})
    assert json.load(open(path)) == {'a': 1}


def test_retrieval_flags_off(monkeypatch):
    for flag in ss.SLOW_RETRIEVAL_FLAGS:
        monkeypatch.setenv(flag, 'true')
    ss.retrieval_flags_off()
    assert all(os.environ[f] == 'false' for f in ss.SLOW_RETRIEVAL_FLAGS)
