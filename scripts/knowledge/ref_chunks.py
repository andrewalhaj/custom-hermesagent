"""
Chunk-based semantic index for the Hermes references store.

Builds an incremental local index of ~/.hermes/references/*.md chunked by the
overlap chunker (A2 arm from the 2026-10-14 experiment: 800-char chunks with
synthetic path/heading prefix, embedded with Qwen3-Embedding-4B at 2560 dims).
Deep Success@5 jumps from 0.63 (A0 keyword) to 0.97 with this approach.

Search strategy: S6 — chunk-level RRF(k=20) of cosine + BM25, best-250-char
query-term-overlap window as snippet.  Winner of the 2026-10-14 chunk/snippet
selection experiment (deep AiT@250 0.567, deep doc_S@5 0.967).

Config (module-level constants, overridable via env at startup — a one-line change):
  REF_CHUNK_TARGET  env var, default 800
  REF_CHUNK_MAX     env var, default 1500

Index layout (~/.hermes/cache/ref_chunks/):
  chunks.jsonl   — chunk metadata (file, chunk_idx, heading_context, prefix, body)
  vectors.npy    — float32 array shape (N, 2560)
  manifest.json  — {params, embed_model, dim, files: {name: {sha256, mtime, n_chunks}}, built_at, n_chunks}

CLI:
  python3 -m knowledge.ref_chunks build
  python3 -m knowledge.ref_chunks search "query text"
"""

import os
import re
import sys
import json
import math
import time
import hashlib
import logging
import threading
from collections import defaultdict
from pathlib import Path

# ── Config constants (one-line change: edit the default literal below) ────────
REF_CHUNK_TARGET: int = int(os.environ.get("REF_CHUNK_TARGET", "800"))
REF_CHUNK_MAX: int = int(os.environ.get("REF_CHUNK_MAX", "1500"))

HERMES_HOME: Path = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
INDEX_DIR: Path = HERMES_HOME / "cache" / "ref_chunks"

# ── Reports corpus extension ──────────────────────────────────────────────────
# Second index root: ~/reports/*.md, gated by explicit allowlist.
# Files not in this tuple are never indexed, regardless of what exists on disk.
REPORTS_DIR: Path = Path(os.environ.get("REPORTS_DIR", Path.home() / "reports"))
REPORTS_ALLOWLIST: tuple = (
    "dtg-siemens-license-server-plan.md",
    "dtg-azure-landing-zone.md",
    "hermes-agent-repo-plan.md",
    "mira-build-plan.md",
    "dtg-ppkg-onboarding-runbook.md",
    "connected-machine-shop-project.md",
    "rea-profile-plan.md",
    "azure-ai-search-dtg-guide.md",
)
# Label prefix applied to chunk 'file' keys so the two roots stay distinguishable.
_REPORTS_PREFIX: str = "reports/"

EXPECTED_DIM: int = 2560
BATCH_SIZE: int = 16
BATCH_SLEEP: float = 0.5

# RRF constant (k=20 per S6 experiment winner)
_RRF_K: int = 20
# Max embed text length for sanitization retry
_MAX_EMBED_CHARS: int = 6000

logger = logging.getLogger("knowledge.ref_chunks")

# ── Module-level index cache (lazy-loaded, reloaded on manifest mtime change) ─
_index_cache = None          # dict: {manifest, chunks, vectors, bm25}
_manifest_mtime: float = 0.0
_cache_lock = threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _cosine_matrix(qvec, matrix):
    """Cosine similarity of qvec (D,) against matrix (N, D). Returns (N,) float32."""
    import numpy as np
    qvec = qvec.astype(np.float32)
    nq = float(np.linalg.norm(qvec))
    if nq == 0:
        return np.zeros(len(matrix), dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    norms = np.where(norms == 0, 1e-9, norms)
    return (matrix @ qvec) / (norms * nq)


def _sanitize_embed_text(text: str, max_chars: int = _MAX_EMBED_CHARS) -> str:
    """Strip ASCII control chars and cap length for embed retry.

    Strips C0 controls (except tab, LF, CR) and DEL, then truncates to max_chars.
    """
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    return sanitized[:max_chars]


def _has_secrets(text: str) -> bool:
    """Return True if *text* contains credential-looking strings.

    Uses secret_mask.mask_secrets — if the masked version differs from the
    original, at least one credential pattern was found.  Used as a chunk-level
    secret guard so report chunks with credentials are excluded from the index.
    """
    try:
        import sys as _sys
        if str(HERMES_HOME / "scripts") not in _sys.path:
            _sys.path.insert(0, str(HERMES_HOME / "scripts"))
        from secret_mask import mask_secrets
        return mask_secrets(text) != text
    except Exception:
        return False


def best_window(body: str, query_text: str, window_size: int = 250) -> str:
    """Return the window_size-char window of *body* that maximises query-term overlap.

    Sentence-aligned: prefers windows that start at a sentence boundary.
    Falls back to a dense slide every 25 chars if no sentence starts help.
    Ported exactly from selection_eval.py (S4/S6 snippet method).
    """
    if len(body) <= window_size:
        return body

    query_toks = set(re.findall(r"\w+", query_text.lower()))
    if not query_toks:
        return body[:window_size]

    # Sentence boundary starts: after '. ', '! ', '? ', or a newline
    sent_starts = [0]
    for m in re.finditer(r"(?<=[.!?\n])\s+", body):
        s = m.end()
        if 0 < s < len(body) - window_size:
            sent_starts.append(s)

    # Combine sentence starts and a coarse slide
    positions = sorted(set(
        sent_starts + list(range(0, max(1, len(body) - window_size + 1), 25))
    ))

    best_score = -1
    best_pos = 0
    for pos in positions:
        end_pos = min(pos + window_size, len(body))
        window_toks = re.findall(r"\w+", body[pos:end_pos].lower())
        score = sum(1 for t in window_toks if t in query_toks)
        if score > best_score:
            best_score = score
            best_pos = pos

    return body[best_pos: best_pos + window_size]


class _BM25Index:
    """Pure-Python BM25 index.

    Ported exactly from selection_eval.py with identical tokenisation
    (re.findall(r'\\w+', text.lower())), IDF formula, and default k1/b params.
    Documents are indexed over chunk *body* text (not prefix+body), matching
    the S6 experiment setup.
    """

    def __init__(self, docs):
        self.n = len(docs)
        self.tok_docs = [re.findall(r"\w+", d.lower()) for d in docs]
        df = defaultdict(int)
        for td in self.tok_docs:
            for t in set(td):
                df[t] += 1
        self.idf = {
            t: math.log((self.n - df[t] + 0.5) / (df[t] + 0.5) + 1)
            for t in df
        }
        lens = [len(td) for td in self.tok_docs]
        self.avgdl = sum(lens) / max(len(lens), 1)

    def score(self, query: str, k1: float = 1.5, b: float = 0.75):
        """Return BM25 score array (N,) float32 for *query*."""
        import numpy as np
        qtokens = re.findall(r"\w+", query.lower())
        scores = np.zeros(self.n, dtype=np.float64)
        for t in qtokens:
            idf_t = self.idf.get(t, 0.0)
            if idf_t == 0:
                continue
            for i, td in enumerate(self.tok_docs):
                tf = td.count(t)
                dl = len(td)
                num = tf * (k1 + 1)
                den = tf + k1 * (1 - b + b * dl / max(self.avgdl, 1))
                scores[i] += idf_t * (num / max(den, 1e-9))
        return scores.astype(np.float32)


# ── Build ─────────────────────────────────────────────────────────────────────

def build_index(incremental: bool = True):
    """Build (or incrementally update) the chunk index.

    - Corpus: top-level ~/.hermes/references/*.md
    - Embed text: synthetic_prefix(path, heading) + "\\n\\n" + body  (A2 format)
    - Asserts dim == 2560; aborts without overwriting existing index on mismatch.
    - Atomic write: all files written to temps, then os.replace()'d atomically.
    - Chunks whose embed fails after sanitization are excluded (not zero-vectored).

    Returns the manifest dict on success, raises on unrecoverable error.
    """
    # Ensure numpy and knowledge modules are importable
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("numpy is required; install it in the venv")

    if str(HERMES_HOME / "scripts") not in sys.path:
        sys.path.insert(0, str(HERMES_HOME / "scripts"))

    import knowledge.chunker as chunker_mod
    from knowledge.chunker import chunk_overlap, synthetic_prefix
    from knowledge.embed import embed

    # Temporarily patch chunker constants if env overrides differ from defaults
    orig_target = chunker_mod.CHUNK_TARGET_CHARS
    orig_max = chunker_mod.CHUNK_MAX_CHARS
    chunker_mod.CHUNK_TARGET_CHARS = REF_CHUNK_TARGET
    chunker_mod.CHUNK_MAX_CHARS = REF_CHUNK_MAX
    try:
        return _build_impl(embed, chunk_overlap, synthetic_prefix, np, incremental)
    finally:
        chunker_mod.CHUNK_TARGET_CHARS = orig_target
        chunker_mod.CHUNK_MAX_CHARS = orig_max


def _build_impl(embed_fn, chunk_overlap_fn, synthetic_prefix_fn, np, incremental: bool):
    """Core build logic — separated so tests can inject fake embed/chunker/prefix."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = INDEX_DIR / "manifest.json"
    chunks_path   = INDEX_DIR / "chunks.jsonl"
    vectors_path  = INDEX_DIR / "vectors.npy"

    ref_dir = HERMES_HOME / "references"
    if not ref_dir.exists():
        raise RuntimeError(f"References directory not found: {ref_dir}")

    ref_files = sorted(ref_dir.glob("*.md"))
    logger.info("Found %d reference files", len(ref_files))

    # ── Reports corpus (second root, allowlist-gated) ─────────────────────────
    reports_files: list = []
    skipped_reports: list = []
    if REPORTS_DIR.exists():
        for fname in REPORTS_ALLOWLIST:
            p = REPORTS_DIR / fname
            if p.exists():
                reports_files.append(p)
            else:
                skipped_reports.append(fname)
    else:
        skipped_reports = list(REPORTS_ALLOWLIST)
    if skipped_reports:
        logger.info("Reports allowlist: %d missing (skipped): %s", len(skipped_reports), skipped_reports)
    logger.info("Reports corpus: %d files from %s", len(reports_files), REPORTS_DIR)

    # ── Load existing index for incremental build ─────────────────────────────
    existing_manifest: dict = {}
    existing_chunks: list = []
    existing_vectors = None

    if (incremental
            and manifest_path.exists()
            and chunks_path.exists()
            and vectors_path.exists()):
        try:
            with open(manifest_path) as f:
                existing_manifest = json.load(f)
            with open(chunks_path) as f:
                existing_chunks = [json.loads(l) for l in f if l.strip()]
            existing_vectors = np.load(str(vectors_path))
            logger.info("Loaded existing index: %d chunks", len(existing_chunks))
        except Exception as e:
            logger.warning("Could not load existing index (%s); rebuilding from scratch", e)
            existing_manifest = {}
            existing_chunks = []
            existing_vectors = None

    existing_files_meta: dict = existing_manifest.get("files", {})

    # ── Determine which files changed or are new ──────────────────────────────
    current_shas: dict = {}
    for ref_file in ref_files:
        try:
            sha   = _sha256_file(ref_file)
            mtime = ref_file.stat().st_mtime
            current_shas[ref_file.name] = {"sha256": sha, "mtime": mtime}
        except Exception as e:
            logger.warning("Could not stat/hash %s: %s", ref_file, e)

    for rep_file in reports_files:
        try:
            sha   = _sha256_file(rep_file)
            mtime = rep_file.stat().st_mtime
            key   = _REPORTS_PREFIX + rep_file.name   # e.g. "reports/dtg-azure-landing-zone.md"
            current_shas[key] = {"sha256": sha, "mtime": mtime}
        except Exception as e:
            logger.warning("Could not stat/hash %s: %s", rep_file, e)

    changed_files = {
        fname for fname, info in current_shas.items()
        if existing_files_meta.get(fname, {}).get("sha256") != info["sha256"]
    }
    deleted_files = set(existing_files_meta.keys()) - set(current_shas.keys())

    logger.info(
        "Changed: %d, deleted: %d, unchanged: %d",
        len(changed_files), len(deleted_files),
        len(ref_files) - len(changed_files),
    )

    # ── Keep chunks/vectors for unchanged files ───────────────────────────────
    keep_chunks: list = []
    keep_vectors: list = []
    if existing_vectors is not None and existing_chunks:
        for i, chunk in enumerate(existing_chunks):
            fname = chunk.get("file", "")
            if fname not in changed_files and fname not in deleted_files:
                keep_chunks.append(chunk)
                keep_vectors.append(existing_vectors[i])

    # ── Chunk and embed changed/new files ────────────────────────────────────
    new_chunks: list  = []
    new_embed_texts: list = []

    for ref_file in ref_files:
        if ref_file.name not in changed_files:
            continue
        try:
            text = ref_file.read_text(errors="ignore")
            raw = chunk_overlap_fn(text, source_path=str(ref_file))
            for idx, (body, heading_ctx, _paras) in enumerate(raw):
                pfx = synthetic_prefix_fn(str(ref_file), heading_ctx)
                embed_text = pfx + "\n\n" + body
                new_chunks.append({
                    "file":            ref_file.name,
                    "source_path":     str(ref_file),
                    "chunk_idx":       idx,
                    "heading_context": heading_ctx,
                    "prefix":          pfx,
                    "body":            body,
                })
                new_embed_texts.append(embed_text)
        except Exception as e:
            logger.warning("Could not chunk %s: %s", ref_file, e)

    # ── Chunk reports files (allowlisted, with secret guard) ──────────────────
    excluded_secret_chunks: list = []   # [(file_key, chunk_idx, heading)] — for reporting
    for rep_file in reports_files:
        key = _REPORTS_PREFIX + rep_file.name
        if key not in changed_files:
            continue
        try:
            text = rep_file.read_text(errors="ignore")
            raw = chunk_overlap_fn(text, source_path=str(rep_file))
            for idx, (body, heading_ctx, _paras) in enumerate(raw):
                # ── Secret guard: exclude chunks containing credential patterns ──
                if _has_secrets(body):
                    logger.warning(
                        "Excluding reports chunk %s[%d] heading=%r: "
                        "credential-looking string detected in body",
                        key, idx, heading_ctx,
                    )
                    excluded_secret_chunks.append((key, idx, heading_ctx))
                    continue
                pfx = synthetic_prefix_fn(str(rep_file), heading_ctx)
                embed_text = pfx + "\n\n" + body
                new_chunks.append({
                    "file":            key,          # "reports/dtg-azure-landing-zone.md"
                    "source_path":     str(rep_file),
                    "chunk_idx":       idx,
                    "heading_context": heading_ctx,
                    "prefix":          pfx,
                    "body":            body,
                })
                new_embed_texts.append(embed_text)
        except Exception as e:
            logger.warning("Could not chunk reports file %s: %s", rep_file, e)
    if excluded_secret_chunks:
        logger.warning(
            "Secret guard excluded %d report chunk(s): %s",
            len(excluded_secret_chunks),
            excluded_secret_chunks,
        )

    # ── Batch embed with throttle, retry, and dim assertion ──────────────────
    # new_vectors[i] is either a valid vector or None (excluded chunk).
    new_vectors: list = []
    if new_embed_texts:
        n = len(new_embed_texts)
        logger.info("Embedding %d new chunks ...", n)
        for i in range(0, n, BATCH_SIZE):
            batch = new_embed_texts[i : i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE
            vecs = None
            MAX_RETRIES = 3
            for attempt in range(MAX_RETRIES):
                vecs = embed_fn(batch)
                wrong = [j for j, v in enumerate(vecs) if len(v) != EXPECTED_DIM]
                if not wrong:
                    break
                # Purge LRU cache entries for wrong-dim texts so next call re-fetches
                logger.warning(
                    "Batch %d attempt %d: %d wrong-dim vectors %s, purging cache & retrying in 2s",
                    batch_num, attempt + 1, len(wrong),
                    {len(vecs[j]) for j in wrong},
                )
                try:
                    from knowledge.embed import _EMBED_CACHE
                    for j in wrong:
                        k = hashlib.sha256(batch[j].encode("utf-8")).hexdigest()
                        _EMBED_CACHE.pop(k, None)
                except Exception:
                    pass
                time.sleep(2)
            else:
                # All batch-level retries exhausted — fall back to per-text embedding.
                # Per-text: try original text, then sanitized/truncated text.
                # If both fail, exclude the chunk (None sentinel) rather than zero-vector.
                logger.warning(
                    "Batch %d: all %d retries failed; falling back to per-text embedding",
                    batch_num, MAX_RETRIES,
                )
                vecs = []
                for j, t in enumerate(batch):
                    # Purge cache so we get a fresh fetch
                    try:
                        from knowledge.embed import _EMBED_CACHE
                        k = hashlib.sha256(t.encode("utf-8")).hexdigest()
                        _EMBED_CACHE.pop(k, None)
                    except Exception:
                        pass

                    v = None

                    # Attempt 1: original text
                    try:
                        tv = embed_fn([t])
                        candidate = tv[0]
                        if len(candidate) == EXPECTED_DIM:
                            v = candidate
                    except Exception as e:
                        logger.warning(
                            "Per-text embed failed for original text (chunk %d in batch %d): %s",
                            j, batch_num, e,
                        )

                    # Attempt 2: sanitized / truncated text
                    if v is None:
                        sanitized = _sanitize_embed_text(t)
                        if sanitized != t:
                            try:
                                from knowledge.embed import _EMBED_CACHE
                                ks = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
                                _EMBED_CACHE.pop(ks, None)
                            except Exception:
                                pass
                            try:
                                tv2 = embed_fn([sanitized])
                                candidate2 = tv2[0]
                                if len(candidate2) == EXPECTED_DIM:
                                    v = candidate2
                                    logger.info(
                                        "Sanitized embed succeeded for chunk %d in batch %d "
                                        "(original len=%d, sanitized len=%d)",
                                        j, batch_num, len(t), len(sanitized),
                                    )
                            except Exception as e2:
                                logger.warning(
                                    "Sanitized embed also failed for chunk %d in batch %d: %s",
                                    j, batch_num, e2,
                                )

                    if v is None:
                        logger.warning(
                            "Excluding chunk (global idx %d, batch %d item %d) from index: "
                            "embed failed after sanitization (embed_text len=%d)",
                            i + j, batch_num, j, len(t),
                        )

                    vecs.append(v)  # None for excluded chunks

            new_vectors.extend(vecs)
            if i + BATCH_SIZE < n:
                time.sleep(BATCH_SLEEP)
            logger.info("  embedded %d/%d", min(i + BATCH_SIZE, n), n)

    # ── Filter out excluded chunks (None vectors from sanitization failures) ──
    if any(v is None for v in new_vectors):
        pairs = [(c, v) for c, v in zip(new_chunks, new_vectors) if v is not None]
        n_excluded = len(new_chunks) - len(pairs)
        logger.warning(
            "Excluded %d chunk(s) from index due to persistent embed failures", n_excluded
        )
        new_chunks  = [p[0] for p in pairs]
        new_vectors = [p[1] for p in pairs]

    # ── Merge kept + new ──────────────────────────────────────────────────────
    all_chunks  = keep_chunks + new_chunks
    all_vectors = keep_vectors + new_vectors

    if not all_chunks:
        logger.warning("No chunks to index — nothing written")
        return None

    # Final dim assertion on merged set (catches dim drift in kept vectors)
    for i, v in enumerate(all_vectors):
        dim = len(v) if hasattr(v, "__len__") else int(np.asarray(v).shape[0])
        if dim != EXPECTED_DIM:
            raise ValueError(
                f"Dim mismatch in merged vector {i}: expected {EXPECTED_DIM}, got {dim}. "
                f"Aborting without overwriting existing index."
            )

    arr = np.array(all_vectors, dtype=np.float32)

    # Embed model name for manifest (informational)
    try:
        from knowledge.embed import EMBEDDING_MODEL
        embed_model = EMBEDDING_MODEL or "unknown"
    except Exception:
        embed_model = "unknown"

    # Per-file chunk counts for manifest
    files_meta: dict = {}
    for chunk in all_chunks:
        fname = chunk["file"]
        if fname not in files_meta and fname in current_shas:
            files_meta[fname] = {
                "sha256":   current_shas[fname]["sha256"],
                "mtime":    current_shas[fname]["mtime"],
                "n_chunks": 0,
            }
        if fname in files_meta:
            files_meta[fname]["n_chunks"] += 1

    manifest = {
        "params": {"chunk_target": REF_CHUNK_TARGET, "chunk_max": REF_CHUNK_MAX},
        "embed_model": embed_model,
        "dim":         EXPECTED_DIM,
        "files":       files_meta,
        "built_at":    time.time(),
        "n_chunks":    len(all_chunks),
    }

    # ── Atomic write (tmp → os.replace) ──────────────────────────────────────
    tmp_chunks   = INDEX_DIR / "._chunks.jsonl.tmp"
    tmp_vectors  = INDEX_DIR / "._vectors_build.npy"   # must end .npy so np.save won't append it
    tmp_manifest = INDEX_DIR / "._manifest.json.tmp"

    try:
        with open(tmp_chunks, "w") as f:
            for chunk in all_chunks:
                f.write(json.dumps(chunk) + "\n")

        np.save(str(tmp_vectors), arr)

        with open(tmp_manifest, "w") as f:
            json.dump(manifest, f, indent=2)

        os.replace(str(tmp_chunks),   str(INDEX_DIR / "chunks.jsonl"))
        os.replace(str(tmp_vectors),  str(INDEX_DIR / "vectors.npy"))
        os.replace(str(tmp_manifest), str(INDEX_DIR / "manifest.json"))

    except Exception:
        for p in (tmp_chunks, tmp_vectors, tmp_manifest):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        raise

    logger.info(
        "Index built: %d chunks, dim=%d, files=%d",
        len(all_chunks), arr.shape[1], len(files_meta),
    )
    return manifest


# ── Search ────────────────────────────────────────────────────────────────────

def _load_index():
    """Lazy-load index into module cache; reload when manifest.json mtime changes.

    Also builds and caches the BM25 index (over chunk bodies) on each (re)load.
    Returns cache dict or None if index is absent/unreadable.
    Thread-safe: uses _cache_lock.
    """
    global _index_cache, _manifest_mtime
    import numpy as np

    manifest_path = INDEX_DIR / "manifest.json"
    chunks_path   = INDEX_DIR / "chunks.jsonl"
    vectors_path  = INDEX_DIR / "vectors.npy"

    if not (manifest_path.exists() and chunks_path.exists() and vectors_path.exists()):
        return None

    try:
        current_mtime = manifest_path.stat().st_mtime
    except Exception:
        return None

    with _cache_lock:
        if _index_cache is not None and _manifest_mtime == current_mtime:
            return _index_cache
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            with open(chunks_path) as f:
                chunks = [json.loads(l) for l in f if l.strip()]
            vectors = np.load(str(vectors_path))

            # Build BM25 index over chunk bodies (same as S6 experiment setup)
            logger.info("Building BM25 index over %d chunk bodies ...", len(chunks))
            bm25 = _BM25Index([c.get("body", "") for c in chunks])

            _index_cache  = {
                "manifest": manifest,
                "chunks":   chunks,
                "vectors":  vectors,
                "bm25":     bm25,
            }
            _manifest_mtime = current_mtime
            return _index_cache
        except Exception as e:
            logger.error("Failed to load index: %s", e)
            return None


def search(query: str, top_k: int = 5, per_file_cap=None):
    """Search the chunk index using S6: chunk-level RRF(k=20) of cosine + BM25.

    Ranking: RRF(k=20) merge of cosine ranking and BM25 ranking over chunk bodies.
    Snippet (content): best 250-char query-term-overlap window of the chunk body.

    Returns a list of dicts:
      store, id, title, content, score, sector_path, url, chunk_idx, heading
      + body  (full chunk body, for memory_search 1000-char injection slot)
      + rrf   (RRF score used for ranking)

    per_file_cap: if set to an int, at most that many chunks per source file.
    Default is None (no cap), matching the S6 experiment which uses raw top-K.

    Never raises; returns [] and logs on any error.
    """
    try:
        return _search_impl(query, top_k=top_k, per_file_cap=per_file_cap)
    except Exception as e:
        logger.error("ref_chunks.search failed: %s", e)
        return []


def _search_impl(query: str, top_k: int, per_file_cap):
    import numpy as np

    if str(HERMES_HOME / "scripts") not in sys.path:
        sys.path.insert(0, str(HERMES_HOME / "scripts"))

    cache = _load_index()
    if cache is None:
        logger.warning("No index available; returning []")
        return []

    from knowledge.embed import embed

    chunks  = cache["chunks"]
    vectors = cache["vectors"]
    bm25    = cache["bm25"]

    # Embed query raw — no prefix, mirrors eval.py embed_query / search.py _single_search
    vecs = embed([query])
    qvec = np.array(vecs[0], dtype=np.float32)

    # ── S6: RRF(k=20) of dense cosine + BM25 ─────────────────────────────────
    cosine_scores = _cosine_matrix(qvec, vectors)
    bm25_scores   = bm25.score(query)

    # Candidate pool: top-max(20, top_k*4) from each ranker
    candidate_size = max(20, top_k * 4)
    dense_top = list(map(int, np.argsort(-cosine_scores)[:candidate_size]))
    bm25_top  = list(map(int, np.argsort(-bm25_scores)[:candidate_size]))

    k = _RRF_K
    rrf_scores: dict = defaultdict(float)
    for rank, idx in enumerate(dense_top, 1):
        rrf_scores[idx] += 1.0 / (k + rank)
    for rank, idx in enumerate(bm25_top, 1):
        rrf_scores[idx] += 1.0 / (k + rank)

    # Sort by RRF score descending
    rrf_order = sorted(rrf_scores, key=rrf_scores.__getitem__, reverse=True)

    # ── Collect top_k results (apply per_file_cap if set) ────────────────────
    results = []
    file_hits: dict = {}

    for idx_i in rrf_order:
        if len(results) >= top_k:
            break
        chunk = chunks[int(idx_i)]
        fname = chunk.get("file", "")

        if per_file_cap is not None and file_hits.get(fname, 0) >= per_file_cap:
            continue
        file_hits[fname] = file_hits.get(fname, 0) + 1

        body        = chunk.get("body", "")
        heading_ctx = chunk.get("heading_context", "")
        src_path    = chunk.get("source_path", "")
        fname_key   = chunk.get("file", "")
        # Keep roots distinguishable: reports/ prefix in file key → prefix the id too
        if fname_key.startswith(_REPORTS_PREFIX):
            stem = _REPORTS_PREFIX + Path(src_path).stem if src_path else fname_key.replace(".md", "")
        else:
            stem = Path(src_path).stem if src_path else fname_key.replace(".md", "")

        # content = best 250-char query-term-overlap window of the chunk body
        content = best_window(body, query, window_size=250)

        results.append({
            "store":       "reference",
            "id":          stem,
            "title":       stem,
            "content":     content,
            "body":        body,
            "score":       float(cosine_scores[int(idx_i)]),
            "rrf":         float(rrf_scores[idx_i]),
            "sector_path": [],
            "url":         src_path,
            "chunk_idx":   chunk.get("chunk_idx", 0),
            "heading":     heading_ctx,
        })

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_build():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print("[ref_chunks] Starting incremental index build ...")
    t0 = time.time()
    m = build_index(incremental=True)
    elapsed = time.time() - t0
    if m:
        print(
            f"[ref_chunks] Done: {m['n_chunks']} chunks, dim={m['dim']}, "
            f"files={len(m['files'])}, elapsed={elapsed:.1f}s"
        )
    else:
        print("[ref_chunks] Build completed (nothing to index or no chunks produced)")


def _cli_search(query: str):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    t0 = time.time()
    results = search(query, top_k=5)
    elapsed_ms = (time.time() - t0) * 1000
    print(f"[ref_chunks] {len(results)} results in {elapsed_ms:.0f}ms")
    for i, r in enumerate(results, 1):
        snippet = r["content"][:120].replace("\n", " ")
        print(
            f"  {i}. [cos={r['score']:.4f} rrf={r['rrf']:.4f}] {r['id']} "
            f"chunk={r['chunk_idx']} heading={r['heading']!r}"
        )
        print(f"     {snippet!r}")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="ref_chunks: chunk-based reference search index")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="Build/update the index")
    sp = sub.add_parser("search", help="Search the index")
    sp.add_argument("query", help="Query string")
    sp.add_argument("--top-k", type=int, default=5)

    args = p.parse_args()
    if args.cmd == "build":
        _cli_build()
    elif args.cmd == "search":
        _cli_search(args.query)
