"""Conversation recall: FTS5 over USER messages in state.db (read-only, last N days, one hit per session)."""
import os, re, sqlite3, sys, time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

_STOP = {"the", "and", "for", "are", "was", "were", "with", "that", "this", "what", "where", "when",
         "how", "did", "does", "you", "our", "can", "about", "from", "have", "has", "not", "but",
         "left", "off", "we", "us", "it", "is", "of", "to", "in", "on", "at", "a", "an", "i", "my", "me"}
_warned = False


def _tokens(query: str) -> List[str]:
    seen = []
    for t in re.findall(r"[A-Za-z0-9]+", query.lower()):
        if len(t) >= 3 and t not in _STOP and t not in seen:
            seen.append(t)
    return seen


_EXCERPT_STOP = {"why", "still", "anything", "after"}


def excerpt(reply: str, query: str, width: int = 500) -> str:
    """~width chars of `reply` starting 120 chars before the earliest query keyword (head if none), cut at whitespace."""
    low = reply.lower()
    pos = [i for t in _tokens(query) if t not in _EXCERPT_STOP for i in [low.find(t)] if i >= 0]
    start = max(0, min(pos) - 120) if pos else 0
    end = start + width
    if start and not reply[start - 1].isspace():  # drop the cut word
        sp = reply.find(" ", start)
        start = sp + 1 if 0 <= sp < start + width // 2 else start
    if end < len(reply) and not reply[end].isspace():
        sp = reply.rfind(" ", start, end)
        end = sp if sp > start + width // 2 else end  # unbroken blob: hard cut
    return ("…" if start else "") + reply[start:end].strip() + ("…" if end < len(reply) else "")


def _next_reply(conn, sid, mid) -> str:
    """Next non-empty assistant message after user message `mid`, before the next user turn (session_embed.fetch_pairs rule)."""
    row = conn.execute(
        "SELECT content FROM messages WHERE session_id = ? AND role = 'assistant' AND id > ? AND TRIM(COALESCE(content, '')) != '' "
        "AND id < COALESCE((SELECT MIN(id) FROM messages WHERE session_id = ? AND role = 'user' AND id > ?), 9223372036854775807) "
        "ORDER BY id LIMIT 1", (sid, mid, sid, mid)).fetchone()
    return " ".join(row[0].split()) if row else ""


def _build_hits(conn, rows, top_k, query) -> List[Dict]:
    hits, seen = [], set()
    for mid, sid, content, rank, title, source, ts in rows:  # rows are best-rank first
        if sid in seen:
            continue
        seen.add(sid)
        title = title or "untitled"
        x = max(-rank, 0.0)
        # Scaled into [0, 0.6): knowledge hits are reranked 0..1, so a strong session hit lands in the
        # top-8 window next to them but never outranks a strong knowledge hit.
        score = 0.6 * x / (1 + x)
        user = " ".join((content or "").split())
        reply = _next_reply(conn, sid, mid)
        text = f"{title} — you: {user[:160]} | me: {excerpt(reply, query)}" if reply else f"{title} — you said: {user[:200]}"
        hits.append({
            'store': 'sessions', 'score': score,
            'title': f"{source} {datetime.fromtimestamp(ts).strftime('%Y-%m-%d')}: {title}",
            'text': text,
            'link': f"@session:default/{sid}", 'provenance': 'session',
            'session_id': sid, 'message_id': mid,
        })
        if len(hits) >= top_k:
            break
    return hits


def search_sessions(query: str, top_k: int = 2, exclude_session_id: str = '', days: int = 14) -> List[Dict]:
    global _warned
    toks = _tokens(query or '')
    if not toks:
        return []
    db = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "state.db"
    sql = ("SELECT m.id, m.session_id, m.content, f.rank, s.title, s.source, m.timestamp "
           "FROM messages_fts f JOIN messages m ON m.id = f.rowid JOIN sessions s ON s.id = m.session_id "
           "WHERE messages_fts MATCH ? AND m.role = 'user' AND SUBSTR(LTRIM(m.content), 1, 1) != '[' "  # gateway notifications are stored as user-role
           "AND m.timestamp >= ? AND m.session_id != ? "
           "ORDER BY f.rank LIMIT 50")
    since = time.time() - days * 86400
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
        try:
            rows = []
            # AND of all terms first; OR of the top 5 terms if that finds nothing
            for joiner, ts in ((" AND ", toks), (" OR ", toks[:5])):
                rows = conn.execute(sql, (joiner.join(f'"{t}"' for t in ts), since, exclude_session_id)).fetchall()
                if rows:
                    break
            hits = _build_hits(conn, rows, top_k, query)
        finally:
            conn.close()
    except sqlite3.Error as e:
        if not _warned:
            _warned = True
            print(f"session_store: {e}", file=sys.stderr)
        return []

    # Vector leg (SESSION_VECTOR flag, [] when off): paraphrase recall over embedded turn pairs.
    from knowledge.session_vector import merge_session_hits, search_session_turns
    return merge_session_hits(hits, search_session_turns(query, top_k=top_k, exclude_session_id=exclude_session_id), top_k)
