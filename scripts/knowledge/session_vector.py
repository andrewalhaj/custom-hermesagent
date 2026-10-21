"""Vector leg for the 'sessions' store (flag SESSION_VECTOR, default off).

Why vector AND FTS (session_store.search_sessions):
  - FTS5 matches the literal nouns the user actually typed ("ALM", "kanban").
  - Vector matches paraphrase: "that license server thing" ~ "ALM" shares no
    token with the original turn but sits close in embedding space.
Run both and combine with merge_session_hits().

Source: Supabase table session_turns via RPC match_session_turns.
"""
import sys
from datetime import datetime, timedelta, timezone

from knowledge.embed import embed
from knowledge.supabase_client import _supa_post
from knowledge.config import flag
from knowledge.session_store import excerpt

_RPC_TIMEOUT = 3


def _text(r, query):
    text = f"{r.get('title') or ''} — you: {(r.get('user_text') or '')[:160]}"
    reply = " ".join((r.get('reply_text') or '').split())
    return f"{text} | me: {excerpt(reply, query)}" if reply else text


def search_session_turns(query, top_k=2, exclude_session_id='', days=30):
    if not flag('SESSION_VECTOR'):
        return []
    try:
        qvec = embed([query])[0]
        qvec = qvec.tolist() if hasattr(qvec, 'tolist') else list(qvec)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = _supa_post('/rest/v1/rpc/match_session_turns',
                          {'query_embedding': qvec, 'match_count': top_k * 3, 'since': since},
                          prefer='', timeout=_RPC_TIMEOUT)
        if not isinstance(rows, list):
            return []
        best = {}  # session_id -> row with the highest similarity
        for r in rows:
            sid = r['session_id']
            if sid == exclude_session_id:
                continue
            if sid not in best or r['similarity'] > best[sid]['similarity']:
                best[sid] = r
        hits = [{
            'store': 'sessions',
            # cosine similarity is already 0..1, same scale as knowledge hits pre-rerank
            'score': float(r['similarity']),
            'title': f"{r.get('source') or ''} {str(r.get('ts') or '')[:10]}: {r.get('title') or ''}",
            'text': _text(r, query),
            'link': f"@session:default/{r['session_id']}",
            'provenance': 'session',
            'session_id': r['session_id'],
            'message_id': r.get('message_id'),
        } for r in best.values()]
        return sorted(hits, key=lambda h: h['score'], reverse=True)[:top_k]
    except Exception as e:
        print(f"[knowledge] session vector search failed: {e}", file=sys.stderr)
        return []


def merge_session_hits(fts_hits, vec_hits, top_k):
    """Reciprocal rank fusion (k=60) by session_id, cut to top_k; RRF is scale-free, so cosine and BM25 scores needn't be comparable."""
    best = {}  # sid -> (best rank, hit, rrf)
    for leg in (fts_hits, vec_hits):
        seen = set()
        for h in leg:  # legs are assumed sorted best-first
            sid = h['session_id']
            if sid in seen:
                continue
            seen.add(sid)
            rank = len(seen)
            gain = 1 / (60 + rank)
            if sid not in best:
                best[sid] = [rank, h, gain]
            else:
                best[sid][2] += gain
                if rank < best[sid][0]:
                    best[sid][:2] = [rank, h]
    merged = [{**h, 'rrf': rrf} for _, h, rrf in best.values()]
    return sorted(merged, key=lambda h: (h['rrf'], h['score']), reverse=True)[:top_k]
