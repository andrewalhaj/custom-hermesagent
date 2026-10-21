"""SESSION_STORE: opt-in 'sessions' store over user messages in state.db."""
import importlib
import os
import sqlite3
import sys
import time

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

ss = importlib.import_module("knowledge.session_store")
us = importlib.import_module("knowledge.unified_search")

# Real state.db DDL, trimmed to the columns session_store reads; plain FTS5 (rowid == messages.id).
DDL = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, started_at REAL NOT NULL, title TEXT);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL, content TEXT, timestamp REAL NOT NULL);
CREATE VIRTUAL TABLE messages_fts USING fts5(content);
"""
DAY = 86400


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.executescript(DDL)

    def add(sid, role, content, age_days=1, title="T", source="cli"):
        conn.execute("INSERT OR IGNORE INTO sessions VALUES (?,?,?,?)", (sid, source, 0, title))
        cur = conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                           (sid, role, content, time.time() - age_days * DAY))
        conn.execute("INSERT INTO messages_fts (rowid, content) VALUES (?,?)", (cur.lastrowid, content))
        conn.commit()
        return cur.lastrowid

    yield add
    conn.close()


def test_user_only(db):
    db("a", "assistant", "kanban dispatcher plan")
    db("b", "tool", "kanban dispatcher output")
    assert ss.search_sessions("kanban dispatcher") == []
    db("c", "user", "kanban dispatcher plan")
    assert [h["session_id"] for h in ss.search_sessions("kanban dispatcher")] == ["c"]


def test_window(db):
    db("old", "user", "kanban dispatcher", age_days=20)
    db("new", "user", "kanban dispatcher", age_days=2)
    assert [h["session_id"] for h in ss.search_sessions("kanban dispatcher")] == ["new"]


def test_one_hit_per_session_and_top_k(db):
    for _ in range(3):
        db("a", "user", "kanban dispatcher")
    db("b", "user", "kanban dispatcher")
    db("c", "user", "kanban dispatcher")
    hits = ss.search_sessions("kanban dispatcher", top_k=5)
    assert sorted(h["session_id"] for h in hits) == ["a", "b", "c"]
    assert len(ss.search_sessions("kanban dispatcher")) == 2


def test_exclude_current_session(db):
    db("cur", "user", "kanban dispatcher")
    db("other", "user", "kanban dispatcher")
    assert [h["session_id"] for h in ss.search_sessions("kanban dispatcher", exclude_session_id="cur")] == ["other"]


def test_and_falls_back_to_or(db):
    db("a", "user", "kanban dispatcher")
    assert ss.search_sessions("kanban dispatcher zebra") != []  # AND finds nothing; OR does


@pytest.mark.parametrize("q", ['kanban" OR (', "NEAR(a b) *", "foo:bar -baz ^", "'; DROP TABLE x;--", "", "the of"])
def test_punctuation_and_operators_never_raise(db, q):
    db("a", "user", "kanban dispatcher")
    assert isinstance(ss.search_sessions(q), list)


def test_operator_words_are_literal(db):
    db("a", "user", "kanban dispatcher")
    assert ss.search_sessions("kanban NOT dispatcher") != []


def test_hit_shape(db):
    db("abc", "user", "kanban dispatcher " + "x" * 400, title="Plan", source="telegram")
    h = ss.search_sessions("kanban dispatcher")[0]
    assert h["store"] == "sessions" and h["provenance"] == "session"
    assert 0 < h["score"] < 1
    assert h["link"] == "@session:default/abc" and h["session_id"] == "abc" and isinstance(h["message_id"], int)
    assert h["title"].startswith("telegram ") and h["title"].endswith(": Plan")
    assert h["text"].startswith("Plan — you said: kanban dispatcher")
    assert len(h["text"]) <= len("Plan — you said: ") + 200


def test_hit_carries_paired_reply(db):
    db("r", "user", "did RR1 ship verdict")
    db("r", "assistant", "   ")
    db("r", "assistant", "NO SHIP on latency " + "z" * 600)
    db("r", "user", "thanks")
    db("r", "assistant", "later reply")
    t = ss.search_sessions("RR1 verdict")[0]["text"]
    assert t.startswith("T — you: did RR1 ship verdict | me: NO SHIP on latency")
    assert "later reply" not in t and len(t.split(" | me: ")[1]) == 501  # 500 + trailing …


def test_no_reply_before_next_user_turn_falls_back(db):
    db("n", "user", "did RR1 ship verdict")
    db("n", "user", "hello?")
    db("n", "assistant", "answer to the second")
    assert ss.search_sessions("RR1 verdict")[0]["text"] == "T — you said: did RR1 ship verdict"


def test_missing_db_returns_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert ss.search_sessions("kanban dispatcher") == []


def _only_sessions(monkeypatch):
    for n in ("_search_knowledge", "_search_kanban"):
        monkeypatch.setattr(us, n, lambda *a, **k: [])
    monkeypatch.setattr(us, "_search_skills", lambda *a, **k: [])
    monkeypatch.setattr(us, "_search_references", lambda *a, **k: [])


def test_flag_off_ignores_sessions(db, monkeypatch):
    _only_sessions(monkeypatch)
    db("a", "user", "kanban dispatcher")
    monkeypatch.delenv("SESSION_STORE", raising=False)
    assert us.unified_search("kanban dispatcher", stores=["sessions"]) == []


def test_flag_on_requires_explicit_store(db, monkeypatch):
    _only_sessions(monkeypatch)
    db("a", "user", "kanban dispatcher")
    monkeypatch.setenv("SESSION_STORE", "1")
    assert us.unified_search("kanban dispatcher") == []  # default stores exclude sessions
    assert [h["store"] for h in us.unified_search("kanban dispatcher", stores=["sessions"])] == ["sessions"]
    assert us.unified_search("kanban dispatcher", stores=["sessions"], exclude_session_id="a") == []


def test_unknown_store_ignored(db, monkeypatch):
    _only_sessions(monkeypatch)
    assert us.unified_search("kanban", stores=["nope", "sessions"]) == []


def test_excerpt_centres_on_earliest_keyword():
    reply = "intro " * 100 + "the fix was bumping the watchdog timeout " + "tail " * 200
    out = ss.excerpt(reply, "what did we do about the watchdog timeout")
    assert "watchdog" in out and out.startswith("…") and out.endswith("…") and len(out) <= 502
    assert not out.startswith("…ntro")  # cut on whitespace, not mid-word


def test_excerpt_no_term_is_head_and_short_reply_whole():
    assert ss.excerpt("alpha " * 200, "zzzz qqqq").startswith("alpha alpha") and not ss.excerpt("alpha " * 200, "zzzz").startswith("…")
    assert len(ss.excerpt("alpha " * 200, "zzzz")) <= 501
    assert ss.excerpt("short reply here", "reply") == "short reply here"


def test_excerpt_ignores_stopwords():
    assert ss.excerpt("the " * 300 + "kanban", "why the kanban").endswith("kanban")


def test_fts_hit_text_uses_excerpt(db):
    db("s", "user", "kanban dispatcher question")
    db("s", "assistant", "filler " * 150 + "the answer is port 8123 for kanban")
    (h,) = ss.search_sessions("kanban dispatcher")
    assert "port 8123" in h["text"]


def test_bracketed_notifications_are_not_user_messages(db, monkeypatch):
    monkeypatch.setenv("SESSION_STORE", "true")
    db("a", "user", "[ASYNC DELEGATION BATCH COMPLETE] what did azure blob pricing come out to")
    assert ss.search_sessions("azure blob pricing") == []
    db("b", "user", "what did azure blob pricing come out to")
    assert [h["session_id"] for h in ss.search_sessions("azure blob pricing")] == ["b"]
