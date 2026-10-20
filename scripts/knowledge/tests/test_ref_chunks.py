"""
Tests for knowledge.ref_chunks — chunk-based reference search index.

Run:
  cd ~/.hermes/scripts
  /usr/local/lib/hermes-agent/venv/bin/python3 -m pytest knowledge/tests/test_ref_chunks.py -v
"""
import os
import sys
import json
import time
import hashlib
import threading
import importlib

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import numpy as np
# Use importlib to get the actual MODULE objects, not the shadowed names that
# knowledge/__init__.py exposes (it re-exports functions under the same names).
import importlib
rc_mod    = importlib.import_module("knowledge.ref_chunks")
us_mod    = importlib.import_module("knowledge.unified_search")
embed_mod = importlib.import_module("knowledge.embed")   # module, not the function shadow


# ── Fake embed factory ────────────────────────────────────────────────────────

def make_fake_embed(dim: int = 2560):
    """Return a deterministic fake embed function + call log.

    The fake produces a unit-normalised vector whose direction is derived from
    the SHA-256 of the input text, so equal texts always produce equal vectors.
    This makes cosine similarity tests deterministic.
    """
    calls = []

    def fake_embed(texts):
        vecs = []
        for t in texts:
            calls.append(t)
            seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:4], "big")
            rng  = np.random.RandomState(seed)
            v    = rng.randn(dim).astype(np.float32)
            norm = np.linalg.norm(v)
            if norm > 0:
                v /= norm
            vecs.append(v.tolist())
        return vecs

    fake_embed.calls = calls
    return fake_embed


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _write_refs(ref_dir, files: dict):
    """Write {filename: content} to ref_dir (creates dir)."""
    ref_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (ref_dir / fname).write_text(content)


# ── Build tests ───────────────────────────────────────────────────────────────

def test_build_creates_index_files(tmp_path, monkeypatch):
    """build_index() creates chunks.jsonl, vectors.npy, manifest.json."""
    ref_dir = tmp_path / "references"
    idx_dir = tmp_path / "cache" / "ref_chunks"
    _write_refs(ref_dir, {
        "alpha.md": "# Alpha\n\nThis is a test document about alpha.\n\n" + "a " * 200,
        "beta.md":  "# Beta\n\nThis is a test document about beta.\n\n"  + "b " * 200,
    })

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME",       tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",         idx_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_DIR",       tmp_path / "no_reports")  # isolated
    monkeypatch.setattr(rc_mod, "REPORTS_ALLOWLIST", ())
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    m = rc_mod.build_index(incremental=True)

    assert m is not None, "build_index returned None"
    assert m["dim"] == 2560
    assert m["n_chunks"] > 0
    assert set(m["files"].keys()) == {"alpha.md", "beta.md"}

    assert (idx_dir / "chunks.jsonl").exists()
    assert (idx_dir / "vectors.npy").exists()
    assert (idx_dir / "manifest.json").exists()

    vecs = np.load(str(idx_dir / "vectors.npy"))
    assert vecs.dtype == np.float32
    assert vecs.shape[1] == 2560
    assert vecs.shape[0] == m["n_chunks"]


def test_incremental_rebuild_skips_unchanged(tmp_path, monkeypatch):
    """Second build with an unchanged file must not re-embed that file's chunks."""
    ref_dir = tmp_path / "references"
    idx_dir = tmp_path / "cache" / "ref_chunks"
    _write_refs(ref_dir, {
        "stable.md":  "# Stable\n\nContent that does not change.\n\n" + "x " * 300,
        "mutable.md": "# Mutable\n\nVersion 1.\n\n"                   + "v1 " * 300,
    })

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",   idx_dir)
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    m1 = rc_mod.build_index(incremental=True)
    calls_after_build1 = len(fake.calls)
    assert calls_after_build1 > 0

    # Overwrite mutable.md (different sha256)
    (ref_dir / "mutable.md").write_text("# Mutable\n\nVersion 2.\n\n" + "v2 " * 300)

    # Reset module cache so the reload happens
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)
    fake.calls.clear()

    m2 = rc_mod.build_index(incremental=True)
    calls_after_build2 = len(fake.calls)

    # stable.md chunks must NOT have been re-embedded
    stable_chunks = m1["files"]["stable.md"]["n_chunks"]
    mutable_chunks_v2 = m2["files"]["mutable.md"]["n_chunks"]
    # Only mutable.md's new chunks should appear in the second embed call batch
    assert calls_after_build2 <= mutable_chunks_v2, (
        f"Expected <= {mutable_chunks_v2} embed calls (only mutable.md), "
        f"got {calls_after_build2}"
    )
    assert m2["n_chunks"] > 0
    assert "stable.md" in m2["files"]
    assert "mutable.md" in m2["files"]


def test_dim_assert_aborts_without_overwriting(tmp_path, monkeypatch):
    """When embed persistently returns wrong dim (768), failing chunks are EXCLUDED
    (not stored as zero vectors). Key invariants:
      - Index never written with 768-dim vectors.
      - Chunks with failing embeds are excluded, not zero-vectored.
      - Kept chunks from unchanged files survive so the build still completes.
    """
    ref_dir = tmp_path / "references"
    idx_dir = tmp_path / "cache" / "ref_chunks"
    # Two files: stable (unchanged across builds) + mutable (re-embedded with bad dim)
    _write_refs(ref_dir, {
        "stable.md":  "# Stable\n\nContent that does not change.\n\n" + "s " * 300,
        "mutable.md": "# Mutable\n\nVersion 1.\n\n"                   + "v1 " * 300,
    })

    # First build with correct dim
    fake_ok = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake_ok)
    monkeypatch.setattr(rc_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",   idx_dir)
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    m1 = rc_mod.build_index(incremental=True)
    assert m1 is not None and m1["n_chunks"] > 0

    # Modify mutable.md so incremental build re-embeds it
    (ref_dir / "mutable.md").write_text("# Mutable\n\nModified.\n\n" + "q " * 300)
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    # Second build with wrong dim (768) — simulates local 768-dim fallback.
    # Per-text fallback now EXCLUDES chunks rather than zero-vectoring them.
    fake_bad = make_fake_embed(768)
    monkeypatch.setattr(embed_mod, "embed", fake_bad)

    m2 = rc_mod.build_index(incremental=True)

    # Build completes: stable.md's chunks are kept from previous build
    assert m2 is not None, "Build must complete: stable.md chunks kept from prior index"
    assert m2["n_chunks"] > 0

    # The resulting vectors file must have dim 2560, never 768
    surviving_vectors = np.load(str(idx_dir / "vectors.npy"))
    assert surviving_vectors.shape[1] == 2560, (
        f"Index must not contain 768-dim vectors, got shape {surviving_vectors.shape}"
    )

    # No zero vectors — failed chunks are excluded, not stored as zeros
    norms = np.linalg.norm(surviving_vectors, axis=1)
    assert not np.any(norms == 0), (
        f"Zero vectors found at indices {np.where(norms == 0)[0]}; "
        "failing embeds must be excluded, not zero-vectored"
    )

    # mutable.md chunks excluded; stable.md chunks preserved
    surviving_manifest = json.loads((idx_dir / "manifest.json").read_text())
    assert surviving_manifest["n_chunks"] > 0
    with open(idx_dir / "chunks.jsonl") as f:
        stored_chunks = [json.loads(l) for l in f if l.strip()]
    stored_files = {c["file"] for c in stored_chunks}
    assert "stable.md" in stored_files, "stable.md chunks must be preserved"
    assert "mutable.md" not in stored_files, "mutable.md chunks must be excluded (embed failed)"


def test_search_schema(tmp_path, monkeypatch):
    """search() returns dicts with all required schema keys."""
    required_keys = {"store", "id", "title", "content", "score",
                     "sector_path", "url", "chunk_idx", "heading"}
    ref_dir = tmp_path / "references"
    idx_dir = tmp_path / "cache" / "ref_chunks"
    _write_refs(ref_dir, {
        "schema-test.md": "# Schema Test\n\nFoo bar baz.\n\n" + "detail " * 200,
    })

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",   idx_dir)
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    rc_mod.build_index(incremental=True)

    monkeypatch.setattr(rc_mod, "_index_cache", None)  # force reload
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    results = rc_mod.search("foo bar baz details", top_k=5)

    assert isinstance(results, list)
    assert len(results) > 0

    for r in results:
        missing = required_keys - set(r.keys())
        assert not missing, f"Result missing keys: {missing}"
        assert r["store"] == "reference"
        assert isinstance(r["score"], float)
        assert isinstance(r["sector_path"], list)
        assert isinstance(r["chunk_idx"], int)


def test_per_file_cap(tmp_path, monkeypatch):
    """per_file_cap=1 must not return two chunks from the same file."""
    ref_dir = tmp_path / "references"
    idx_dir = tmp_path / "cache" / "ref_chunks"
    # Three files, each with enough content for multiple chunks
    content = ("# Section\n\nParagraph of text " + "content " * 120 + "\n\n") * 3
    _write_refs(ref_dir, {
        "file-a.md": content,
        "file-b.md": content.replace("Section", "Other"),
        "file-c.md": content.replace("Section", "Third"),
    })

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME",       tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",         idx_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_DIR",       tmp_path / "no_reports")  # isolated
    monkeypatch.setattr(rc_mod, "REPORTS_ALLOWLIST", ())
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    rc_mod.build_index(incremental=True)

    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    results = rc_mod.search("paragraph text content", top_k=6, per_file_cap=1)

    file_ids = [r["id"] for r in results]
    # No duplicate file stems
    assert len(file_ids) == len(set(file_ids)), f"Duplicate files in results: {file_ids}"
    # With 3 files and per_file_cap=1, at most 3 results
    assert len(results) <= 3


def test_search_never_raises_on_missing_index(tmp_path, monkeypatch):
    """search() returns [] (no raise) when no index has been built."""
    idx_dir = tmp_path / "no_index_here"
    monkeypatch.setattr(rc_mod, "INDEX_DIR",   idx_dir)
    monkeypatch.setattr(rc_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)
    monkeypatch.setattr(embed_mod, "embed", make_fake_embed(2560))

    results = rc_mod.search("anything")
    assert results == []


# ── unified_search flag tests ─────────────────────────────────────────────────

def test_flag_off_returns_a0_unchanged(monkeypatch):
    """REF_CHUNKS unset/off must give identical output to the A0 keyword path."""
    sentinel = [{"store": "reference", "id": "x", "title": "x",
                 "content": "c", "score": 0.5, "sector_path": [], "url": "/x.md"}]
    monkeypatch.setattr(us_mod, "_search_references_a0",
                        lambda q, qs, tk: list(sentinel))
    monkeypatch.delenv("REF_CHUNKS", raising=False)

    result = us_mod._search_references("hello", [], top_k=5)
    assert result == sentinel, "flag=off must return A0 results unchanged"


def test_flag_log_returns_a0_immediately(tmp_path, monkeypatch):
    """REF_CHUNKS=log must return A0 results synchronously."""
    sentinel = [{"store": "reference", "id": "y", "title": "y",
                 "content": "c", "score": 0.7, "sector_path": [], "url": "/y.md"}]

    monkeypatch.setattr(us_mod, "_search_references_a0",
                        lambda q, qs, tk: list(sentinel))

    # Stub out ref_chunks.search so the daemon thread doesn't hit the real index
    import knowledge.ref_chunks as _rc
    monkeypatch.setattr(_rc, "search", lambda *a, **k: [])
    # Redirect shadow log to tmp dir
    shadow_path = tmp_path / "ref_chunks_shadow.jsonl"
    monkeypatch.setattr(us_mod, "_REF_CHUNKS_SHADOW_PATH", shadow_path)

    monkeypatch.setenv("REF_CHUNKS", "log")

    result = us_mod._search_references("a query", [], top_k=5)
    assert result == sentinel, "log mode must return A0 results synchronously"

    # Give daemon thread time to write — wait for non-empty file, not just creation
    for _ in range(40):
        try:
            if shadow_path.exists() and shadow_path.stat().st_size > 10:
                break
        except Exception:
            pass
        time.sleep(0.1)

    assert shadow_path.exists(), "shadow log file was not created"
    lines = [l for l in shadow_path.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1, "Expected at least one shadow log line"
    row = json.loads(lines[0])
    assert "ts" in row
    assert "query" in row
    assert "a0" in row
    assert "chunks" in row
    assert "latency_ms" in row


def test_flag_on_uses_chunk_results(monkeypatch):
    """REF_CHUNKS=on must return chunk results (capped) not A0."""
    a0_sentinel = [{"store": "reference", "id": "a0", "title": "a0",
                    "content": "a0 content", "score": 0.5,
                    "sector_path": [], "url": "/a0.md"}]
    chunk_sentinel = [
        {"store": "reference", "id": "c1", "title": "c1", "content": "chunk1",
         "score": 0.95, "sector_path": [], "url": "/c1.md",
         "chunk_idx": 0, "heading": ""},
        {"store": "reference", "id": "c2", "title": "c2", "content": "chunk2",
         "score": 0.85, "sector_path": [], "url": "/c2.md",
         "chunk_idx": 0, "heading": ""},
        {"store": "reference", "id": "c3", "title": "c3", "content": "chunk3",
         "score": 0.75, "sector_path": [], "url": "/c3.md",
         "chunk_idx": 0, "heading": ""},
    ]

    monkeypatch.setattr(us_mod, "_search_references_a0", lambda q, qs, tk: a0_sentinel)
    import knowledge.ref_chunks as _rc
    monkeypatch.setattr(_rc, "search", lambda *a, **k: chunk_sentinel)

    monkeypatch.setenv("REF_CHUNKS", "on")
    monkeypatch.setenv("REF_CHUNKS_MAX_SLOTS", "2")

    result = us_mod._search_references("query", [], top_k=5)
    assert len(result) == 2, f"Expected 2 chunk results (cap=2), got {len(result)}"
    assert result[0]["id"] == "c1"
    assert result[1]["id"] == "c2"


def test_flag_on_falls_back_to_a0_when_chunks_empty(monkeypatch):
    """REF_CHUNKS=on falls back to A0 when chunk search returns []."""
    a0_sentinel = [{"store": "reference", "id": "a0", "title": "a0",
                    "content": "a0 content", "score": 0.5,
                    "sector_path": [], "url": "/a0.md"}]

    monkeypatch.setattr(us_mod, "_search_references_a0", lambda q, qs, tk: a0_sentinel)
    import knowledge.ref_chunks as _rc
    monkeypatch.setattr(_rc, "search", lambda *a, **k: [])

    monkeypatch.setenv("REF_CHUNKS", "on")

    result = us_mod._search_references("query", [], top_k=5)
    assert result == a0_sentinel, "REF_CHUNKS=on with empty chunks must fall back to A0"


# ── S6 strategy tests ─────────────────────────────────────────────────────────

def test_s6_bm25_only_match_in_top5(monkeypatch):
    """S6 RRF: a chunk with exact BM25 match but low cosine must appear in top-5.

    Setup: 6 'regular' chunks collinear with the query (cosine=1, no BM25 match)
    and 1 'bm25-only' chunk anti-collinear with the query (cosine=-1, exact BM25 match).
    Pure cosine top-5 excludes the bm25-only chunk; S6 RRF includes it.
    """
    dim = 2560
    rng = np.random.RandomState(42)
    u = rng.randn(dim).astype(np.float32)
    u /= np.linalg.norm(u)
    anti_u = -u  # cosine = -1 with query vector u

    # 6 regular chunks: high cosine, no BM25 match for "FINDME_UNIQUE"
    chunks = [
        {
            "file": f"regular-{i}.md",
            "chunk_idx": 0,
            "heading_context": "",
            "prefix": f"From regular-{i}",
            "body": f"This is regular text about foo bar baz qux {i}.",
            "source_path": f"/fake/regular-{i}.md",
        }
        for i in range(6)
    ]
    # 1 BM25-only chunk: low cosine (-1), but exact match for "FINDME_UNIQUE"
    chunks.append({
        "file": "bm25-only.md",
        "chunk_idx": 0,
        "heading_context": "",
        "prefix": "From bm25-only",
        "body": "FINDME_UNIQUE is the exact identifier you are looking for.",
        "source_path": "/fake/bm25-only.md",
    })

    # regular chunks → u (cosine=1 with query), bm25 chunk → anti_u (cosine=-1)
    vectors = np.vstack([np.stack([u] * 6), anti_u[np.newaxis, :]])
    bm25    = rc_mod._BM25Index([c["body"] for c in chunks])

    # Patch _load_index to return controlled cache
    fake_cache = {"manifest": {}, "chunks": chunks, "vectors": vectors, "bm25": bm25}
    monkeypatch.setattr(rc_mod, "_load_index", lambda: fake_cache)
    # Query embedding = u (cosine=1 with regular chunks, -1 with bm25 chunk)
    monkeypatch.setattr(embed_mod, "embed", lambda texts: [u.tolist() for _ in texts])

    results = rc_mod.search("FINDME_UNIQUE", top_k=5)

    result_ids = [r["id"] for r in results]
    assert "bm25-only" in result_ids, (
        f"S6 RRF must boost BM25-only chunk into top-5. Got ids: {result_ids}"
    )

    # Confirm pure cosine would NOT include it (anti_u → cosine=-1 → rank 7 of 7)
    cosine_scores = rc_mod._cosine_matrix(u, vectors)
    pure_cosine_top5_files = [chunks[i]["file"] for i in np.argsort(-cosine_scores)[:5]]
    assert "bm25-only.md" not in pure_cosine_top5_files, (
        "Test setup error: bm25-only should be absent from pure-cosine top-5"
    )


def test_best_window_contains_query_term(tmp_path, monkeypatch):
    """content (250-char best window) must contain the query term when it appears in body."""
    ref_dir = tmp_path / "references"
    idx_dir = tmp_path / "cache" / "ref_chunks"

    # NEEDLE42 appears in the middle of a long body so the window must slide to find it
    filler  = "irrelevant text " * 20   # ~320 chars
    needle  = " NEEDLE42 appears here "  # unique query token in the middle
    body    = filler + needle + filler   # total ~660 chars > 250
    _write_refs(ref_dir, {"haystack.md": "# Haystack\n\n" + body + "\n"})

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",   idx_dir)
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    rc_mod.build_index(incremental=True)
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    results = rc_mod.search("NEEDLE42", top_k=5)
    assert len(results) > 0, "Expected at least one result"

    # BM25 should boost the NEEDLE42 chunk; the 250-char window should contain it
    any_hit = any("NEEDLE42" in r["content"] for r in results)
    assert any_hit, (
        f"Expected NEEDLE42 in at least one content window (best_window must slide to it). "
        f"Contents: {[r['content'][:60] for r in results]}"
    )


def test_search_returns_body_field(tmp_path, monkeypatch):
    """search() results must include 'body' = full chunk body (longer than 250-char content)."""
    ref_dir = tmp_path / "references"
    idx_dir = tmp_path / "cache" / "ref_chunks"
    # Ensure body is definitely > 250 chars
    long_body = "longword " * 60   # ~540 chars
    _write_refs(ref_dir, {"test-body.md": "# TestBody\n\n" + long_body + "\n"})

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",   idx_dir)
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    rc_mod.build_index(incremental=True)
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    results = rc_mod.search("longword", top_k=5)
    assert len(results) > 0, "Expected at least one result"

    for r in results:
        assert "body" in r, "Result must include 'body' key"
        assert "rrf" in r,  "Result must include 'rrf' key"
        # body = full chunk body, NOT truncated to 250
        assert len(r["body"]) > 250, (
            f"'body' should be the full chunk (> 250 chars), got {len(r['body'])}"
        )
        # content = best-window snippet ≤ 250 chars
        assert len(r["content"]) <= 250, (
            f"'content' should be the 250-char window, got {len(r['content'])}"
        )
        assert isinstance(r["rrf"], float), "rrf must be a float"


def test_no_zero_vectors_after_failing_embed(tmp_path, monkeypatch):
    """Chunks whose embed persistently returns wrong dim are excluded, not zero-vectored."""
    ref_dir = tmp_path / "references"
    idx_dir = tmp_path / "cache" / "ref_chunks"
    _write_refs(ref_dir, {
        "good.md":   "# Good\n\nThis is good content.\n\n"   + "g " * 200,
        "poison.md": "# Poison\n\nThis is poisonous content.\n\n" + "p " * 200,
    })

    dim, bad_dim = 2560, 768

    def mixed_embed(texts):
        """Return 768-dim for texts containing 'poison', 2560-dim for others."""
        vecs = []
        for t in texts:
            seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:4], "big")
            rng  = np.random.RandomState(seed)
            d    = bad_dim if "poison" in t.lower() else dim
            v    = rng.randn(d).astype(np.float32)
            if d == dim:
                v /= max(float(np.linalg.norm(v)), 1e-9)
            vecs.append(v.tolist())
        return vecs

    monkeypatch.setattr(embed_mod, "embed", mixed_embed)
    monkeypatch.setattr(rc_mod, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",   idx_dir)
    monkeypatch.setattr(rc_mod, "_index_cache", None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    m = rc_mod.build_index(incremental=True)

    assert m is not None, "Build must complete (good.md chunks survive)"

    # No zero vectors — failing chunks are excluded, not stored as zeros
    vecs  = np.load(str(idx_dir / "vectors.npy"))
    norms = np.linalg.norm(vecs, axis=1)
    assert not np.any(norms == 0), (
        f"Zero vectors found at indices {np.where(norms == 0)[0]}; "
        "failing embeds must be excluded from the index"
    )

    # good.md chunks present, poison.md chunks excluded
    with open(idx_dir / "chunks.jsonl") as f:
        stored_chunks = [json.loads(l) for l in f if l.strip()]
    stored_files = {c["file"] for c in stored_chunks}
    assert "good.md"   in stored_files, "good.md chunks must be stored"
    assert "poison.md" not in stored_files, "poison.md chunks must be excluded (embed failed)"


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import inspect

    class _MP:
        def __init__(self):
            self._undo = []
            self._envs = []

        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name, None)))
            object.__setattr__(obj, name, val) if not isinstance(obj, type) else setattr(obj, name, val)
            # Works for module objects too
            try:
                old = getattr(obj, name)
            except AttributeError:
                old = None
            self._undo.append((obj, name, old))
            setattr(obj, name, val)

        def setenv(self, key, val):
            old = os.environ.get(key)
            os.environ[key] = val
            self._envs.append((key, old))

        def delenv(self, key, raising=True):
            old = os.environ.get(key)
            if key in os.environ:
                del os.environ[key]
            elif raising:
                raise KeyError(key)
            self._envs.append((key, old))

        def undo(self):
            for obj, name, old in reversed(self._undo):
                if old is None:
                    try:
                        delattr(obj, name)
                    except Exception:
                        pass
                else:
                    setattr(obj, name, old)
            for key, old in reversed(self._envs):
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
            self._undo.clear()
            self._envs.clear()

    import pathlib
    import tempfile

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        mp = _MP()
        with tempfile.TemporaryDirectory() as td:
            try:
                sig = inspect.signature(fn)
                kwargs = {}
                if "tmp_path" in sig.parameters:
                    kwargs["tmp_path"] = pathlib.Path(td)
                if "monkeypatch" in sig.parameters:
                    kwargs["monkeypatch"] = mp
                fn(**kwargs)
                print(f"PASS  {name}")
                passed += 1
            except Exception as e:
                import traceback
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
                failed += 1
            finally:
                mp.undo()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def test_chunker_hard_caps_unpunctuated_block():
    """A long table/log block with no sentence punctuation must not exceed CHUNK_MAX_CHARS."""
    from knowledge import chunker
    table = "\n".join(f"| row {i} | host-{i} | value {i} |" for i in range(600))  # ~17k chars, no .!?
    longline = "x" * 5000  # single line, no newlines
    text = "# Audit\n\nIntro paragraph.\n\n" + table + "\n\n" + longline + "\n\nOutro."
    chunks = chunker.chunk_overlap(text, "t.md")
    assert chunks
    assert max(len(c[0]) for c in chunks) <= chunker.CHUNK_MAX_CHARS
    joined = " ".join(c[0] for c in chunks)
    assert "row 0 " in joined and "row 599 " in joined  # nothing dropped


def test_chunker_short_text_unchanged():
    from knowledge import chunker
    text = "# H\n\nOne short paragraph. Two sentences!"
    assert chunker.chunk_overlap(text, "s.md")[0][0].endswith("Two sentences!")


def test_synthetic_prefix_names_file_and_heading():
    from knowledge import chunker
    assert chunker.synthetic_prefix("/r/gateway-ops_notes.md", "Restart") == \
        "This passage is from gateway ops notes: Restart."
    assert chunker.synthetic_prefix("", "") == "This passage is from unknown."
