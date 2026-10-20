"""knowledge.py is a CLI shim over the knowledge package (the old LanceDB monolith is gone).

Run: python -m pytest scripts/tests/test_knowledge_cli.py -v
"""
import os, runpy, sys

import pytest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCRIPTS)

import knowledge  # noqa: E402  (the package, not knowledge.py)


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, 'argv', ['knowledge.py', *argv])
    runpy.run_path(os.path.join(SCRIPTS, 'knowledge.py'), run_name='__main__')


def test_shim_does_not_import_lancedb():
    src = open(os.path.join(SCRIPTS, 'knowledge.py')).read()
    assert 'lancedb' not in src


def test_store_passes_tags_and_priority(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(knowledge, 'store', lambda text, **kw: seen.update(text=text, **kw) or 'abc123')
    monkeypatch.setenv('KNOWLEDGE_TAGS', 'a, b')
    monkeypatch.setenv('KNOWLEDGE_PRIORITY', 'high')
    _run(monkeypatch, 'store', 'hello', 'world')
    assert seen == {'text': 'hello world', 'tags': ['a', 'b'], 'priority': 'high',
                    'source': 'cli', 'provenance': 'explicit'}
    assert 'Stored: abc123' in capsys.readouterr().out


def test_search_falls_back_in_process_when_daemon_down(monkeypatch, capsys):
    import kb_client

    def down(*a, **k):
        raise kb_client.DaemonUnavailable('no socket')

    monkeypatch.setattr(kb_client, 'daemon_search', down)
    hit = {'id': 'f1', 'score': 0.9, 'priority': 'normal', 'tags': ['t'], 'text': 'gateway port 8787'}
    monkeypatch.setattr(knowledge, 'search', lambda q, top_k: [hit])
    _run(monkeypatch, 'search', 'gateway', 'port')
    out = capsys.readouterr().out
    assert 'gateway port 8787' in out and 'id: f1' in out


def test_unknown_command_prints_usage_and_fails(monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, 'build-graph')
    assert e.value.code == 1
    assert 'Usage' in capsys.readouterr().err
