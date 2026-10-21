"""session_vector: flag-gated vector leg + merge helper."""
import importlib
import os
import sys

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

sv = importlib.import_module("knowledge.session_vector")


def _row(sid, sim, mid=1):
    return {"id": "x", "session_id": sid, "message_id": mid, "source": "cli", "title": "T",
            "user_text": "u" * 300, "reply_text": "r", "ts": "2026-10-01T12:00:00+00:00",
            "similarity": sim}


def _setup(monkeypatch, rows=None, boom=False):
    calls = []

    def post(path, payload, prefer='', timeout=15):
        calls.append((path, payload, timeout))
        if boom:
            raise RuntimeError("down")
        return rows

    monkeypatch.setenv("SESSION_VECTOR", "true")
    monkeypatch.setattr(sv, "embed", lambda t: [[0.1, 0.2]])
    monkeypatch.setattr(sv, "_supa_post", post)
    return calls


def test_flag_off_no_network(monkeypatch):
    monkeypatch.delenv("SESSION_VECTOR", raising=False)
    monkeypatch.setattr(sv, "embed", lambda t: 1 / 0)
    monkeypatch.setattr(sv, "_supa_post", lambda *a, **k: 1 / 0)
    assert sv.search_session_turns("q") == []


def test_rpc_payload_and_hit_shape(monkeypatch):
    calls = _setup(monkeypatch, [_row("s1", 0.8, 7)])
    hits = sv.search_session_turns("q", top_k=2, days=30)
    path, payload, timeout = calls[0]
    assert path == "/rest/v1/rpc/match_session_turns"
    assert payload["match_count"] == 6 and payload["query_embedding"] == [0.1, 0.2]
    assert "since" in payload and timeout == 3
    assert hits == [{"store": "sessions", "score": 0.8, "title": "cli 2026-10-01: T",
                     "text": "T — you: " + "u" * 160 + " | me: r", "link": "@session:default/s1",
                     "provenance": "session", "session_id": "s1", "message_id": 7}]


def test_empty_reply_omits_me_part(monkeypatch):
    _setup(monkeypatch, [{**_row("s1", 0.8), "reply_text": ""}])
    assert sv.search_session_turns("q")[0]["text"] == "T — you: " + "u" * 160


def test_one_hit_per_session_best_similarity(monkeypatch):
    _setup(monkeypatch, [_row("s1", 0.5, 1), _row("s1", 0.9, 2), _row("s2", 0.7, 3)])
    hits = sv.search_session_turns("q", top_k=5)
    assert [(h["session_id"], h["message_id"]) for h in hits] == [("s1", 2), ("s2", 3)]


def test_excludes_current_session(monkeypatch):
    _setup(monkeypatch, [_row("cur", 0.99), _row("s2", 0.7)])
    assert [h["session_id"] for h in sv.search_session_turns("q", exclude_session_id="cur")] == ["s2"]


def test_failure_returns_empty(monkeypatch, capsys):
    _setup(monkeypatch, boom=True)
    assert sv.search_session_turns("q") == []
    assert "session vector search failed" in capsys.readouterr().err


def _h(sid, score):
    return {"session_id": sid, "score": score}


def test_merge_both_legs_beats_single_leg_even_with_higher_cosine():
    out = sv.merge_session_hits([_h("x", 0.5), _h("a", 0.3)], [_h("v", 0.95), _h("a", 0.8)], 5)
    assert [h["session_id"] for h in out] == ["a", "v", "x"]
    assert out[0]["score"] == 0.3 and out[0]["rrf"] > out[1]["rrf"]  # equal best rank: fts dict kept


def test_merge_disjoint_interleaves_by_rank():
    fts = [_h("f1", 0.9), _h("f2", 0.8)]  # equal-rank ties go to the higher original score
    vec = [_h("v1", 0.6), _h("v2", 0.5)]
    out = sv.merge_session_hits(fts, vec, 5)
    assert [h["session_id"] for h in out] == ["f1", "v1", "f2", "v2"]


def test_merge_cuts_to_top_k():
    out = sv.merge_session_hits([_h("a", 0.2), _h("c", 0.8)], [_h("b", 0.6)], 2)
    assert [h["session_id"] for h in out] == ["b", "a"]


def test_merge_empty_legs():
    assert sv.merge_session_hits([], [], 5) == []
    assert [h["session_id"] for h in sv.merge_session_hits([_h("a", 0.2)], [], 5)] == ["a"]
    assert [h["session_id"] for h in sv.merge_session_hits([], [_h("b", 0.6)], 5)] == ["b"]


def test_hit_text_uses_excerpt(monkeypatch):
    row = _row("s1", 0.8) | {"reply_text": "filler " * 150 + "the answer is port 8123"}
    _setup(monkeypatch, [row])
    (h,) = sv.search_session_turns("what port", top_k=2)
    assert "port 8123" in h["text"]
