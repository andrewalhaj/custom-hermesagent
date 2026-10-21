"""Embed (user message, assistant reply) pairs from state.db into the Supabase session_turns table.

Run from scripts/: python -m knowledge.session_embed [--apply].
Default is a dry run (counts + samples, no embed, no writes); --apply embeds and inserts.
"""
import argparse
import random
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import hermes_env

BATCH = 32
_CHATTER = re.compile(r'^(ok|okay|yes|no|proceed|go|test|thanks|k)\b.{0,20}$', re.I | re.S)
_INTERNAL = ('PONYTAIL MODE', '[recall-gate]', 'memory-context')
_SECRET = re.compile(r'\b(?:gh[pousr]_|github_pat_|sbp_|sk-[A-Za-z0-9]|xox[abp]-|AKIA[0-9A-Z]{12}|eyJ[A-Za-z0-9_-]{20,})')  # token prefixes (gitleaks-style)

# (name, drops?) — first rule that drops a pair names it in the dry-run counts.
_RULES = (
    ('short', lambda u, r: len(u.split()) < 6),  # fewer than 6 words
    ('bracket', lambda u, r: u.startswith('[')),  # internal notifications, OOB markers, compaction summaries
    ('chatter', lambda u, r: bool(_CHATTER.match(u))),  # "ok", "proceed", "thanks" and the like
    ('non_alpha', lambda u, r: sum(not c.isalpha() for c in u) / len(u) > 0.7),  # pasted logs/JSON
    ('internal', lambda u, r: any(m in u for m in _INTERNAL)),  # injected harness text, not the user's words
    ('no_reply', lambda u, r: not r),  # nothing to pair with
    ('secret', lambda u, r: bool(_SECRET.search(u) or _SECRET.search(r))),  # pasted credential in either side — never embed
)


def drop_rule(user_text, reply_text):
    """Name of the first rule that drops this pair, or None to keep it."""
    u, r = (user_text or '').strip(), (reply_text or '').strip()
    if not u:
        return 'short'
    return next((n for n, f in _RULES if f(u, r)), None)


def is_embeddable(user_text, reply_text):
    return drop_rule(user_text, reply_text) is None


def build_text(title, user_text, reply_text):
    # shortcut: user_text capped at 2000 chars (p95 is 620) — a 32 KB paste 400s the embedder; raise if real messages get cut
    return f"{title or ''}\nUser: {user_text[:2000]}\nAssistant: {(reply_text or '')[:300]}"


def fetch_pairs(db, days, after_ts=0.0):
    """Each user message in telegram/cli sessions paired with the next non-empty assistant reply before the next user turn."""
    since = max(time.time() - days * 86400, after_ts)
    rows = db.execute(
        "SELECT m.id, m.session_id, s.source, s.title, m.role, m.content, m.timestamp "
        "FROM messages m JOIN sessions s ON s.id = m.session_id "
        "WHERE s.source IN ('telegram','cli') AND m.role IN ('user','assistant') ORDER BY m.session_id, m.id").fetchall()
    pairs, cur = [], None
    for mid, sid, src, title, role, content, ts in rows:
        if role == 'user':
            cur = {'message_id': mid, 'session_id': sid, 'source': src, 'title': title,
                   'user_text': (content or '').strip(), 'reply_text': '', 'ts': ts}
            if ts > since:
                pairs.append(cur)
        elif cur and cur['session_id'] == sid and not cur['reply_text'] and (content or '').strip():
            cur['reply_text'] = content.strip()
    return pairs


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _last_ts():
    from knowledge.supabase_client import _supa_get
    rows = _supa_get('/rest/v1/session_turns?select=ts&order=ts.desc&limit=1')
    return datetime.fromisoformat(rows[0]['ts']).timestamp() if rows else 0.0


def apply(kept):
    from knowledge.embed import embed
    from knowledge.supabase_client import _supa_post
    for i in range(0, len(kept), BATCH):
        chunk = kept[i:i + BATCH]
        texts = [build_text(p['title'], p['user_text'], p['reply_text']) for p in chunk]
        vecs = embed(texts)
        rows = [{'session_id': p['session_id'], 'message_id': p['message_id'], 'source': p['source'],
                 'title': p['title'], 'user_text': p['user_text'], 'reply_text': p['reply_text'],
                 'text': t, 'ts': _iso(p['ts']), 'vector': list(map(float, v))}
                for p, t, v in zip(chunk, texts, vecs)]
        _supa_post('/rest/v1/session_turns?on_conflict=message_id', rows,
                   prefer='resolution=ignore-duplicates,return=minimal', timeout=60)
        print(f'inserted {i + len(chunk)}/{len(kept)}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--apply', action='store_true', help='embed + insert (default: dry run)')
    ap.add_argument('--dry-run', action='store_true', help='default; accepted for clarity')
    ap.add_argument('--since-last', action='store_true', help='only messages newer than max(ts) in the table')
    args = ap.parse_args(argv)
    try:
        after = _last_ts() if args.since_last else 0.0
        db = sqlite3.connect(f'file:{hermes_env.HERMES_HOME / "state.db"}?mode=ro', uri=True)
        pairs = fetch_pairs(db, args.days, after)
        kept, dropped, by_rule = [], [], Counter()
        for p in pairs:
            rule = drop_rule(p['user_text'], p['reply_text'])
            if rule:
                by_rule[rule] += 1
                dropped.append((rule, p))
            else:
                kept.append(p)
        print(f'candidates={len(pairs)} kept={len(kept)} dropped={len(dropped)} by_rule={dict(by_rule)}')
        if not args.apply:
            print('--- KEPT sample ---')
            for p in random.sample(kept, min(15, len(kept))):
                print(' ', repr(p['user_text'][:160]))
            print('--- DROPPED sample ---')
            for rule, p in random.sample(dropped, min(10, len(dropped))):
                print(f'  [{rule}]', repr(p['user_text'][:160]))
            return 0
        apply(kept)
        return 0
    except Exception as e:
        print(f'[session_embed] failed: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
