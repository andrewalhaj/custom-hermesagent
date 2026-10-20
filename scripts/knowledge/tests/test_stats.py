"""knowledge.stats (was utils): count_facts reads the exact count from Content-Range.

Run:  python -m pytest scripts/knowledge/tests/test_stats.py -v
"""
import os
import sys

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from knowledge import stats


class _Resp:
    headers = {"Content-Range": "0-0/5312"}


def test_count_facts_parses_content_range(monkeypatch):
    seen = {}
    monkeypatch.setattr(stats, "get_supabase", lambda: ("https://x.supabase.co", "k"))
    monkeypatch.setattr(stats.urllib.request, "urlopen",
                        lambda req, timeout: seen.update(method=req.get_method()) or _Resp())
    assert stats.count_facts() == 5312
    assert seen["method"] == "HEAD"
