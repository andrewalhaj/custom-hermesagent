"""Audit finding 3: the write-time duplicate gate. The RPC's `similarity` is an RRF rank score (1/(20+rank),
max ~0.048), so the old >=0.92 check never fired. Exact repeats (body_hash) are skipped; a near-duplicate
(real cosine >= threshold) supersedes the old row, which is backed up and deleted after the new row lands.

Run:  python -m pytest scripts/knowledge/tests/test_knn_dedup.py -v
"""
import json, os, sys

import pytest

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

if not hasattr(sys.modules.get('knowledge'), '__path__'):  # test_junk_filter may leave a stub module
    sys.modules.pop('knowledge', None)
from knowledge import storage
import knowledge_maintenance as km

VEC = {"The dispatcher listens on port 8080.": [1.0, 0.0],
       "The dispatcher listens on port 8081.": [0.98, 0.199],   # cosine 0.98: an update
       "edge-host runs nginx for the dashboard.": [0.83, 0.558]}    # cosine 0.83: a different fact


@pytest.fixture
def table(monkeypatch, tmp_path):
    rows, log = {}, []

    def select(q):
        if q.startswith("select=id&limit=1&body_hash=eq."):
            h = q.rsplit(".", 1)[1]
            return [{"id": i} for i, r in rows.items() if r["body_hash"] == h][:1]
        if q.startswith("select=id,vector,text&id=in."):
            ids = json.loads("[" + q.split("in.(", 1)[1][:-1] + "]")
            return [{"id": i, "vector": json.dumps(rows[i]["vector"])} for i in ids if i in rows]
        if q.startswith("select=id,tags,source,text&id=eq."):  # supersede guard's read of the old row
            r = rows.get(q.rsplit(".", 1)[1])
            return [r] if r else []
        if q.startswith("id=eq."):  # delete_fact's backup read
            i = q.split("&", 1)[0][6:]
            return [rows[i]] if i in rows else []
        raise AssertionError(q)

    def rpc(path, payload, prefer=""):
        # Real shape: every row comes back, similarity = 1/(20+rank) whatever the cosine.
        return [{"id": i, "similarity": 1 / (21 + n)} for n, i in enumerate(rows)][:payload["match_count"]]

    def delete(req, timeout):
        rows.pop(req.full_url.split("id=eq.")[1])

    monkeypatch.setattr(storage, "embed", lambda texts: [VEC[t] for t in texts])
    monkeypatch.setattr(storage, "_supa_select", select)
    monkeypatch.setattr(storage, "_supa_post", rpc)
    monkeypatch.setattr(storage, "_supa_insert", lambda row: rows.__setitem__(row["id"], row))
    monkeypatch.setattr(storage, "get_supabase", lambda: ("http://x", "k"))
    monkeypatch.setattr(storage, "_classify_sector", lambda text: [])
    monkeypatch.setattr(storage, "_coldstore_log", lambda *a: log.append(a))
    monkeypatch.setattr(km, "_supa_select", select)
    monkeypatch.setattr(km.urllib.request, "urlopen", delete)
    monkeypatch.setattr(km, "DELETED_LOG", str(tmp_path / "deleted.jsonl"))
    return rows, log, tmp_path / "deleted.jsonl"


def test_same_fact_twice_is_stored_once(table):
    rows, log, _ = table
    first = storage.store("The dispatcher listens on port 8080.")
    assert storage.store("The dispatcher listens on port 8080.") == first
    assert list(rows) == [first]
    assert [a[1] for a in log] == ["allow", "allow", "knn-dedup"]


def test_near_duplicate_supersedes_the_old_fact(table):
    rows, log, deleted = table
    old = storage.store("The dispatcher listens on port 8080.")
    new = storage.store("The dispatcher listens on port 8081.")
    assert new != old and list(rows) == [new]
    assert json.loads(deleted.read_text())["row"]["id"] == old  # old row backed up before delete
    assert log[-1][1] == "knn-dedup" and f"old={old}" in log[-1][2]


def test_distinct_fact_is_kept(table):
    rows, _, _ = table
    storage.store("The dispatcher listens on port 8080.")
    storage.store("edge-host runs nginx for the dashboard.")
    assert len(rows) == 2


def test_new_ids_are_full_uuid_hex(table):
    rows, _, _ = table
    fid = storage.store("The dispatcher listens on port 8080.")
    assert len(fid) == 32 and int(fid, 16) >= 0


def test_gate_fails_open(table, monkeypatch):
    rows, _, _ = table
    monkeypatch.setattr(storage, "_supa_post", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    storage.store("The dispatcher listens on port 8080.")
    assert len(rows) == 1


def test_long_near_duplicate_digest_is_kept(table, monkeypatch):
    # Parallel subagent digests ("rows n=1..30" vs "n=121..150") sit at cosine 0.93-0.99 yet are distinct.
    rows, _, _ = table
    a, b = "Session: rows n=1..30 " + "x" * 300, "Session: rows n=121..150 " + "x" * 300
    VEC.update({a: [1.0, 0.0], b: [0.99, 0.141]})
    storage.store(a)
    storage.store(b)
    assert len(rows) == 2


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setenv("PROV_SUPERSEDE_GUARD", "true")


OLD, NEW = "The dispatcher listens on port 8080.", "The dispatcher listens on port 8081."


def test_extracted_near_dup_does_not_delete_explicit(table, guard):
    rows, log, deleted = table
    old = storage.store(OLD, source="memory_tool")
    new = storage.store(NEW, tags=["session-heartbeat"], source="20261012_093015_ab12cd34")
    assert set(rows) == {old, new} and not deleted.exists()
    assert log[-1][1:3] == ("knn-dedup", f"supersede-refused:old={old}:rank")


def test_explicit_still_supersedes_explicit(table, guard):
    rows, _, _ = table
    storage.store(OLD, source="memory_tool")
    new = storage.store(NEW, source="memory_tool")
    assert list(rows) == [new]


def test_extracted_supersedes_extracted_and_unknown(table, guard):
    rows, _, _ = table
    storage.store(OLD)  # unknown
    new = storage.store(NEW, source="session-distill")
    assert list(rows) == [new]


def test_flag_off_extracted_still_supersedes_explicit(table, monkeypatch):
    monkeypatch.delenv("PROV_SUPERSEDE_GUARD", raising=False)  # the live gateway env exports it
    rows, _, _ = table
    storage.store(OLD, source="memory_tool")
    new = storage.store(NEW, source="session-distill")
    assert list(rows) == [new]


def test_caller_cannot_self_label(table):
    rows, _, _ = table
    fid = storage.store(OLD, tags=["prov:explicit", "keep"])
    assert rows[fid]["tags"] == ["keep"]


def test_provenance_kwarg_stamps_tag(table):
    rows, _, _ = table
    fid = storage.store(OLD, tags=["prov:explicit", "keep"], provenance="extracted")
    assert rows[fid]["tags"] == ["keep", "prov:extracted"]


def test_invalid_provenance_is_rejected(table):
    with pytest.raises(ValueError):
        storage.store(OLD, provenance="bogus")


def test_guard_read_failure_falls_back_to_supersede(table, guard, monkeypatch):
    rows, _, _ = table
    storage.store(OLD, source="memory_tool")
    real = storage._supa_select
    monkeypatch.setattr(storage, "_supa_select",
                        lambda q: (_ for _ in ()).throw(OSError("x")) if "tags,source,text" in q else real(q))
    new = storage.store(NEW, source="session-distill")
    assert list(rows) == [new]
