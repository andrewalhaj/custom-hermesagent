"""factbench scoring: a hit is a needle inside the prefetch block, counted per question kind.

Run:  python -m pytest scripts/knowledge/tests/test_factbench.py -v
"""
import os
import sys

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from knowledge import factbench as fb


def test_run_scores_direct_and_indirect_separately():
    facts = [(["10.0.0.1"], "q-direct-a", "q-indirect-a"),
             (["port 99", "alt-needle"], "q-direct-b", "q-indirect-b")]
    blocks = {"q-direct-a": "- [0.9] Studio IS 10.0.0.1", "q-indirect-a": "- [0.4] unrelated",
              "q-direct-b": "", "q-indirect-b": "- [0.5] see ALT-NEEDLE doc"}
    res = fb.run(lambda q: blocks[q], facts=facts, where=lambda n: ["kb"])
    assert (res["direct"], res["indirect"]) == (0.5, 0.5)
    assert [(r["direct"], r["indirect"]) for r in res["rows"]] == [(True, False), (False, True)]


def test_prefetch_returning_none_is_a_miss():
    res = fb.run(lambda q: None, facts=[(["x"], "d", "i")], where=lambda n: [])
    assert res["direct"] == res["indirect"] == 0


def test_load_facts_round_trips(tmp_path):
    p = tmp_path / "f.json"
    p.write_text('[{"needles": ["a"], "direct": "d", "indirect": "i"}]')
    assert fb.load_facts(p) == [(["a"], "d", "i")]


@pytest.mark.skipif(not fb.FACTS_PATH.exists(), reason="private fact file is not in git")
def test_needles_are_lowercase_and_ilike_safe():
    for needles, direct, indirect in fb.load_facts():
        assert direct and indirect and direct != indirect
        for n in needles:
            assert n == n.lower() and not set(n) & set(",()"), n
