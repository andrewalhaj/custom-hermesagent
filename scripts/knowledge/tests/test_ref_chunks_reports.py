"""
Tests for reports corpus extension in knowledge.ref_chunks.

Tests:
  - Allowlist gating: only allowlisted files in REPORTS_DIR are indexed
  - Label distinction: 'reports/' prefix in 'file' and 'id' fields
  - Secret guard: chunks containing credential patterns are excluded
  - Incremental build: unchanged reports files are not re-embedded

Run:
  cd ~/.hermes/scripts
  /usr/local/lib/hermes-agent/venv/bin/python3 -m pytest knowledge/tests/test_ref_chunks_reports.py -v
"""
import os
import sys
import json
import hashlib
import importlib

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import numpy as np

rc_mod    = importlib.import_module("knowledge.ref_chunks")
embed_mod = importlib.import_module("knowledge.embed")


# ── Fake embed factory (same as test_ref_chunks.py) ───────────────────────────

def make_fake_embed(dim: int = 2560):
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


def _write_refs(ref_dir, files):
    ref_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (ref_dir / fname).write_text(content)


# ── Allowlist gating tests ────────────────────────────────────────────────────

def test_reports_allowlist_gates_unlisted_files(tmp_path, monkeypatch):
    """Files in REPORTS_DIR not in REPORTS_ALLOWLIST must NOT be indexed."""
    ref_dir     = tmp_path / "references"
    idx_dir     = tmp_path / "cache" / "ref_chunks"
    reports_dir = tmp_path / "reports"

    _write_refs(ref_dir, {
        "alpha.md": "# Alpha\n\nReference content alpha.\n\n" + "a " * 200,
    })
    _write_refs(reports_dir, {
        "allowed-report.md":   "# Allowed\n\nAllowed report content.\n\n"   + "x " * 200,
        "disallowed-report.md": "# Disallowed\n\nNot in allowlist.\n\n"     + "z " * 200,
    })

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME",       tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",         idx_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_DIR",       reports_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_ALLOWLIST", ("allowed-report.md",))
    monkeypatch.setattr(rc_mod, "_index_cache",      None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime",   0.0)

    m = rc_mod.build_index(incremental=True)

    assert m is not None
    file_keys = set(m["files"].keys())
    assert "reports/allowed-report.md" in file_keys,   "Allowlisted file must be indexed"
    assert "disallowed-report.md" not in file_keys,    "Non-allowlisted file must NOT be indexed"
    assert "reports/disallowed-report.md" not in file_keys


def test_reports_label_prefix_in_chunks(tmp_path, monkeypatch):
    """Reports chunks must have 'reports/' prefix in 'file' field and search 'id' field."""
    ref_dir     = tmp_path / "references"
    idx_dir     = tmp_path / "cache" / "ref_chunks"
    reports_dir = tmp_path / "reports"

    _write_refs(ref_dir, {})
    _write_refs(reports_dir, {
        "my-report.md": "# My Report\n\nReport content here.\n\n" + "r " * 200,
    })

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME",       tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",         idx_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_DIR",       reports_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_ALLOWLIST", ("my-report.md",))
    monkeypatch.setattr(rc_mod, "_index_cache",      None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime",   0.0)

    rc_mod.build_index(incremental=True)
    monkeypatch.setattr(rc_mod, "_index_cache",    None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    # Verify chunk 'file' field
    with open(idx_dir / "chunks.jsonl") as f:
        chunks = [json.loads(l) for l in f if l.strip()]
    rep_chunks = [c for c in chunks if c.get("file", "").startswith("reports/")]
    assert len(rep_chunks) > 0, "Expected at least one reports/ chunk"
    for c in rep_chunks:
        assert c["file"] == "reports/my-report.md", f"Bad file label: {c['file']}"

    # Verify search 'id' field
    results = rc_mod.search("report content", top_k=5)
    rep_results = [r for r in results if r["id"].startswith("reports/")]
    assert len(rep_results) > 0, "Expected at least one reports/ result in search"
    for r in rep_results:
        assert r["id"].startswith("reports/"), f"Bad id label: {r['id']}"


def test_reports_secret_guard_excludes_credential_chunks(tmp_path, monkeypatch):
    """Chunks containing credential-looking strings must be excluded from the index."""
    ref_dir     = tmp_path / "references"
    idx_dir     = tmp_path / "cache" / "ref_chunks"
    reports_dir = tmp_path / "reports"

    _write_refs(ref_dir, {})
    # clean-chunk section is safe; secret-chunk section has a fake credential
    clean_body   = "This section describes the deployment plan.\n\n" + "content " * 100
    secret_body  = "The API_KEY=sk-abcdef1234567890abcdef1234567890 is used here.\n\n" + "secret " * 50  # gitleaks:allow
    _write_refs(reports_dir, {
        "mixed-report.md": f"# Clean\n\n{clean_body}\n\n# Secret\n\n{secret_body}",
    })

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME",       tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",         idx_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_DIR",       reports_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_ALLOWLIST", ("mixed-report.md",))
    monkeypatch.setattr(rc_mod, "_index_cache",      None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime",   0.0)

    m = rc_mod.build_index(incremental=True)

    assert m is not None
    with open(idx_dir / "chunks.jsonl") as f:
        chunks = [json.loads(l) for l in f if l.strip()]

    # No chunk body should contain a raw credential value
    for c in chunks:
        body = c.get("body", "")
        assert "sk-abcdef1234567890abcdef1234567890" not in body, \
            f"Credential found in indexed chunk: {body[:80]}"

    # But the clean chunk must still be present
    rep_chunks = [c for c in chunks if c.get("file", "").startswith("reports/")]
    assert len(rep_chunks) > 0, "Clean report chunks must survive the secret guard"


def test_reports_incremental_skips_unchanged(tmp_path, monkeypatch):
    """Second build must not re-embed an unchanged reports file."""
    ref_dir     = tmp_path / "references"
    idx_dir     = tmp_path / "cache" / "ref_chunks"
    reports_dir = tmp_path / "reports"

    _write_refs(ref_dir, {})
    _write_refs(reports_dir, {
        "stable-report.md": "# Stable\n\nUnchanged content.\n\n" + "s " * 200,
    })

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME",       tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",         idx_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_DIR",       reports_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_ALLOWLIST", ("stable-report.md",))
    monkeypatch.setattr(rc_mod, "_index_cache",      None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime",   0.0)

    rc_mod.build_index(incremental=True)
    calls_after_first = len(fake.calls)
    assert calls_after_first > 0

    fake.calls.clear()
    monkeypatch.setattr(rc_mod, "_index_cache",    None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime", 0.0)

    m2 = rc_mod.build_index(incremental=True)

    assert m2 is not None
    assert len(fake.calls) == 0, (
        f"Unchanged reports file must not be re-embedded; got {len(fake.calls)} calls"
    )
    assert "reports/stable-report.md" in m2["files"]


def test_reports_and_references_coexist(tmp_path, monkeypatch):
    """References and reports files must coexist without collision."""
    ref_dir     = tmp_path / "references"
    idx_dir     = tmp_path / "cache" / "ref_chunks"
    reports_dir = tmp_path / "reports"

    # Both have a file with the same stem — must not collide
    _write_refs(ref_dir,     {"plan.md": "# Plan\n\nReference plan content.\n\n" + "ref " * 200})
    _write_refs(reports_dir, {"plan.md": "# Plan\n\nReports plan content.\n\n"  + "rep " * 200})

    fake = make_fake_embed(2560)
    monkeypatch.setattr(embed_mod, "embed", fake)
    monkeypatch.setattr(rc_mod, "HERMES_HOME",       tmp_path)
    monkeypatch.setattr(rc_mod, "INDEX_DIR",         idx_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_DIR",       reports_dir)
    monkeypatch.setattr(rc_mod, "REPORTS_ALLOWLIST", ("plan.md",))
    monkeypatch.setattr(rc_mod, "_index_cache",      None)
    monkeypatch.setattr(rc_mod, "_manifest_mtime",   0.0)

    m = rc_mod.build_index(incremental=True)

    assert m is not None
    assert "plan.md"          in m["files"], "Reference plan.md must be in manifest"
    assert "reports/plan.md"  in m["files"], "Reports plan.md must be in manifest"

    with open(idx_dir / "chunks.jsonl") as f:
        chunks = [json.loads(l) for l in f if l.strip()]

    ref_chunks  = [c for c in chunks if c["file"] == "plan.md"]
    rep_chunks  = [c for c in chunks if c["file"] == "reports/plan.md"]
    assert len(ref_chunks) > 0,  "Reference chunks must be present"
    assert len(rep_chunks) > 0,  "Reports chunks must be present"
