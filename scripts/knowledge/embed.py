"""In-process embedding: remote Ollama (Mac Studio), or local mpnet only when no
remote is configured, backed by an LRU cache.

sentence_transformers is imported LAZILY inside get_model() — its top-level
import costs ~4.8s (torch), so lightweight callers never pay that cost.
"""
import sys
import time
import json
import hashlib
import urllib.request
from collections import OrderedDict

from knowledge.config import env

MODEL_NAME = 'all-mpnet-base-v2'  # 768-dim, best quality local model

# Lazy init
_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model

# ── A1: LRU embedding cache ──────────────────────────────────────────────
_EMBED_CACHE = OrderedDict()   # sha256(text) -> (vector, stored_at)
_EMBED_CACHE_CAP = 256
_EMBED_CACHE_TTL = 1800        # 30 min

def _cache_key(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

# -- Remote Ollama embedding (Mac Studio) ----------------------------------
EMBEDDING_API_URL = env("EMBEDDING_API_URL", "")
EMBEDDING_MODEL = env("EMBEDDING_MODEL", "nomic-embed-text")
# Native full-quality embeddings: Qwen3-Embedding-4B emits 2560-dim vectors.
# The store column is halfvec(2560) (float16) + hnsw-indexed with
# halfvec_cosine_ops. We send NO `dimensions` param so the model returns its
# native 2560-dim output (no MRL truncation). Setting EMBEDDING_DIMENSIONS to a
# non-zero value would re-enable top-level MRL truncation and break the <=>
# search against the halfvec(2560) column. 0 / unset → omit the param (native dim).
EMBEDDING_DIMENSIONS = int(env("EMBEDDING_DIMENSIONS", "0") or "0")

def _embed_remote(texts):
    """Call remote Ollama on Mac Studio for embeddings, chunking large batches."""
    BATCH_SIZE = 32
    all_embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        chunk = texts[i:i+BATCH_SIZE]
        body = {"model": EMBEDDING_MODEL, "input": chunk}
        if EMBEDDING_DIMENSIONS:
            body["dimensions"] = EMBEDDING_DIMENSIONS  # top-level MRL truncation
        payload = json.dumps(body)
        req = urllib.request.Request(
            f"{EMBEDDING_API_URL}/api/embed",
            data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        all_embeddings.extend(e for e in data["embeddings"])
    return all_embeddings

_remote_fail_logged = False

def _log_remote_failure_once(e):
    global _remote_fail_logged
    if not _remote_fail_logged:
        _remote_fail_logged = True
        print(f"[knowledge] remote embed failed (no local fallback; further failures not logged): {e}", file=sys.stderr)

def embed(texts):
    """Embed a list of texts -> list of vectors. Remote Ollama when EMBEDDING_API_URL is set
    (raises RuntimeError on failure, never falls back to a wrong-dim local model);
    local mpnet only when no remote is configured."""
    now = time.time()
    results = [None] * len(texts)
    misses, miss_idx = [], []
    for i, t in enumerate(texts):
        k = _cache_key(t)
        entry = _EMBED_CACHE.get(k)
        if entry is not None and (now - entry[1]) < _EMBED_CACHE_TTL:
            results[i] = entry[0]
            _EMBED_CACHE.move_to_end(k)
        else:
            if entry is not None:
                _EMBED_CACHE.pop(k, None)   # expired
            misses.append(t)
            miss_idx.append(i)
    if misses:
        _remote_err = None
        if EMBEDDING_API_URL:
            try:
                embeddings = _embed_remote(misses)
                for j, vec in enumerate(embeddings):
                    results[miss_idx[j]] = vec
                    k = _cache_key(misses[j])
                    _EMBED_CACHE[k] = (vec, now)
                    _EMBED_CACHE.move_to_end(k)
                    while len(_EMBED_CACHE) > _EMBED_CACHE_CAP:
                        _EMBED_CACHE.popitem(last=False)
                return results
            except Exception as e:
                # No local fallback: mpnet is 768-dim, the store is halfvec(2560).
                # Callers (search, classifier) catch; storage refuses the insert.
                _log_remote_failure_once(e)
                raise RuntimeError(f"remote embed failed ({EMBEDDING_API_URL}): {e}") from e
        model = get_model()
        embeddings = model.encode(misses, normalize_embeddings=True)
        for j, vec in enumerate(embeddings.tolist()):
            results[miss_idx[j]] = vec
            k = _cache_key(misses[j])
            _EMBED_CACHE[k] = (vec, now)
            _EMBED_CACHE.move_to_end(k)
            while len(_EMBED_CACHE) > _EMBED_CACHE_CAP:
                _EMBED_CACHE.popitem(last=False)   # evict oldest
    return results
