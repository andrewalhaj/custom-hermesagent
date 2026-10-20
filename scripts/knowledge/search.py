"""Semantic search over Supabase pgvector: query metadata/expansion, RPC search,
RRF merge, confidence scoring, then rerank (remote bge → cosine).
"""
import os
import sys
import time
import math
import re
import json
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_env import env  # noqa: E402

import numpy as np

from knowledge.embed import embed
from knowledge.supabase_client import _supa_post
from knowledge.storage import _as_tags
from knowledge.config import flag


# ── Query-side metadata extraction (fast regex, no LLM needed) ──
# Private host names come from the environment (alternation text, e.g. "a|b\s*c").
_HOST_NAMES = env("KB_HOST_NAME_PATTERN", "")
_HOST_KEYWORDS = [k.strip().lower() for k in env("KB_HOST_KEYWORDS", "host,server,mac mini,mac studio").split(",") if k.strip()]
_META_PATTERNS = {
    'hosts': [
        *([re.compile(r'\b(' + _HOST_NAMES + r')\b', re.I)] if _HOST_NAMES else []),
        re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b'),
    ],
    'ports': [
        re.compile(r'\b(?:port\s+|:)(\d{2,5})\b', re.I),
    ],
    'services': [
        re.compile(r'\b(nginx|docker|uvicorn|home.?assistant|ollama|llama\.cpp|supabase|postgres|fastapi|react|vite)\b', re.I),
    ],
    'systems': [
        re.compile(r'\b(wall-dashboard|ha-dashboard|hermes-webui|kanban|dispatcher|knowledge-store|mealio|linear|sentry|figma|notion|honcho)\b', re.I),
        re.compile(r'\b(wall\s+dashboard|ha\s+dashboard)\b', re.I),
    ],
}

def _extract_query_metadata(query):
    """Extract metadata from a user query using fast regex patterns.
    Returns dict matching the metadata jsonb schema, or None if nothing found."""
    meta = {}
    for field, patterns in _META_PATTERNS.items():
        values = []
        for pat in patterns:
            for m in pat.finditer(query):
                val = m.group(1).lower()
                # Normalize whitespace → hyphen, collapse runs
                val = '-'.join(val.split())
                if val not in values:
                    values.append(val)
        if values:
            meta[field] = values
    return meta if meta else None

# Stopwords for query expansion (variant generation)
_STOPWORDS = {'the','a','an','is','are','was','were','be','been','being','have','has','had',
              'do','does','did','will','would','shall','should','may','might','must','can',
              'could','i','me','my','we','our','you','your','he','she','it','they','them',
              'this','that','these','those','what','which','who','whom','when','where',
              'why','how','all','each','every','both','few','more','most','other','some',
              'such','no','not','only','own','same','so','than','too','very','just',
              'about','above','after','again','against','between','during','into','through',
              'under','with','of','in','to','for','on','at','by','from','up','down','out',
              'off','over','and','but','or','nor','if','then','else','as','until','while',
              'any','does','really','actually','basically','like'}

def _expand_queries(query):
    """Generate 2-3 variant phrasings for query expansion. No LLM needed."""
    words = [w for w in query.lower().split() if w not in _STOPWORDS]
    variants = [query]  # variant 0: original
    if words:
        # Variant 1: first 5 content words
        variants.append(' '.join(words[:5]))
        # Variant 2: key terms only (words >3 chars, up to 5)
        key_terms = [w for w in words if len(w) > 3][:5]
        if key_terms and key_terms != words[:len(key_terms)]:
            variants.append(' '.join(key_terms))
    # Deduplicate while preserving order
    seen = set()
    return [v for v in variants if not (v in seen or seen.add(v))]

def _single_search(query, top_k, pool_size, tag_filter, min_priority, metadata_filter=None, sector_filter=None):
    """Run one search against match_knowledge_v4. Returns raw hits (pre-threshold)."""
    qvec = embed([query])[0]
    qvec = qvec.tolist() if hasattr(qvec, 'tolist') else list(qvec)
    payload = {'query_embedding': qvec, 'query_text': query, 'match_count': pool_size}
    if min_priority:
        payload['min_priority'] = min_priority
    if tag_filter:
        payload['filter_tags'] = list(tag_filter)
    if metadata_filter:
        payload['metadata_filter'] = metadata_filter
    if sector_filter:
        payload['sector_filter'] = sector_filter
    try:
        results = _supa_post('/rest/v1/rpc/match_knowledge_v4', payload, prefer='')
    except Exception as e:
        print(f"[knowledge] match_knowledge_v4 RPC failed: {e}", file=sys.stderr)
        return []
    if not isinstance(results, list):
        return []

    order = {'critical': 0, 'high': 1, 'normal': 2, 'low': 3}
    now = time.time()
    hits = []
    for r in results:
        tags = _as_tags(r.get('tags'))
        priority = r.get('priority', 'normal') or 'normal'
        if tag_filter and not any(t in tags for t in tag_filter):
            continue
        if min_priority and order.get(priority, 2) > order.get(min_priority, 2):
            continue
        score = float(r.get('similarity', 0.0))
        # Importance: small additive tie-breaker (not multiplier — was overpowering relevance)
        score += (0.04 - order.get(priority, 2) * 0.01)
        # Recency: small additive tie-breaker (max 0.02, was 0.10 — was overpowering relevance)
        age_days = _age_days(r.get('stored_at'), now) or 0.0
        score += math.exp(-age_days / 14.0) * 0.02
        hits.append({
            'id': r['id'],
            'text': r['text'],
            'score': round(score, 4),
            'tags': tags,
            'priority': priority,
            'source': r.get('source', '') or '',
            'context_prefix': r.get('context_prefix', '') or '',
            'match_source': r.get('match_source', ''),
            'sector_path': r.get('sector_path', []),
            'stored_at': r.get('stored_at', ''),
        })
    return hits

def _age_days(stored_at, now):
    """Age in days of an ISO string or epoch timestamp; None when missing or unparseable."""
    try:
        if isinstance(stored_at, str) and stored_at:
            ts = datetime.fromisoformat(stored_at.replace('Z', '+00:00')).timestamp()
        elif isinstance(stored_at, (int, float)):
            ts = float(stored_at)
        else:
            return None
    except ValueError:
        return None
    return (now - ts) / 86400.0

def _rrf_merge(variant_results):
    """Merge results from multiple query variants via reciprocal rank fusion (k=20)."""
    rrf_scores = {}
    hits_by_id = {}
    for hits in (variant_results or []):
        for rank, h in enumerate(hits or []):
            rid = h['id']
            rrf_scores[rid] = rrf_scores.get(rid, 0) + 1.0 / (20 + rank)
            if rid not in hits_by_id:
                hits_by_id[rid] = h
    merged = []
    for rid, score in rrf_scores.items():
        h = hits_by_id[rid]
        h['score'] = round(score, 4)
        merged.append(h)
    merged.sort(key=lambda h: h['score'], reverse=True)
    return merged

def _cosine_rerank(hits, query, top_k):
    """Rerank: remote Studio bge-reranker (~0.4s/query), else cosine similarity.

    The local CPU cross-encoder was removed (D4 ablation 2026-10-16): never reached while
    the remote is up, and 48s+/query when it was — far past the prefetch timeout.
    """
    hits = hits or []
    if not hits:
        return []
    try:
        remote = _remote_rerank(hits, query, top_k)
        if remote is not None:
            return remote
    except Exception:
        pass
    try:
        qvec = embed([query])[0]
        for h in hits:
            hvec = embed([h['text']])[0]
            h['score'] = round(_cosine(qvec, hvec), 4)
        hits.sort(key=lambda h: h['score'], reverse=True)
    except Exception:
        pass
    return hits[:top_k]

def _remote_rerank(hits, query, top_k):
    """Rerank via the LH02 llama-server /v1/rerank endpoint.

    Returns reranked hits, or None to signal fallback (endpoint disabled or bad shape).
    Scores are min-max normalized to 0-1. Timeout is tight (5s) so a down node degrades
    to cosine instead of stalling the caller.
    """
    url = env('RERANK_REMOTE_URL')
    if not url or url.lower() in ('0', 'off', 'false', 'disabled'):
        return None
    docs = [(h.get('text') or '')[:2000] for h in hits]
    body = json.dumps({'model': 'bge-reranker-v2-m3', 'query': query,
                       'documents': docs}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={'Content-Type': 'application/json'})
    resp = json.load(urllib.request.urlopen(req, timeout=5))
    results = resp.get('results')
    if not results or len(results) != len(hits):
        return None
    scores = [0.0] * len(hits)
    for it in results:
        scores[it['index']] = float(it['relevance_score'])
    smin, smax = min(scores), max(scores)
    srange = smax - smin if smax != smin else 1.0
    for h, s in zip(hits, scores):
        h['rerank_raw'] = round(s, 4)  # raw cross-encoder logit: >0 ≈ relevant,
        # absolute scale survives normalization — used for abstention detection
        h['score'] = round((s - smin) / srange, 4)
    hits.sort(key=lambda h: h['score'], reverse=True)
    return hits[:top_k]

def _cosine(a, b):
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    na = np.linalg.norm(a); nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))

_IMPORTANCE = {'critical': 1.0, 'high': 0.7, 'normal': 0.5, 'low': 0.3}

# Real Fibonacci: L0=+8, L1=+5, L2=+3, L3=+2
# Max boost: 1.0 + (8+5+3+2) × 0.15 = 3.70× (full 4-level match)
_FIB_WEIGHTS = [8, 5, 3, 2]

def _detect_query_sector(query):
    """Classify a query into a sector_path using the embedding classifier.
    Falls back to keyword regex if the classifier is unavailable.

    GUARD: refuses input >200 chars (this is QUERY-tuned, 5-80 chars). Facts get
    sector_path from webhooks/manual-enrichment, not keyword inference — bulk
    classifying fact text via this path destroyed retrieval accuracy (73%→60% P@5).
    """
    if len(query) > 200:
        raise ValueError(
            f"_detect_query_sector refuses text >200 chars (got {len(query)}). "
            f"Use knowledge.classifier.classify() directly for long text."
        )
    try:
        from knowledge.classifier import classify
        result = classify(query)
        if result:
            return result
    except Exception:
        pass
    # Fallback: original keyword regex (preserved below).
    return _detect_query_sector_keyword(query)

def _detect_query_sector_keyword(query):
    """Keyword fallback for _detect_query_sector (which enforces the ≤200-char guard).
    Returns list like ['infrastructure', 'hosting', 'deploy'] or [] for unclassified."""
    q = query.lower()
    # Infrastructure
    if any(w in q for w in ['deploy', 'dispatcher', 'dashboard', 'docker', 'systemd', 'service']):
        return ['infrastructure', 'hosting', 'deploy']
    if any(w in q for w in ['config', 'yaml', 'gateway', 'provider', 'model', 'profile']):
        return ['infrastructure', 'hosting', 'configure']
    if any(w in q for w in ['tailscale', 'tailnet', 'vps', 'cloudflare', 'tunnel']):
        return ['infrastructure', 'networking', 'tailscale']
    if any(w in q for w in _HOST_KEYWORDS):
        return ['infrastructure', 'hosting', 'monitor']
    # Development
    if any(w in q for w in ['coder fleet', 'coder-b', 'coder-c', 'coder-d', 'coding agent', 'coding subtask']):
        return ['development', 'coder-fleet', 'dispatch']
    if any(w in q for w in ['webui', 'frontend', 'react', 'web ui', 'typescript']):
        return ['development', 'webui', 'build']
    if any(w in q for w in ['knowledge', 'pgvector', 'embedding', 'semantic', 'search']):
        return ['development', 'knowledge', 'search']
    if any(w in q for w in ['graph', 'community', 'mcp']):
        return ['development', 'knowledge', 'graph']
    # Monitoring
    if any(w in q for w in ['sentry']) and any(w in q for w in ['integration', 'sdk', 'setup', 'pipeline', 'hmac']):
        return ['monitoring', 'sentry', 'pipeline']
    if any(w in q for w in ['linear', 'kanban']):
        return ['monitoring', 'linear', 'issue']
    if any(w in q for w in ['sentry', 'alert', 'error']):
        return ['monitoring', 'sentry', 'alert']
    if any(w in q for w in ['webhook', 'hook']):
        return ['monitoring', 'webhooks', 'dispatch']
    # Home automation
    if any(w in q for w in ['wall dash', 'wall-dash', 'home assistant', 'ha dashboard']):
        return ['home-automation', 'dashboard', 'display']
    # User
    if any(w in q for w in ['prefer', 'style', 'user', 'hardware', 'ssh']):
        return ['user', 'preferences', 'workflow']
    return []

def _build_search_context(query):
    """Compute query-side retrieval signals once per request.

    Returns a dict with:
      - metadata_filter: dict from _extract_query_metadata (or None).
                         Only populated when metadata facts are actually indexed
                         in the store (hosts/ports/services metadata fields).
                         Passing an unmatched metadata_filter to the RPC returns
                         0 results — so this is gated behind a populated store check.
      - sector_filter:   top-level sector domain string (or None).
                         Gated behind flag('SECTOR_FILTER') at call time.
      - query_path:      full sector_path list from _detect_query_sector (or [])

    Centralizing this avoids re-running _detect_query_sector and the regex
    metadata extraction multiple times for the same query within one search.
    """
    # Sector from the opening only: the classifier refuses >200 chars, and a raise here
    # made search() return [] for every long user message (no memory recall).
    query_path = _detect_query_sector(query[:200])
    # metadata_filter: only wire through when METADATA_FILTER env is explicitly enabled.
    # The store does not have populated hosts/ports/services fields on most facts —
    # passing a metadata_filter against an unpopulated store returns 0 results (kills recall).
    meta = _extract_query_metadata(query) if flag('METADATA_FILTER') else None
    # sector_filter: gated at call time (not import time) so toggling the env var takes effect.
    sector = query_path[0] if (query_path and flag('SECTOR_FILTER')) else None
    return {
        "metadata_filter": meta,
        "sector_filter": sector,
        "query_path": query_path,
    }

def _sector_weight(fact_path, query_path):
    """Compute Fibonacci overlap weight between fact sector_path and query sector_path.
    
    Returns a multiplier ≥ 1.0. Full match at all levels = 3.70× (8+5+3+2).
    Partial match gets proportional weight. No match = 1.0 (neutral).
    """
    if not fact_path or not query_path:
        return 1.0
    weight = 1.0
    for depth in range(min(len(fact_path), len(query_path), len(_FIB_WEIGHTS))):
        if fact_path[depth] == query_path[depth]:
            weight += _FIB_WEIGHTS[depth] * 0.15  # Scale: full match = 1.0 + (8+5+3+2)*0.15 = 3.70
        else:
            break  # Hierarchy must match top-down
    return round(weight, 4)

# ── Confidence scoring ─────────────────────────────────────────────────

def _confidence_score(hit, query, top_hits=None, query_path=None):
    """Multi-signal agreement score (0-1). Higher = more signals agree.
    
    Signals checked:
      - vector: match_source contains 'vector'
      - fts: match_source contains 'fts'
      - sector: fact and query share at least domain level

    Scoring:
      ≥3 signals: HIGH   (0.90-1.00)
      2 signals:  MEDIUM (0.60-0.89)
      1 signal:   LOW    (0.30-0.59)
      0 signals:  NONE   (<0.30)
    """
    signals = 0
    
    # Vector agreement
    ms = hit.get('match_source', '')
    if 'vector' in ms:
        signals += 1
    
    # FTS agreement
    if 'fts' in ms:
        signals += 1
    
    # Sector agreement
    fact_path = hit.get('sector_path', [])
    if fact_path:
        if query_path is None:
            query_path = _detect_query_sector(query)
        if query_path and fact_path[0] == query_path[0]:
            signals += 1

    # Map to score band
    if signals >= 3:
        return round(0.85 + signals * 0.05, 2)  # 0.95-1.00
    elif signals == 2:
        return round(0.60 + signals * 0.05, 2)  # 0.70
    elif signals == 1:
        return round(0.30 + signals * 0.05, 2)  # 0.35
    else:
        return 0.10

def search(query, top_k=8, tag_filter=None, min_priority=None):
    """Semantic search over the Supabase pgvector knowledge store.

    Hardened public entrypoint: returns a list of hits and NEVER raises or
    returns None. Any exception from the internal pipeline (RPC failure,
    unresponsive embed/rerank backend, malformed data) is caught and turned
    into an empty list so callers can safely iterate/index the result.
    """
    try:
        result = _search_impl(query, top_k=top_k, tag_filter=tag_filter,
                              min_priority=min_priority)
        return result or []
    except Exception as e:
        print(f"[knowledge] search() failed, returning []: {e}", file=sys.stderr)
        return []

def _search_impl(query, top_k=8, tag_filter=None, min_priority=None):
    """Over-fetch, then confidence, optional rerank."""
    search_query = query

    pool = max(top_k * 3, 15)

    # --- build search context once (metadata + sector signals) ---
    ctx = _build_search_context(search_query)
    metadata_filter = ctx["metadata_filter"]
    query_path = ctx["query_path"]

    # --- sector filter (env-gated) ---
    sector_filter = ctx["sector_filter"] if flag('SECTOR_FILTER') else None

    # --- query expansion (multi-variant → RRF merge) ---
    if flag('QUERY_EXPAND'):
        variants = _expand_queries(search_query)
        variant_results = []
        for v in variants:
            vhits = _single_search(v, top_k, pool, tag_filter, min_priority, metadata_filter, sector_filter) or []
            if vhits:
                variant_results.append(vhits)
        if variant_results:
            hits = _rrf_merge(variant_results) or []
        else:
            hits = []
    else:
        hits = _single_search(search_query, top_k, pool, tag_filter, min_priority, metadata_filter, sector_filter) or []

    hits = hits or []
    hits.sort(key=lambda h: h['score'], reverse=True)

    # --- Confidence scoring (multi-signal agreement) ---
    for h in hits:
        h['confidence'] = _confidence_score(h, query, query_path=query_path)

    # --- rerank (remote bge, cosine fallback) ---
    if flag('RERANK') and hits:
        hits = _cosine_rerank(hits, query, top_k) or []
        return hits

    return hits[:top_k]
