"""session_embed: junk filter, pairing, text contract, dry-run safety, --since-last."""
import os
import sqlite3
import sys
import time

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from knowledge import session_embed as se

GOOD = "please check why the gateway restarts every night"


@pytest.mark.parametrize("user,reply,ok", [
    (GOOD, "it is the cron", True),
    ("fix the thing now", "ok", False),                    # short
    ("[SYSTEM] " + GOOD, "ok", False),                     # bracket
    ("proceed with the plan please", "x", False),          # chatter
    (GOOD + " proceed", "x", True),                        # chatter only drops short tails
    ("1234 {}[] 5678 ::: ,,, ;;; -- 99 11 22 33 ab", "x", False),  # non-alpha
    (GOOD + " PONYTAIL MODE", "x", False),                 # internal
    (GOOD + " [recall-gate]", "x", False),
    (GOOD + " memory-context", "x", False),
    (GOOD, "   ", False),                                  # no reply
    ("", "x", False),
])
def test_is_embeddable(user, reply, ok):
    assert se.is_embeddable(user, reply) is ok


def make_db(path=":memory:"):
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT);
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,
                           content TEXT, tool_calls TEXT, timestamp REAL NOT NULL);
    """)
    return db


def add(db, sid, role, content, ts):
    db.execute("INSERT INTO messages(session_id, role, content, timestamp) VALUES (?,?,?,?)", (sid, role, content, ts))


@pytest.fixture
def db():
    d = make_db()
    d.execute("INSERT INTO sessions VALUES ('s1','telegram','Gateway'), ('s2','cron','Cron'), ('s3','cli',NULL)")
    now = time.time()
    add(d, 's1', 'user', GOOD, now - 100)
    add(d, 's1', 'assistant', None, now - 99)          # tool-only assistant row
    add(d, 's1', 'assistant', 'the reply', now - 98)
    add(d, 's1', 'user', GOOD + ' again', now - 50)    # next user turn with no reply
    add(d, 's2', 'user', GOOD, now - 100)              # wrong source
    add(d, 's3', 'user', GOOD, now - 40 * 86400)       # older than --days
    return d


def test_pairing_skips_empty_assistant_and_stops_at_next_user(db):
    pairs = se.fetch_pairs(db, 30)
    assert [(p['session_id'], p['reply_text']) for p in pairs] == [('s1', 'the reply'), ('s1', '')]
    assert len(se.fetch_pairs(db, 365)) == 3  # --days widens the window to include s3


def test_text_matches_contract():
    assert se.build_text('T', 'u', 'r' * 400) == 'T\nUser: u\nAssistant: ' + 'r' * 300


def test_since_last_filters_older(db):
    ts = db.execute("SELECT timestamp FROM messages WHERE content = ?", (GOOD + ' again',)).fetchone()[0]
    assert [p['user_text'] for p in se.fetch_pairs(db, 30, after_ts=ts - 1)] == [GOOD + ' again']
    assert se.fetch_pairs(db, 30, after_ts=ts) == []


def _state_db(tmp_path):
    d = make_db(str(tmp_path / "state.db"))
    d.execute("INSERT INTO sessions VALUES ('s1','cli','T')")
    add(d, 's1', 'user', GOOD, time.time())
    add(d, 's1', 'assistant', 'reply', time.time())
    d.commit()
    d.close()


def test_dry_run_embeds_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _state_db(tmp_path)
    monkeypatch.setattr(se.hermes_env, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(se, "apply", lambda kept: pytest.fail("must not embed or write in dry run"))
    assert se.main([]) == 0
    assert "candidates=1 kept=1 dropped=0" in capsys.readouterr().out


def test_apply_failure_exits_nonzero(tmp_path, monkeypatch, capsys):
    _state_db(tmp_path)
    monkeypatch.setattr(se.hermes_env, "HERMES_HOME", tmp_path)
    def down(kept):
        raise RuntimeError("embed down")
    monkeypatch.setattr(se, "apply", down)
    assert se.main(["--apply"]) == 1
    assert "embed down" in capsys.readouterr().err


def test_secret_in_user_or_reply_is_dropped():
    from knowledge.session_embed import drop_rule
    reply = "Sure, I'll use it."
    assert drop_rule("heres new PAT github_pat_11AAAA_abc for the repo please use it", reply) == 'secret'
    assert drop_rule("sbp_FAKEFIXTURE0000 heres the new supabase management token", reply) == 'secret'
    assert drop_rule("please rotate the key, the header was Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.x", reply) == 'secret'  # gitleaks:allow (fake fixture)
    assert drop_rule("can you add the token to the env file for me now", "done: TOKEN=ghp_abcdefghijklmnop") == 'secret'
    assert drop_rule("can you add the token to the env file for me now", reply) is None
