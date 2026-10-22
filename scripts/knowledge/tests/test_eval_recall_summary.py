"""eval_recall: summarize + print path survives a bench with missing buckets and prints paraphrase."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from knowledge import eval_recall as er


def row(source_type, hit, group=None):
    return {"source_type": source_type, "hit": hit, "group": group}


def test_missing_session_bucket_does_not_raise(capsys):
    s = er.summarize([row("knowledge", True), row("knowledge", False)], 50, 1)
    assert set(s) == {"all", "knowledge"}
    er.print_summary(s)
    out = capsys.readouterr().out
    assert "knowledge" in out and "session" not in out


def test_paraphrase_bucket_is_printed(capsys):
    s = er.summarize([row("knowledge", True), row("session", True, "paraphrase")], 50, 1)
    er.print_summary(s)
    assert "paraphrase" in capsys.readouterr().out
