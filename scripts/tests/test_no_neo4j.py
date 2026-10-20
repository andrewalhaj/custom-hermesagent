"""Neo4j / GRAPH_* removal guard: search() runs with no graph env, and nothing
under scripts/knowledge references neo4j or the graph-traverse flags."""
import os
import re
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE = os.path.join(SCRIPTS, "knowledge")
# Topic vocabulary describing stored facts (sector text + its embedded anchors), not integration code.
_DATA_FILES = {"taxonomy.json", "anchors.pkl"}

# Subprocess: keeps the probe's monkeypatch of knowledge.search out of this pytest session.
_PROBE = r"""
import importlib, sys
s = importlib.import_module('knowledge.search')  # package re-exports search(), shadowing the submodule
s._single_search = lambda q, *a, **k: [{'id': 'abc12345', 'text': 'nginx runs on the edge host', 'score': 0.9,
    'source': '', 'tags': [], 'priority': 'normal', 'match_source': 'vector+fts'}]
hits = s.search('where does nginx run', top_k=3)
assert [h['id'] for h in hits] == ['abc12345'], hits
assert 'neo4j' not in sys.modules
print('OK')
"""


def test_search_runs_without_graph_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GRAPH_", "NEO4J_"))}
    r = subprocess.run([sys.executable, "-c", _PROBE], cwd=SCRIPTS, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]


def test_no_neo4j_refs_in_knowledge():
    tracked = subprocess.run(["git", "-C", KNOWLEDGE, "ls-files"], capture_output=True,
                             text=True, check=True).stdout.split()
    pat = re.compile(r"neo4j|GRAPH_TRAVERSE|graph_traverse|GRAPH_EXPAND|graph_expand", re.I)
    offenders = [f for f in tracked if os.path.basename(f) not in _DATA_FILES
                 and pat.search(open(os.path.join(KNOWLEDGE, f), errors="ignore").read())]
    assert offenders == []
