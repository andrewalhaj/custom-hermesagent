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
    add(d, 's1', 'user', GOOD, now - 7300)
    add(d, 's1', 'assistant', None, now - 7299)          # tool-only assistant row
    add(d, 's1', 'assistant', 'the reply', now - 7298)
    add(d, 's1', 'user', GOOD + ' again', now - 7250)  # next user turn with no reply (older than the pending grace)
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


def test_since_last_does_not_skip_a_turn_still_awaiting_its_reply(tmp_path):
    now = time.time()
    d = make_db()
    d.execute("INSERT INTO sessions VALUES ('a','cli','A'), ('b','cli','B')")
    add(d, 'a', 'user', GOOD, now - 100)               # no reply yet
    add(d, 'b', 'user', GOOD + ' b', now - 50)
    add(d, 'b', 'assistant', 'reply b', now - 49)
    first = se.fetch_pairs(d, 30)
    assert [p['session_id'] for p in first] == []      # b is held back too: its ts would advance the watermark past a
    add(d, 'a', 'assistant', 'reply a', now - 40)
    second = se.fetch_pairs(d, 30, after_ts=0.0)       # nothing was inserted, so the watermark is unchanged
    assert [p['session_id'] for p in second] == ['a', 'b']


def test_abandoned_unanswered_turn_does_not_block_forever():
    d = make_db()
    d.execute("INSERT INTO sessions VALUES ('a','cli','A')")
    add(d, 'a', 'user', GOOD, time.time() - se.PENDING_GRACE_S - 10)
    assert len(se.fetch_pairs(d, 30)) == 1             # old enough: final no_reply drop, not pending


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
