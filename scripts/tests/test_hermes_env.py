"""hermes_env: env-then-.env lookup, Python and bash flavours.

Run: .venv/bin/python -m pytest scripts/tests/test_hermes_env.py -v
"""
import os, subprocess, sys
from pathlib import Path

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, SCRIPTS)

import pytest

import hermes_env

DOTENV = """# comment
PLAIN=plain-value
DQ="double quoted"
SQ='single quoted'
export EXPORTED=exp-value
this line is malformed
DUP=first
DUP=second
HOMEDIR=~/somewhere
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text(DOTENV)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(hermes_env, 'HERMES_HOME', tmp_path)
    hermes_env._cache.clear()
    for k in ('PLAIN', 'DQ', 'SQ', 'EXPORTED', 'DUP', 'MISSING', 'HOMEDIR'):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_parses_dotenv_forms(home):
    assert hermes_env.env('PLAIN') == 'plain-value'
    assert hermes_env.env('DQ') == 'double quoted'
    assert hermes_env.env('SQ') == 'single quoted'
    assert hermes_env.env('EXPORTED') == 'exp-value'


def test_later_duplicate_wins(home):
    assert hermes_env.env('DUP') == 'second'


def test_malformed_and_comment_ignored(home):
    assert hermes_env.env('MISSING') is None
    assert hermes_env.env('MISSING', 'dflt') == 'dflt'
    assert hermes_env.env('this line is malformed') is None


def test_environ_beats_file(home, monkeypatch):
    monkeypatch.setenv('PLAIN', 'from-environ')
    assert hermes_env.env('PLAIN') == 'from-environ'


def test_empty_environ_falls_back_to_file(home, monkeypatch):
    monkeypatch.setenv('PLAIN', '')
    assert hermes_env.env('PLAIN') == 'plain-value'


def test_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(hermes_env, 'HERMES_HOME', tmp_path)
    hermes_env._cache.clear()
    monkeypatch.delenv('PLAIN', raising=False)
    assert hermes_env.env('PLAIN', 'd') == 'd'


def test_require_returns_value(home):
    assert hermes_env.require('PLAIN') == 'plain-value'


def test_require_exits_naming_key_and_file(home):
    with pytest.raises(SystemExit) as e:
        hermes_env.require('MISSING')
    assert 'MISSING' in str(e.value) and str(home / '.env') in str(e.value)


def test_path_expands_user(home):
    assert hermes_env.path('HOMEDIR', '/x') == Path.home() / 'somewhere'
    assert hermes_env.path('MISSING', '~/d') == Path.home() / 'd'


def test_bash_helpers(home):
    lib = os.path.join(SCRIPTS, 'lib', 'hermes_env.sh')
    script = (f'set -euo pipefail; . "{lib}"; hermes_env DQ; hermes_env MISSING dflt; '
              'hermes_env DUP; rc=0; hermes_require MISSING || rc=$?; echo rc=$rc')
    env = {**os.environ, 'HERMES_HOME': str(home)}
    for k in ('DQ', 'DUP', 'MISSING'):
        env.pop(k, None)
    r = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
    assert r.stdout.splitlines() == ['double quoted', 'dflt', 'second', 'rc=1']
    assert 'MISSING' in r.stderr


PARITY = """  SPACED = padded value  
export   EXP="quoted export"
EMPTY=
EQ=a=b=c
MISMATCH="open
LONE="
INNER="has 'inner' quotes"
CRLF=crlf-value\r
#COMMENTED=no
"""


def test_python_and_bash_parsers_agree(tmp_path, monkeypatch):
    """The shell entry point mirrors hermes_env.py: same value for every key shape."""
    (tmp_path / '.env').write_text(PARITY)
    monkeypatch.setattr(hermes_env, 'HERMES_HOME', tmp_path)
    hermes_env._cache.clear()
    keys = ['SPACED', 'EXP', 'EMPTY', 'EQ', 'MISMATCH', 'LONE', 'INNER', 'CRLF', 'COMMENTED']
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    lib = os.path.join(SCRIPTS, 'lib', 'hermes_env.sh')
    env = {**os.environ, 'HERMES_HOME': str(tmp_path)}
    for k in keys:
        env.pop(k, None)
        r = subprocess.run(['bash', '-c', f'. "{lib}"; hermes_env {k} UNSET'], env=env,
                           capture_output=True, text=True)
        assert r.stdout.rstrip('\n') == hermes_env.env(k, 'UNSET'), k
    assert hermes_env.env('SPACED') == 'padded value'
    assert hermes_env.env('EXP') == 'quoted export'
    assert hermes_env.env('EQ') == 'a=b=c'
    assert hermes_env.env('INNER') == "has 'inner' quotes"
    assert hermes_env.env('MISMATCH') == '"open'
    assert hermes_env.env('EMPTY', 'dflt') == 'dflt'
    assert hermes_env.env('COMMENTED') is None
