"""Step 1.1: the hourly cleanup must never delete a fact's only copy.

Run: python -m pytest scripts/tests/test_knowledge_maintenance.py -v
"""
import io, json, os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import knowledge.supabase_client as sc
import knowledge_maintenance as km


def test_all_rows_drops_ids_repeated_across_pages(monkeypatch):
    # An insert between pages shifts the offset: row 'b' comes back on both pages.
    pages = [[{'id': 'a'}, {'id': 'b'}], [{'id': 'b'}, {'id': 'c'}], []]
    monkeypatch.setattr(sc, 'get_supabase', lambda: ('http://x', 'k'))
    monkeypatch.setattr(sc.urllib.request, 'urlopen',
                        lambda req, timeout: io.BytesIO(json.dumps(pages.pop(0)).encode()))
    assert [r['id'] for r in sc._supa_all_rows(page=2)] == ['a', 'b', 'c']


def test_dedup_never_targets_the_kept_row():
    same = {'id': 'x', 'text': 'Fact', 'stored_at': '2026-01-01'}
    assert km.dedup_ids([same, dict(same)]) == []


def test_dedup_deletes_younger_copies_only():
    facts = [{'id': 'new', 'text': 'Fact ', 'stored_at': '2026-02-01'},
             {'id': 'old', 'text': 'fact', 'stored_at': '2026-01-01'}]
    assert km.dedup_ids(facts) == ['new']


def test_dedup_survives_null_text():
    facts = [{'id': 'a', 'text': None, 'stored_at': '1'},
             {'id': 'b', 'text': None, 'stored_at': '2'}]
    assert km.dedup_ids(facts) == []


def test_corrupt_fact_kept_when_clean_text_stored_nowhere_else():
    blob = 'Clean fact. Related knowledge: [high] ' + 'x' * 400
    facts = [{'id': 'c', 'text': blob}]
    assert km.corrupt_ids(facts) == []
    facts.append({'id': 'k', 'text': 'Clean fact.'})
    assert km.corrupt_ids(facts) == ['c']


def test_delete_backs_up_full_row_before_deleting(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(km, 'DELETED_LOG', str(tmp_path / 'deleted.jsonl'))
    monkeypatch.setattr(km, '_supa_select',
                        lambda q: calls.append('get') or [{'id': 'a', 'text': 't', 'embedding': [1]}])
    monkeypatch.setattr(km.urllib.request, 'urlopen', lambda req, timeout: calls.append(req.method))
    km.delete_fact('http://x', 'k', 'a', reason='dedup')
    assert calls == ['get', 'DELETE']
    row = json.loads((tmp_path / 'deleted.jsonl').read_text())
    assert row['row']['embedding'] == [1] and row['reason'] == 'dedup'


def test_delete_skips_row_already_gone(monkeypatch, tmp_path):
    monkeypatch.setattr(km, 'DELETED_LOG', str(tmp_path / 'deleted.jsonl'))
    monkeypatch.setattr(km, '_supa_select', lambda q: [])
    monkeypatch.setattr(km.urllib.request, 'urlopen', lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert km.delete_fact('http://x', 'k', 'a', reason='dedup') is False


def test_all_rows_walks_each_row_once_while_rows_land(monkeypatch):
    # Audit finding 16: heartbeat inserts the newest row mid-walk. Ordered oldest-first, new rows land
    # past the cursor, so no page shifts: every row is served exactly once (the id dedup isn't needed).
    table = [{'id': f'{i:02d}', 'stored_at': f'2026-01-{i:02d}'} for i in range(1, 8)]
    served = []

    def urlopen(req, timeout):
        order = req.full_url.split('order=', 1)[1]
        desc = order.startswith('stored_at.desc')
        rows = sorted(table, key=lambda r: (r['stored_at'], r['id']), reverse=desc)
        start, end = map(int, req.get_header('Range').split('-'))
        page = rows[start:end + 1]
        served.extend(r['id'] for r in page)
        table.append({'id': f'n{len(table)}', 'stored_at': f'2026-02-{len(table):02d}'})  # lands mid-walk
        return io.BytesIO(json.dumps(page).encode())

    monkeypatch.setattr(sc, 'get_supabase', lambda: ('http://x', 'k'))
    monkeypatch.setattr(sc.urllib.request, 'urlopen', urlopen)
    sc._supa_all_rows(page=3)
    originals = [s for s in served if not s.startswith('n')]
    assert sorted(originals) == [f'{i:02d}' for i in range(1, 8)]
    assert len(served) == len(set(served))


# ── Step 2: abstain-once marker ──────────────────────────────────────────
from datetime import datetime, timedelta, timezone


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _run_backfill(monkeypatch, tmp_path, capsys, rows, classify, live_meta=None, urlopen=None):
    """Run main() with mocked store + HTTP; returns (stdout, PATCH requests, classifier inputs)."""
    import knowledge.storage as storage
    sent, seen = [], []
    monkeypatch.setattr(km, 'STATE_PATH', str(tmp_path / 'state.json'))
    monkeypatch.setattr(km, '_supa_all_rows', lambda **kw: rows)
    monkeypatch.setattr(km, 'get_supabase', lambda: ('http://x', 'k'))
    monkeypatch.setattr(km.urllib.request, 'urlopen', urlopen or (lambda req, timeout: sent.append(req)))
    live = live_meta or {}
    monkeypatch.setattr(km, '_supa_select', lambda q: [{'metadata': live.get(
        q.split('id=eq.')[1], next(r['metadata'] for r in rows if r['id'] == q.split('id=eq.')[1]))}])
    monkeypatch.setattr(storage, '_classify_sector', lambda t: seen.append(t) or classify(t))
    monkeypatch.delenv('SECTOR_RETRY_DAYS', raising=False)
    km.main()
    return capsys.readouterr().out, sent, seen


def _row(i, **kw):
    return {'id': f'id{i}', 'text': f'fact {i}', 'stored_at': f'2026-01-{i:02d}', 'sector_path': None, 'metadata': None, **kw}


def test_abstained_row_gets_marker_merged_into_metadata(monkeypatch, tmp_path, capsys):
    rows = [_row(1, metadata={'k': 'v'})]
    _, sent, _ = _run_backfill(monkeypatch, tmp_path, capsys, rows, lambda t: None)
    assert len(sent) == 1 and sent[0].method == 'PATCH' and sent[0].full_url.endswith('id=eq.id1')
    meta = json.loads(sent[0].data)['metadata']
    assert meta['k'] == 'v' and datetime.fromisoformat(meta['sector_attempted_at']).tzinfo is not None


def test_fresh_marker_is_skipped_without_calling_classifier(monkeypatch, tmp_path, capsys):
    rows = [_row(1, metadata={'sector_attempted_at': _iso(1)})]
    _, sent, seen = _run_backfill(monkeypatch, tmp_path, capsys, rows, lambda t: None)
    assert seen == [] and sent == []


def test_old_marker_is_retried(monkeypatch, tmp_path, capsys):
    rows = [_row(1, metadata={'sector_attempted_at': _iso(8)})]
    _, sent, seen = _run_backfill(monkeypatch, tmp_path, capsys, rows, lambda t: ['dev'])
    assert seen == ['fact 1'] and json.loads(sent[0].data) == {'sector_path': ['dev']}


def test_report_line_counts_classified_abstained_skipped(monkeypatch, tmp_path, capsys):
    rows = [_row(1), _row(2), _row(3, metadata={'sector_attempted_at': _iso(0)})]
    out, _, _ = _run_backfill(monkeypatch, tmp_path, capsys, rows,
                              lambda t: ['dev'] if t == 'fact 1' else None)
    assert "Backfill: 1 classified, 1 abstained, 1 skipped (recent abstain) (of 3 unlabeled)" in out


def test_whole_batch_abstain_of_three_or_more_is_not_marked(monkeypatch, tmp_path, capsys):
    out, sent, seen = _run_backfill(monkeypatch, tmp_path, capsys, [_row(1), _row(2), _row(3)], lambda t: None)
    assert len(seen) == 3 and sent == []
    assert "Backfill: 0 classified, 3 abstained (not marked: whole batch abstained)" in out


def test_marker_keeps_metadata_written_since_the_snapshot(monkeypatch, tmp_path, capsys):
    _, sent, _ = _run_backfill(monkeypatch, tmp_path, capsys, [_row(1, metadata={'old': 1})], lambda t: None,
                               live_meta={'id1': {'old': 1, 'new': 2}})
    assert json.loads(sent[0].data)['metadata']['new'] == 2


def test_failed_marker_patch_is_not_counted_as_abstained(monkeypatch, tmp_path, capsys):
    def boom(req, timeout):
        raise OSError('down')
    out, _, _ = _run_backfill(monkeypatch, tmp_path, capsys, [_row(1)], lambda t: None, urlopen=boom)
    assert "backfill FAIL id1" in out and "abstained" not in out


def _run_one(monkeypatch, tmp_path, capsys, meta, env=None):
    if env:
        monkeypatch.setenv('SECTOR_RETRY_DAYS', env)
    import knowledge.storage as storage
    sent = []
    (tmp_path / 'state.json').unlink(missing_ok=True)  # else the high-water mark exits early
    monkeypatch.setattr(km, 'STATE_PATH', str(tmp_path / 'state.json'))
    monkeypatch.setattr(km, '_supa_all_rows', lambda **kw: [_row(1, metadata=meta)])
    monkeypatch.setattr(km, 'get_supabase', lambda: ('http://x', 'k'))
    monkeypatch.setattr(km, '_supa_select', lambda q: [{'metadata': meta}])
    monkeypatch.setattr(km.urllib.request, 'urlopen', lambda req, timeout: sent.append(req))
    seen = []
    monkeypatch.setattr(storage, '_classify_sector', lambda t: seen.append(t) or ['dev'])
    km.main()
    return seen


def test_bad_retry_days_env_behaves_as_seven(monkeypatch, tmp_path, capsys):
    assert _run_one(monkeypatch, tmp_path, capsys, {'sector_attempted_at': _iso(6)}, env='7d') == []
    assert _run_one(monkeypatch, tmp_path, capsys, {'sector_attempted_at': _iso(8)}, env='7d') == ['fact 1']


def test_garbage_marker_is_retried(monkeypatch, tmp_path, capsys):
    assert _run_one(monkeypatch, tmp_path, capsys, {'sector_attempted_at': 'garbage'}) == ['fact 1']


def test_naive_marker_is_retried(monkeypatch, tmp_path, capsys):
    naive = datetime.now().isoformat()  # no tzinfo
    assert _run_one(monkeypatch, tmp_path, capsys, {'sector_attempted_at': naive}) == ['fact 1']
