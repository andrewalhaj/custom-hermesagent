"""_lib.load_env parses ~/.hermes/.env the way every caller expects.

Run: python -m pytest scripts/tests/test_lib.py -v
"""
import os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from _lib import load_env  # noqa: E402


def test_load_env_parses_quotes_comments_and_equals_in_value(tmp_path):
    p = tmp_path / '.env'
    p.write_text('# comment\n\nA=1\n B = "two" \nC=\'x=y\'\nnot a pair\n')
    assert load_env(str(p)) == {'A': '1', 'B': 'two', 'C': 'x=y'}


def test_load_env_missing_file_is_empty(tmp_path):
    assert load_env(str(tmp_path / 'nope')) == {}
