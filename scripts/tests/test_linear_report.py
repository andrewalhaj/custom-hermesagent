"""linear_report: errors exit non-zero (cron must not deliver a dead report as a quiet day),
results page past 100, and weekly "closed" means closed this week, not touched this week.

Run: python -m pytest scripts/tests/test_linear_report.py -v
"""
import io, json, os, sys, urllib.error

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import linear_report  # noqa: E402


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_api(monkeypatch, pages):
    """Serve `pages` (response dicts) in order; record each request's variables."""
    sent = []

    def urlopen(req, timeout=None):
        sent.append(json.loads(req.data)['variables'])
        p = pages.pop(0)
        if isinstance(p, Exception):
            raise p
        return _Resp(json.dumps(p).encode())

    monkeypatch.setenv('LINEAR_API_KEY', 'test-key')
    monkeypatch.setattr(linear_report.urllib.request, 'urlopen', urlopen)
    return sent


def _page(nodes, more=False, cursor=None):
    return {'data': {'issues': {'nodes': nodes,
                                'pageInfo': {'hasNextPage': more, 'endCursor': cursor}}}}


def test_graphql_errors_exit_nonzero(monkeypatch):
    _fake_api(monkeypatch, [{'errors': [{'message': 'bad filter'}], 'data': None}])
    with pytest.raises(SystemExit) as e:
        linear_report.main('weekly')
    assert e.value.code not in (0, None)


def test_http_401_exits_nonzero(monkeypatch):
    err = urllib.error.HTTPError(linear_report.API_URL, 401, 'Unauthorized', {}, io.BytesIO(b''))
    _fake_api(monkeypatch, [err])
    with pytest.raises(SystemExit) as e:
        linear_report.main('digest')
    assert e.value.code not in (0, None)


def test_missing_key_exits_nonzero(monkeypatch):
    monkeypatch.delenv('LINEAR_API_KEY', raising=False)
    with pytest.raises(SystemExit) as e:
        linear_report.main('stale')
    assert e.value.code not in (0, None)


def test_issues_follow_pages(monkeypatch):
    sent = _fake_api(monkeypatch, [_page([{'identifier': 'A-1'}], True, 'c1'),
                                   _page([{'identifier': 'A-2'}])])
    got = linear_report.issues({}, 'identifier')
    assert [i['identifier'] for i in got] == ['A-1', 'A-2']
    assert sent[1]['after'] == 'c1'


def test_weekly_closed_filters_on_close_date_not_updated(monkeypatch):
    sent = _fake_api(monkeypatch, [_page([]), _page([])])
    linear_report.main('weekly')
    closed_filter = json.dumps(sent[0]['filter'])
    assert 'updatedAt' not in closed_filter
    assert 'completedAt' in closed_filter and 'canceledAt' in closed_filter


def test_weekly_reports_counts(monkeypatch, capsys):
    closed = [{'identifier': 'A-1', 'title': 'x', 'createdAt': '2026-10-01T00:00:00Z',
               'completedAt': '2026-10-03T00:00:00Z', 'canceledAt': None}]
    opened = [{'identifier': 'A-2', 'title': 'y'}, {'identifier': 'A-3', 'title': 'z'}]
    _fake_api(monkeypatch, [_page(closed), _page(opened)])
    linear_report.main('weekly')
    out = capsys.readouterr().out
    assert '1 closed' in out and '2 opened' in out and 'Avg close: 2.0 days' in out


def test_stale_silent_when_none(monkeypatch, capsys):
    _fake_api(monkeypatch, [_page([])])
    linear_report.main('stale')
    assert capsys.readouterr().out == ''
