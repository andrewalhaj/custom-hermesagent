"""kbench: eval prints P@5/MRR; --alert is silent unless P@5 regresses; a failed run is never silent.

Run: python -m pytest scripts/tests/test_kbench.py -v
"""
import os, sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import kbench  # noqa: E402


def _result(p5):
    return {'p@5': p5, 'MRR': 0.5, 'total': 2, 'passed': int(p5 * 2),
            'results': [{'query': 'q1', 'found': True, 'rank': 0},
                        {'query': 'q2', 'found': p5 == 1.0, 'rank': -1}]}


def test_eval_prints_metrics_and_misses(monkeypatch, capsys):
    monkeypatch.setattr(kbench, 'eval_benchmark', lambda top_k: _result(0.5))
    kbench.main(['eval'])
    out = capsys.readouterr().out
    assert 'P@5=50.0%' in out and 'MRR=0.5' in out and "MISS: 'q2'" in out


def test_alert_silent_when_healthy(monkeypatch, capsys):
    monkeypatch.setattr(kbench, 'eval_benchmark', lambda top_k: _result(1.0))
    kbench.main(['eval', '--alert'])
    assert capsys.readouterr().out == ''


def test_alert_fires_on_regression(monkeypatch, capsys):
    monkeypatch.setattr(kbench, 'eval_benchmark', lambda top_k: _result(0.5))
    kbench.main(['eval', '--alert'])
    assert 'regression' in capsys.readouterr().out


def test_error_result_exits_nonzero(monkeypatch):
    monkeypatch.setattr(kbench, 'eval_benchmark', lambda top_k: {'error': 'no_benchmark_file', 'path': '/x'})
    with pytest.raises(SystemExit) as e:
        kbench.main(['eval', '--alert'])
    assert e.value.code not in (0, None)


def test_sweep_runs_each_k(monkeypatch, capsys):
    ks = []
    monkeypatch.setattr(kbench, 'eval_benchmark', lambda top_k: ks.append(top_k) or _result(1.0))
    kbench.main(['sweep', '3', '8'])
    assert ks == [3, 8]
    assert 'k=3' in capsys.readouterr().out
