"""query_router: rg gets a literal query; agentic runs the knowledge search once."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import query_router

HIT = {"id": "1", "text": "fact", "score": 0.9, "source": "a.md", "tags": []}


def _stub(monkeypatch, tmp_path):
    calls = {"search": 0, "rg": []}
    monkeypatch.setattr(query_router, "search_knowledge",
                        lambda q, top_k=8: calls.__setitem__("search", calls["search"] + 1) or [HIT])
    monkeypatch.setattr(query_router, "REFERENCES_DIR", str(tmp_path))
    monkeypatch.setattr(query_router, "MEMORY_PATH", str(tmp_path / "none"))
    monkeypatch.setattr(query_router, "USER_PATH", str(tmp_path / "none"))
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls["rg"].append(cmd) or
                        subprocess.CompletedProcess(cmd, 0, "", ""))
    return calls


def test_rg_query_is_literal(monkeypatch, tmp_path):
    calls = _stub(monkeypatch, tmp_path)
    query_router.tier_classic("foo(bar?", as_json=True)
    assert "-F" in calls["rg"][0]


def test_agentic_searches_once(monkeypatch, tmp_path):
    for as_json in (True, False):
        calls = _stub(monkeypatch, tmp_path)
        out = query_router.tier_agentic("deploy", as_json=as_json)
        assert calls["search"] == 1
        assert "a.md" in out
    assert json.loads(query_router.tier_agentic("deploy", as_json=True))["next_reads"] == ["a.md"]
