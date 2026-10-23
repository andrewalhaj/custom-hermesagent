"""The two top-level sectors that both plausibly own the knowledge store's tables must name their boundary.

Run: python -m pytest scripts/tests/test_taxonomy.py -v
The taxonomy file is local-only (gitignored), so these tests skip where it is absent.
"""
import os, sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from knowledge import jev

if not os.path.exists(os.path.join(os.path.dirname(jev.__file__), 'taxonomy.json')):
    pytest.skip('taxonomy.json not present', allow_module_level=True)


def test_overlapping_sectors_exclude_each_other():
    top = {n['path'][0]: n['description'] for n in jev._children([])}
    for name in ('infrastructure', 'development'):
        assert 'not' in top[name].lower().split('. ')[-1].lower(), top[name]
        assert len(top[name]) < 220


def test_tree_is_walkable_and_descriptions_short():
    nodes = jev._taxonomy()
    assert all(len(n['description']) < 220 for n in nodes)
    assert {n['path'][0] for n in nodes} <= {n['path'][0] for n in jev._children([])}
    assert all(jev._children(n['path']) is not None for n in nodes)
