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
