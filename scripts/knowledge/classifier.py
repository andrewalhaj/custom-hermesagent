"""Embedding-based taxonomy classifier (Phase 1A).

Implements a multi-stage, length-adaptive pipeline that maps arbitrary text to a
taxonomy ``sector_path`` (or ``[]`` to abstain):

    Stage 0  Hash cache          SHA-256(text) -> cached sector_path (SQLite)
    Stage 1  Keyword fast-path   high-confidence (>=0.85) obvious cases only
    Stage 2  Prototype centroid  cosine vs L0/L1/L2 centroids (Snell 2017)
    Stage 3a kNN reject scorer   mean dist to k=5 nearest anchors (Sun 2022)

The embedder stays FROZEN (Kumar 2022): no fine-tuning, so the kNN reject
geometry is preserved. Centroids/anchors are generated artifacts built by
``build_centroids`` (see build_centroids.py).

Public API (signatures are FIXED):
    classify(text, threshold=None) -> list[str]
    classify_batch(texts, threshold=None) -> list[list[str]]
    explain(text) -> dict
    build_centroids(taxonomy_path, output_dir, calibrate=True) -> None
"""
import os
import re
import json
import time
import pickle
import sqlite3
import hashlib

import numpy as np

from knowledge.embed import embed
from knowledge.config import DB_DIR, flag

# ── Paths ────────────────────────────────────────────────────────────────
DATA_DIR = os.path.dirname(os.path.abspath(__file__))          # knowledge/
TAXONOMY_PATH = os.path.join(DATA_DIR, 'taxonomy.json')
CENTROIDS_PATH = os.path.join(DATA_DIR, 'centroids.pkl')
ANCHORS_PATH = os.path.join(DATA_DIR, 'anchors.pkl')
THRESHOLD_PATH = os.path.join(DATA_DIR, 'classification_threshold.json')
CACHE_DB = os.path.join(DB_DIR, 'classification_cache.db')

# ── Thresholds (defaults — overridden by classification_threshold.json) ──
L0_THRESHOLD = 0.40
L1_THRESHOLD = 0.35
L2_THRESHOLD = 0.30
KNN_REJECT_DEFAULT = 0.65      # mean dist to 5 nearest anchors above this → abstain
KNN_K = 5

# ── Lazy module-level caches ─────────────────────────────────────────────
_TAXONOMY = None
_CENTROIDS = None
_ANCHOR_MATRIX = None
_ANCHOR_META = None
_THRESHOLDS = None


# ════════════════════════════════════════════════════════════════════════
#  Loaders
# ════════════════════════════════════════════════════════════════════════
def _load_taxonomy():
    global _TAXONOMY
    if _TAXONOMY is None:
        with open(TAXONOMY_PATH) as f:
            _TAXONOMY = json.load(f)
    return _TAXONOMY


def _load_centroids():
    """Return the centroids dict (np arrays, normalized) or None if not built."""
    global _CENTROIDS
    if _CENTROIDS is None:
        if not os.path.exists(CENTROIDS_PATH):
            return None
        with open(CENTROIDS_PATH, 'rb') as f:
            _CENTROIDS = pickle.load(f)
    return _CENTROIDS


def _load_anchors():
    """Return (matrix (N,768), meta list) or (None, None) if not built."""
    global _ANCHOR_MATRIX, _ANCHOR_META
    if _ANCHOR_MATRIX is None:
        if not os.path.exists(ANCHORS_PATH):
            return None, None
        with open(ANCHORS_PATH, 'rb') as f:
            data = pickle.load(f)
        _ANCHOR_MATRIX = np.asarray(data['matrix'], dtype=np.float32)
        _ANCHOR_META = data['meta']
    return _ANCHOR_MATRIX, _ANCHOR_META


def _load_thresholds():
    """Return calibrated thresholds, falling back to module defaults."""
    global _THRESHOLDS
    if _THRESHOLDS is None:
        t = {'l0': L0_THRESHOLD, 'knn': KNN_REJECT_DEFAULT}
        try:
            with open(THRESHOLD_PATH) as f:
                raw = json.load(f)
            t['l0'] = float(raw.get('centroid_l0_threshold',
                            raw.get('centroid_cosine_threshold', L0_THRESHOLD)))
            t['knn'] = float(raw.get('knn_distance_threshold', KNN_REJECT_DEFAULT))
        except Exception:
            pass
        _THRESHOLDS = t
    return _THRESHOLDS


def _reset_caches():
    """Drop lazy caches so freshly-built artifacts are picked up (used post-build)."""
    global _CENTROIDS, _ANCHOR_MATRIX, _ANCHOR_META, _THRESHOLDS
    global _CACHE_CONN
    _CENTROIDS = _ANCHOR_MATRIX = _ANCHOR_META = _THRESHOLDS = None
    if _CACHE_CONN:
        _CACHE_CONN[1].close()
    _CACHE_CONN = None


# ════════════════════════════════════════════════════════════════════════
#  Embedding helpers
# ════════════════════════════════════════════════════════════════════════
def _normalize(vec):
    v = np.asarray(vec, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _embed_one(text):
    """Embed a single string → normalized np array, or None on failure."""
    try:
        return _normalize(embed([text])[0])
    except Exception:
        return None


def _embed_many(texts):
    """Embed many strings → (N,768) normalized matrix, or None on failure."""
    try:
        vecs = embed(texts)
        mat = np.asarray(vecs, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return mat / norms
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════════
#  Stage 0 — hash cache (SQLite)
# ════════════════════════════════════════════════════════════════════════
def _cache_hash(text, threshold=None):
    """Cache key. An explicit threshold changes the answer, so it is part of the key."""
    if threshold is not None:
        text = f'{text}\0thr={threshold}'
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


_CACHE_CONN = None  # (path, connection), opened lazily once per process


def _cache_conn():
    global _CACHE_CONN
    if _CACHE_CONN is None or _CACHE_CONN[0] != CACHE_DB:
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(CACHE_DB, timeout=5, check_same_thread=False)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache "
            "(hash TEXT PRIMARY KEY, path TEXT, stored_at REAL)")
        _CACHE_CONN = (CACHE_DB, conn)
    return _CACHE_CONN[1]


def _cache_get(h):
    try:
        row = _cache_conn().execute("SELECT path FROM cache WHERE hash = ?", (h,)).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _cache_put(h, path):
    try:
        conn = _cache_conn()
        conn.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                     (h, json.dumps(path), time.time()))
        conn.commit()
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════
#  Stage 1 — keyword fast-path
# ════════════════════════════════════════════════════════════════════════
# Each rule: a valid taxonomy path + the keywords that, when present together,
# make that path unambiguous. confidence = fraction of the rule's keywords found.
# Single-keyword rules fire at 1.0 only for genuinely unambiguous terms.
_KEYWORD_RULES = [
    # infrastructure
    (['infrastructure', 'hosting', 'deploy'], ['systemd']),
    (['infrastructure', 'hosting', 'deploy'], ['docker', 'compose']),
    (['infrastructure', 'hosting', 'configure'], ['config.yaml']),
    (['infrastructure', 'networking', 'tunnel'], ['cloudflare', 'tunnel']),
    (['infrastructure', 'networking', 'tunnel'], ['tailscale']),
    (['infrastructure', 'networking', 'dns'], ['nginx']),
    (['infrastructure', 'storage', 'database'], ['pgvector']),
    (['infrastructure', 'storage', 'database'], ['supabase']),
    # development
    (['development', 'integrations', 'mcp'], ['mcp', 'server']),
    (['development', 'webui', 'frontend'], ['react', 'vite']),
    # monitoring
    (['monitoring', 'alerting', 'sentry'], ['sentry', 'hmac']),
    (['monitoring', 'alerting', 'sentry'], ['sentry']),
    (['monitoring', 'alerting', 'linear'], ['linear']),
    # home-automation
    (['home-automation', 'dashboard', 'wall-dash'], ['wall-dash']),
    (['home-automation', 'dashboard', 'ha-fusion'], ['ha-fusion']),
    (['home-automation', 'devices', 'lights'], ['govee']),
    # user
    (['user', 'preferences', 'style'], ['terse']),
]


def _kw_tokens(text):
    return text.lower()


def _keyword_confidence(text, path):
    """Fraction of keywords matched / total keywords, for the best rule whose
    path equals `path`. Returns 0.0 if no rule matches that path."""
    low = text.lower()
    best = 0.0
    for rpath, kws in _KEYWORD_RULES:
        if rpath != list(path):
            continue
        matched = sum(1 for k in kws if k in low)
        conf = matched / len(kws) if kws else 0.0
        best = max(best, conf)
    return best


def _keyword_best(text):
    """Return (path, confidence) for the highest-confidence matching rule."""
    low = text.lower()
    best_path, best_conf = [], 0.0
    for rpath, kws in _KEYWORD_RULES:
        matched = sum(1 for k in kws if k in low)
        conf = matched / len(kws) if kws else 0.0
        if conf > best_conf:
            best_conf, best_path = conf, list(rpath)
    return best_path, best_conf


def _keyword_fastpath(text):
    """Return a path only if a rule fires at confidence >= 0.85, else []."""
    path, conf = _keyword_best(text)
    return path if conf >= 0.85 else []


def _keyword_fastpath_only(text):
    """Keyword-only classification for very short text (<10 chars)."""
    return _keyword_fastpath(text)


# ════════════════════════════════════════════════════════════════════════
#  Stage 2 — prototype centroid match (Snell 2017)
# ════════════════════════════════════════════════════════════════════════
def _l0_scores(vec, centroids):
    return {dom: float(np.dot(vec, c)) for dom, c in centroids.get('l0', {}).items()}


def _best_child(vec, children):
    """Return (key, score) of the highest-cosine child centroid, or (None, -1)."""
    best_k, best_s = None, -1.0
    for k, c in children.items():
        s = float(np.dot(vec, c))
        if s > best_s:
            best_s, best_k = s, k
    return best_k, best_s


def _centroid_stage(vec, centroids, l0_threshold):
    """Top-down hierarchical centroid classification. Returns a breakdown dict."""
    scores = _l0_scores(vec, centroids)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    out = {
        'l0_scores': {k: round(v, 4) for k, v in scores.items()},
        'path': [], 'l0_score': 0.0, 'l1_score': None, 'l2_score': None,
        'margin': None,
    }
    if not ranked:
        return out
    top_dom, top_score = ranked[0]
    out['l0_score'] = round(top_score, 4)
    out['margin'] = round(top_score - ranked[1][1], 4) if len(ranked) > 1 else round(top_score, 4)
    if top_score < l0_threshold:
        return out  # below L0 acceptance → no path

    path = [top_dom]
    # L1
    l1_children = centroids.get('l1', {}).get(top_dom, {})
    sub, sub_score = _best_child(vec, l1_children)
    if sub is not None and sub_score >= L1_THRESHOLD:
        path.append(sub)
        out['l1_score'] = round(sub_score, 4)
        # L2
        l2_children = centroids.get('l2', {}).get(top_dom, {}).get(sub, {})
        leaf, leaf_score = _best_child(vec, l2_children)
        if leaf is not None and leaf_score >= L2_THRESHOLD:
            path.append(leaf)
            out['l2_score'] = round(leaf_score, 4)
    out['path'] = path
    return out


# ════════════════════════════════════════════════════════════════════════
#  Stage 3a — kNN reject scorer (Sun 2022)
# ════════════════════════════════════════════════════════════════════════
def _knn_distance(vec, matrix, k=KNN_K):
    """Mean cosine distance to the k nearest anchor rows (any node)."""
    sims = matrix @ vec                       # rows + vec are unit-normalized
    dists = 1.0 - sims
    k = min(k, dists.shape[0])
    nearest = np.partition(dists, k - 1)[:k]
    return float(np.mean(nearest))


# ════════════════════════════════════════════════════════════════════════
#  Length-adaptive helpers
# ════════════════════════════════════════════════════════════════════════
_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+|\n+')


def _split_to_sentences(text, max_chunks=3):
    parts = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    if not parts:
        parts = [text]
    return parts[:max_chunks]


# ════════════════════════════════════════════════════════════════════════
#  Core pipeline
# ════════════════════════════════════════════════════════════════════════
def _blank_result():
    return {
        'path': [], 'stage': 'abstain',
        'signals': {'centroid_margin': None, 'knn_distance': None},
        'l0_scores': {}, 'confidence': 0.0, 'abstain_reason': None,
    }


def _finish_from_vec(text, vec, threshold, h, use_cache, result):
    """Run Stage 2 → 3a on a precomputed embedding."""
    centroids = _load_centroids()
    if centroids is None:
        result['abstain_reason'] = 'centroids not built (run build_centroids.py)'
        return result

    thr = _load_thresholds()
    l0_thr = threshold if threshold is not None else thr['l0']
    cs = _centroid_stage(vec, centroids, l0_thr)
    result['l0_scores'] = cs['l0_scores']
    result['signals']['centroid_margin'] = cs['margin']

    if not cs['path']:
        result['abstain_reason'] = (
            f"centroid L0 score {cs['l0_score']} < threshold {round(l0_thr, 3)}")
        return result

    # Stage 3a — kNN reject gate
    matrix, _meta = _load_anchors()
    if matrix is not None:
        knn = _knn_distance(vec, matrix)
        result['signals']['knn_distance'] = round(knn, 4)
        if knn > thr['knn']:
            result['abstain_reason'] = (
                f"kNN distance {round(knn, 4)} > threshold {round(thr['knn'], 3)} "
                f"(out of distribution)")
            return result

    # Passed the kNN gate: accept the centroid candidate
    result.update(path=cs['path'], stage='centroid', confidence=cs['l0_score'])
    if use_cache:
        _cache_put(h, cs['path'])
    return result


def _finish_chunked(text, threshold, h, use_cache, result):
    """300–800 char path: split to sentences, majority-vote L0, classify the winner."""
    centroids = _load_centroids()
    if centroids is None:
        result['abstain_reason'] = 'centroids not built (run build_centroids.py)'
        return result
    sentences = _split_to_sentences(text, max_chunks=3)
    mat = _embed_many(sentences)
    if mat is None:
        result['abstain_reason'] = 'embedding unavailable'
        return result
    # L0 vote per sentence
    votes, by_l0 = {}, {}
    for i in range(mat.shape[0]):
        scores = _l0_scores(mat[i], centroids)
        if not scores:
            continue
        dom = max(scores, key=scores.get)
        votes[dom] = votes.get(dom, 0) + 1
        by_l0.setdefault(dom, []).append(mat[i])
    if not votes:
        result['abstain_reason'] = 'no L0 vote (centroids empty)'
        return result
    winner = max(votes, key=votes.get)
    # Mean of the winning L0's sentence vectors → full pipeline on that vector
    vec = _normalize(np.mean(np.vstack(by_l0[winner]), axis=0))
    return _finish_from_vec(text, vec, threshold, h, use_cache, result)


def _run_pipeline(text, threshold=None, use_cache=True):
    """Full classification pipeline. Returns the explain-shaped result dict."""
    result = _blank_result()
    text = (text or '').strip()
    if not text:
        result['abstain_reason'] = 'empty input'
        return result

    h = _cache_hash(text, threshold)

    # Stage 0 — cache
    if use_cache:
        cached = _cache_get(h)
        if cached is not None:
            result.update(path=cached, stage='cache',
                          confidence=1.0 if cached else 0.0)
            return result

    # Length routing — very short text: keyword fast-path only
    if len(text) < 10:
        path = _keyword_fastpath_only(text)
        if path:
            result.update(path=path, stage='keyword',
                          confidence=round(_keyword_confidence(text, path), 4))
            if use_cache:
                _cache_put(h, path)
        else:
            result['abstain_reason'] = 'short text (<10 chars), no keyword match'
        return result

    # Stage 1 — keyword fast-path (high confidence only)
    kpath, kconf = _keyword_best(text)
    if kpath and kconf >= 0.85:
        result.update(path=kpath, stage='keyword', confidence=round(kconf, 4))
        if use_cache:
            _cache_put(h, kpath)
        return result

    # Length routing — long / medium text
    if len(text) > 800:
        vec = _embed_one(text[:400])
        if vec is None:
            result['abstain_reason'] = 'embedding unavailable'
            return result
        return _finish_from_vec(text, vec, threshold, h, use_cache, result)
    if 300 < len(text) <= 800:
        return _finish_chunked(text, threshold, h, use_cache, result)

    # Normal text (10–300 chars)
    vec = _embed_one(text)
    if vec is None:
        result['abstain_reason'] = 'embedding unavailable'
        return result
    return _finish_from_vec(text, vec, threshold, h, use_cache, result)


# ════════════════════════════════════════════════════════════════════════
#  Public API
# ════════════════════════════════════════════════════════════════════════
def classify(text, threshold=None):
    """Classify text into a taxonomy sector_path. Returns [] to abstain.

    Never raises — all errors are caught and return []."""
    try:
        return _run_pipeline(text, threshold=threshold, use_cache=True)['path']
    except Exception:
        return []


def classify_batch(texts, threshold=None):
    """Batch classify. Pre-warms the embed LRU cache with one batched embed()
    call so per-item classification reuses the cached vectors."""
    if not texts:
        return []
    # Pre-warm: embed the primary string each normal/long item will embed.
    warm = []
    for t in texts:
        t = (t or '').strip()
        if len(t) > 800:
            warm.append(t[:400])
        elif 10 <= len(t) <= 300:
            warm.append(t)
    if warm:
        try:
            embed(warm)
        except Exception:
            pass
    return [classify(t, threshold=threshold) for t in texts]


def explain(text):
    """Return the full scoring breakdown for debugging (does not write cache)."""
    try:
        return _run_pipeline(text, threshold=None, use_cache=True)
    except Exception as e:
        r = _blank_result()
        r['abstain_reason'] = f'error: {e}'
        return r


def build_centroids(taxonomy_path, output_dir, calibrate=True):
    """Build centroids.pkl + anchors.pkl from a taxonomy file.

    Optionally runs the calibration sweep (writes classification_threshold.json).
    """
    with open(taxonomy_path) as f:
        taxonomy = json.load(f)
    nodes = taxonomy.get('nodes', [])

    # Collect every anchor, embed in ONE batched call.
    all_texts, owner = [], []
    for node in nodes:
        for a in node.get('anchors', []):
            all_texts.append(a)
            owner.append(tuple(node['path']))
    if not all_texts:
        raise ValueError('taxonomy has no anchors')
    print(f"[build] embedding {len(all_texts)} anchors across {len(nodes)} nodes...")
    mat = _embed_many(all_texts)
    if mat is None:
        raise RuntimeError('embedding failed — cannot build centroids')

    # Group anchor vectors by node path.
    vecs_by_path = {}
    for i, p in enumerate(owner):
        vecs_by_path.setdefault(p, []).append(mat[i])

    centroids = {'l0': {}, 'l1': {}, 'l2': {}}
    for path, vlist in vecs_by_path.items():
        centroid = _normalize(np.mean(np.vstack(vlist), axis=0))
        if len(path) == 1:
            centroids['l0'][path[0]] = centroid
        elif len(path) == 2:
            centroids['l1'].setdefault(path[0], {})[path[1]] = centroid
        elif len(path) == 3:
            centroids['l2'].setdefault(path[0], {}).setdefault(path[1], {})[path[2]] = centroid

    out_centroids = os.path.join(output_dir, 'centroids.pkl')
    with open(out_centroids, 'wb') as f:
        pickle.dump(centroids, f)
    print(f"[build] wrote {out_centroids} "
          f"(L0={len(centroids['l0'])} "
          f"L1={sum(len(v) for v in centroids['l1'].values())} "
          f"L2={sum(len(s) for v in centroids['l2'].values() for s in v.values())})")

    # Flatten ALL anchors into the kNN reject matrix.
    meta = [{'path': list(owner[i]), 'text': all_texts[i]} for i in range(len(all_texts))]
    out_anchors = os.path.join(output_dir, 'anchors.pkl')
    with open(out_anchors, 'wb') as f:
        pickle.dump({'matrix': mat.astype(np.float32), 'meta': meta}, f)
    print(f"[build] wrote {out_anchors} (matrix shape {mat.shape})")

    _reset_caches()  # pick up freshly-built artifacts in-process

    if calibrate:
        _calibrate(centroids, mat, output_dir)


# ════════════════════════════════════════════════════════════════════════
#  Calibration sweep (Geifman risk-coverage + Guo 2017)
# ════════════════════════════════════════════════════════════════════════
def _l0_predict(vec, centroids):
    scores = _l0_scores(vec, centroids)
    if not scores:
        return None, 0.0
    dom = max(scores, key=scores.get)
    return dom, scores[dom]


def _calibrate(centroids, anchor_matrix, output_dir, target_risk=0.05):
    """Sweep centroid + kNN thresholds over labeled facts; save best to JSON.

    selective_risk = fraction of ACCEPTED predictions whose L0 != true L0.
    coverage       = fraction of all labeled facts accepted.
    Pick the threshold with selective_risk <= target_risk maximizing coverage.
    """
    try:
        from knowledge.supabase_client import _supa_all_rows
        rows = _supa_all_rows(select='id,text,sector_path')
    except Exception as e:
        print(f"[calibrate] could not load facts ({e}); writing default thresholds.")
        rows = []

    labeled = []
    for r in rows:
        sp = r.get('sector_path') or []
        txt = (r.get('text') or '').strip()
        if isinstance(sp, list) and sp and txt:
            labeled.append((txt[:400], sp[0]))

    out = {
        'centroid_l0_threshold': L0_THRESHOLD,
        'knn_distance_threshold': KNN_REJECT_DEFAULT,
        'calibration_date': time.strftime('%Y-%m-%d'),
        'validation_n': len(labeled),
        'selective_risk_at_threshold': None,
        'coverage_at_threshold': None,
    }

    if not labeled:
        print("[calibrate] no labeled facts (sector_path) found — keeping defaults.")
        _write_thresholds(output_dir, out)
        return

    print(f"[calibrate] embedding {len(labeled)} labeled facts...")
    mat = _embed_many([t for t, _ in labeled])
    if mat is None:
        print("[calibrate] embedding failed — keeping defaults.")
        _write_thresholds(output_dir, out)
        return

    preds = []  # (true_l0, pred_l0, l0_score, knn_dist)
    for i, (_txt, true_l0) in enumerate(labeled):
        pred_l0, score = _l0_predict(mat[i], centroids)
        knn = _knn_distance(mat[i], anchor_matrix)
        preds.append((true_l0, pred_l0, score, knn))

    cts = [round(0.20 + 0.05 * i, 2) for i in range(13)]   # 0.20 → 0.80
    kts = [round(0.30 + 0.05 * i, 2) for i in range(11)]   # 0.30 → 0.80
    total = len(preds)

    print("\n[calibrate] risk-coverage sweep:")
    print(f"  {'centroid':>9} {'knn':>6} {'coverage':>9} {'risk':>7} {'accepted':>9}")
    best = None
    for ct in cts:
        for kt in kts:
            accepted = [(t, p) for (t, p, s, k) in preds if s >= ct and k <= kt]
            if not accepted:
                continue
            wrong = sum(1 for t, p in accepted if t != p)
            risk = wrong / len(accepted)
            coverage = len(accepted) / total
            # Only print a representative slice to keep output readable.
            if kt in (0.50, 0.65, 0.80):
                print(f"  {ct:>9.2f} {kt:>6.2f} {coverage:>9.2f} {risk:>7.2f} {len(accepted):>9d}")
            if risk <= target_risk:
                # maximize coverage, tie-break on lower risk
                key = (coverage, -risk)
                if best is None or key > best[0]:
                    best = (key, ct, kt, risk, coverage)

    if best is not None:
        _, ct, kt, risk, coverage = best
        out['centroid_l0_threshold'] = ct
        out['knn_distance_threshold'] = kt
        out['selective_risk_at_threshold'] = round(risk, 4)
        out['coverage_at_threshold'] = round(coverage, 4)
        print(f"\n[calibrate] best: centroid>={ct} knn<={kt} "
              f"risk={risk:.3f} coverage={coverage:.3f}")
    else:
        print(f"\n[calibrate] no threshold reached risk<={target_risk}; keeping defaults.")

    _write_thresholds(output_dir, out)


def _write_thresholds(output_dir, out):
    path = os.path.join(output_dir, 'classification_threshold.json')
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)
    _reset_caches()
    print(f"[calibrate] wrote {path}")
