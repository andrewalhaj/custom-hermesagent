"""
Unified search across all Hermes knowledge stores.
Fans out to: knowledge store (Supabase pgvector), skills, references, kanban.
Merges results by Fibonacci sector weight.

REF_CHUNKS env flag (read at call time, never at import time):
  unset / 'off' — identical A0 keyword behaviour (default)
  'log'         — A0 results returned synchronously; a daemon thread runs the
                  chunk search and appends one JSON line to
                  ~/.hermes/logs/ref_chunks_shadow.jsonl for shadowing.
  'on'          — chunk search results returned (capped at REF_CHUNKS_MAX_SLOTS,
                  default 2); A0 results used as fallback when chunks return [].
"""
import os, sqlite3, json, re, time, threading
from pathlib import Path
from typing import List, Dict, Any

from knowledge.search import _sector_weight

# Shadow log path — exposed as module attribute so tests can redirect it.
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
_REF_CHUNKS_SHADOW_PATH: Path = HERMES_HOME / "logs" / "ref_chunks_shadow.jsonl"
_shadow_lock = threading.Lock()


def _search_knowledge(query: str, top_k: int = 5) -> List[Dict]:
    """Search Supabase pgvector knowledge store."""
    try:
        import sys
        if str(HERMES_HOME / 'scripts') not in sys.path:
            sys.path.insert(0, str(HERMES_HOME / 'scripts'))
        from knowledge.search import search
        from knowledge.provenance import provenance
        hits = search(query, top_k=top_k)
        return [{
            'store': 'knowledge',
            'id': h.get('id', ''),
            'title': h.get('text', '')[:100],
            'content': h.get('text', ''),
            'score': float(h.get('score', 0)),
            'sector_path': h.get('sector_path', []),
            'tags': h.get('tags', []),
            'source': h.get('source', ''),
            'priority': h.get('priority', 'normal'),
            'provenance': provenance(h),
            'url': None,
        } for h in hits]
    except Exception as e:
        return []

def _search_skills(query: str, query_sector: list, top_k: int = 5) -> List[Dict]:
    """Search skills by sector overlap and keyword match."""
    import yaml
    results = []
    query_lower = query.lower()
    query_words = set(re.findall(r'\w+', query_lower))
    
    for skill_md in HERMES_HOME.glob('skills/**/SKILL.md'):
        if '.curator_backups' in str(skill_md):
            continue
        try:
            content = skill_md.read_text(errors='ignore')
            # Parse frontmatter
            if not content.startswith('---'):
                continue
            parts = content.split('---', 2)
            if len(parts) < 3:
                continue
            fm = yaml.safe_load(parts[1]) or {}
            name = fm.get('name', skill_md.parent.name)
            desc = fm.get('description', '')
            sector = fm.get('sector_path', [])
            
            # Score: keyword match + sector boost
            text_lower = (name + ' ' + desc).lower()
            text_words = set(re.findall(r'\w+', text_lower))
            keyword_score = len(query_words & text_words) / max(len(query_words), 1)
            final_score = keyword_score * _sector_weight(sector, query_sector)
            
            if final_score > 0.05:  # minimum threshold
                results.append({
                    'store': 'skill',
                    'id': name,
                    'title': name,
                    'content': desc,
                    'score': final_score,
                    'sector_path': sector,
                    'url': str(skill_md),
                })
        except Exception:
            continue
    
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_k]

def _search_references_a0(query: str, query_sector: list, top_k: int = 5) -> List[Dict]:
    """A0: original keyword-based reference search. Untouched — used as fallback."""
    results = []
    query_lower = query.lower()
    query_words = set(re.findall(r'\w+', query_lower))
    ref_dir = HERMES_HOME / 'references'

    if not ref_dir.exists():
        return []

    for ref_file in ref_dir.glob('*.md'):
        try:
            content = ref_file.read_text(errors='ignore')[:500]  # first 500 chars
            text = ref_file.stem.replace('-', ' ').replace('_', ' ') + ' ' + content
            text_words = set(re.findall(r'\w+', text.lower()))
            score = len(query_words & text_words) / max(len(query_words), 1)
            if score > 0.1:
                results.append({
                    'store': 'reference',
                    'id': ref_file.stem,
                    'title': ref_file.stem,
                    'content': content[:200],
                    'score': score,
                    'sector_path': [],
                    'url': str(ref_file),
                })
        except Exception:
            continue

    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_k]


def _fire_shadow_log_thread(query: str, a0_results: List[Dict], top_k: int) -> None:
    """Spawn a daemon thread that runs ref_chunks.search and appends to shadow log.

    Never blocks the caller; never joined. File append is guarded by _shadow_lock.
    """
    def _log():
        try:
            from knowledge import ref_chunks
            t0 = time.time()
            try:
                chunk_results = ref_chunks.search(query, top_k=top_k)
                error = None
            except Exception as e:
                chunk_results = []
                error = str(e)[:200]
            latency_ms = round((time.time() - t0) * 1000, 1)

            line = json.dumps({
                'ts':         time.time(),
                'query':      query[:200],
                'a0':         [[r['id'], r['score']] for r in (a0_results or [])],
                'chunks':     [[r['id'], r.get('chunk_idx', 0), r['score']]
                               for r in (chunk_results or [])],
                'latency_ms': latency_ms,
                'error':      error,
            })

            log_path = _REF_CHUNKS_SHADOW_PATH
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with _shadow_lock:
                with open(log_path, 'a') as f:
                    f.write(line + '\n')
        except Exception:
            pass  # daemon thread — never propagate

    t = threading.Thread(target=_log, daemon=True)
    t.start()


def _search_references(query: str, query_sector: list, top_k: int = 5) -> List[Dict]:
    """Search references. REF_CHUNKS env flag (read at call time) controls behaviour:
      unset / 'off' → A0 keyword search (unchanged)
      'log'         → A0 results + daemon shadow thread for chunk search comparison
      'on'          → chunk search results (capped at REF_CHUNKS_MAX_SLOTS=2),
                       A0 fallback when chunks return []
    """
    flag = os.environ.get('REF_CHUNKS', '').lower().strip()

    if flag not in ('log', 'on'):
        return _search_references_a0(query, query_sector, top_k)

    a0_results = _search_references_a0(query, query_sector, top_k)

    if flag == 'log':
        _fire_shadow_log_thread(query, a0_results, top_k)
        return a0_results  # A0 results returned immediately, thread runs in bg

    # flag == 'on': use chunk search, cap slots, fall back to A0 on empty
    max_slots = int(os.environ.get('REF_CHUNKS_MAX_SLOTS', '2'))
    try:
        from knowledge import ref_chunks
        chunk_results = ref_chunks.search(query, top_k=top_k)
    except Exception:
        chunk_results = []

    if not chunk_results:
        return a0_results  # fallback

    return chunk_results[:max_slots]

def _search_kanban(query: str, top_k: int = 5) -> List[Dict]:
    """Search active kanban tasks by title/body keyword match."""
    results = []
    db_path = HERMES_HOME / 'kanban.db'
    if not db_path.exists():
        return []
    
    query_words = set(re.findall(r'\w+', query.lower()))
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, body, status FROM tasks "
            "WHERE status NOT IN ('archived','done') LIMIT 100"
        ).fetchall()
        conn.close()
        
        for row in rows:
            text = ((row['title'] or '') + ' ' + (row['body'] or ''))[:500]
            text_words = set(re.findall(r'\w+', text.lower()))
            score = len(query_words & text_words) / max(len(query_words), 1)
            if score > 0.1:
                results.append({
                    'store': 'kanban',
                    'id': row['id'],
                    'title': row['title'],
                    'content': (row['body'] or '')[:200],
                    'score': score,
                    'sector_path': [],
                    'url': None,
                })
    except Exception:
        pass
    
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_k]

def unified_search(query: str, top_k: int = 10, stores: list = None) -> List[Dict]:
    """
    Fan-out search across all Hermes knowledge stores.
    Returns merged results ranked by score + sector Fibonacci boost.

    Hardened public entrypoint: always returns a list, never raises or returns
    None. Any exception in the fan-out/merge pipeline is caught and yields [].

    Args:
        query: Natural language query
        top_k: Max results to return
        stores: List of stores to search (default: all)

    Returns:
        List of dicts with keys: store, id, title, content, score, sector_path, url
    """
    try:
        result = _unified_search_impl(query, top_k=top_k, stores=stores)
        return result or []
    except Exception:
        return []

def _unified_search_impl(query: str, top_k: int = 10, stores: list = None) -> List[Dict]:
    """Internal implementation for unified_search (see wrapper above)."""
    if stores is None:
        stores = ['knowledge', 'skills', 'references', 'kanban']
    
    # Classify query into sector
    query_sector = []
    try:
        import sys
        if str(HERMES_HOME / 'scripts') not in sys.path:
            sys.path.insert(0, str(HERMES_HOME / 'scripts'))
        from knowledge.classifier import classify
        query_sector = classify(query)
    except Exception:
        pass
    
    # Fan out to all stores
    all_results = []
    
    if 'knowledge' in stores:
        all_results.extend(_search_knowledge(query, top_k=top_k) or [])
    if 'skills' in stores:
        all_results.extend(_search_skills(query, query_sector, top_k=top_k) or [])
    if 'references' in stores:
        all_results.extend(_search_references(query, query_sector, top_k=top_k) or [])
    if 'kanban' in stores:
        all_results.extend(_search_kanban(query, top_k=top_k) or [])
    
    # No sector boost here: search() boosts knowledge hits, _search_skills boosts skills — once each.
    # Sort and deduplicate
    all_results.sort(key=lambda x: x.get('score') or 0, reverse=True)

    # Deduplicate by content similarity (simple: same title)
    seen = set()
    deduped = []
    for r in all_results:
        key = (r.get('title') or '')[:50].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return deduped[:top_k]
