"""Thin urllib REST client for the Supabase pgvector backend.

Stdlib only (no supabase-py / httpx) to keep imports cheap. Embeddings come from
knowledge.embed; only the vector store and search are remote.
"""
import json
import urllib.request

from knowledge.config import env


_supabase_url = None
_supabase_key = None

def get_supabase():
    """Return (url, key) for the Supabase project, read once (env, then ~/.hermes/.env)."""
    global _supabase_url, _supabase_key
    if _supabase_url:
        return _supabase_url, _supabase_key
    _supabase_url = env('SUPABASE_URL')
    _supabase_key = env('SUPABASE_KEY') or env('SUPABASE_SERVICE_KEY')
    return _supabase_url, _supabase_key

def _supa_get(path, timeout=15):
    """GET an arbitrary Supabase REST path (caller supplies querystring). → parsed JSON."""
    url, key = get_supabase()
    req = urllib.request.Request(url + path)
    req.add_header('apikey', key)
    req.add_header('Authorization', f'Bearer {key}')
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())

def _supa_select(query, timeout=15):
    """GET /rest/v1/knowledge?<query> → list of row dicts. Replaces tbl.to_pandas()."""
    return _supa_get('/rest/v1/knowledge?' + query, timeout=timeout)

def _supa_post(path, payload, prefer='return=minimal', timeout=15):
    """POST JSON to a Supabase REST/RPC path. → parsed JSON (or {} on empty body)."""
    url, key = get_supabase()
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url + path, data=data, method='POST')
    req.add_header('apikey', key)
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Prefer', prefer)
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = resp.read()
    return json.loads(body) if body else {}

def _supa_insert(row):
    """Insert one row dict into the knowledge table. A PK conflict fails loudly (no silent overwrite)."""
    _supa_post('/rest/v1/knowledge', [row])

def _supa_all_rows(select='*', page=1000):
    """Fetch every row in the knowledge table, paginating via Range headers.
    Paginates the full table via REST instead of a single full-table scan."""
    url, key = get_supabase()
    rows = []
    start = 0
    while True:
        req = urllib.request.Request(
            url + f'/rest/v1/knowledge?select={select}&order=stored_at.asc,id.asc')
        req.add_header('apikey', key)
        req.add_header('Authorization', f'Bearer {key}')
        req.add_header('Range-Unit', 'items')
        req.add_header('Range', f'{start}-{start + page - 1}')
        resp = urllib.request.urlopen(req, timeout=20)
        batch = json.loads(resp.read())
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page:
            break
        start += page
    # Oldest-first, so rows inserted mid-walk land past the cursor instead of shifting pages. Kept as a guard:
    # a repeated id would look like a duplicate fact to dedup callers, which would then delete the only copy.
    return list({r.get('id', i): r for i, r in enumerate(rows)}.values())
