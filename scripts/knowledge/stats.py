"""Store stats for the CLI and health checks: fact count, status line, recent facts."""
import urllib.request

from knowledge.supabase_client import get_supabase, _supa_select
from knowledge.storage import _as_tags


def count_facts():
    url, key = get_supabase()
    req = urllib.request.Request(url + '/rest/v1/knowledge?select=id', method='HEAD')
    req.add_header('apikey', key)
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Prefer', 'count=exact')
    resp = urllib.request.urlopen(req, timeout=8)
    cr = resp.headers.get('Content-Range', '0-0/0')
    return int(cr.split('/')[-1]) if '/' in cr else 0

def status():
    url, key = get_supabase()
    total = count_facts()
    print(f'Knowledge DB: {total} facts stored in Supabase ({url})')

def recent(limit=10):
    rows = _supa_select(
        'select=id,text,tags,priority,source,stored_at,context_prefix'
        f'&order=stored_at.desc&limit={limit}', timeout=10)
    results = []
    for r in rows:
        prefix = r.get('context_prefix', '') or ''
        results.append({
            'id': r['id'],
            'text': (r.get('text') or '')[:120],
            'tags': _as_tags(r.get('tags')),
            'priority': r.get('priority', 'normal') or 'normal',
            'stored_at': r.get('stored_at', '') or '',
            'context_prefix': prefix,
        })
    return results
