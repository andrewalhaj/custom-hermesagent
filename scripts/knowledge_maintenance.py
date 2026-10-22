#!/usr/bin/env python3
"""Hourly knowledge store maintenance watchdog.

Runs every hour. Silent when nothing changed (empty stdout = no Telegram delivery).
Reports when dedup or sector_path backfill actually runs.

Checks:
  1. High-water mark — any new facts since last run?
  2. Exact-text dedup — delete younger copies of duplicated facts
     (every delete is logged as the full row to deleted_facts.jsonl first)
  3. Sector_path backfill — classify unlabeled facts via classifier

State: ~/.hermes/knowledge_db/maintenance_state.json
  {"last_run_ts": "<ISO>", "last_fact_count": N}
"""
import os, re, sys, json, time
import urllib.request
from datetime import datetime, timezone
from collections import defaultdict

# ── Paths ───────────────────────────────────────────────────────────────
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

DB_DIR    = os.path.expanduser('~/.hermes/knowledge_db')
STATE_PATH = os.path.join(DB_DIR, 'maintenance_state.json')
DELETED_LOG = os.path.join(DB_DIR, 'deleted_facts.jsonl')
os.makedirs(DB_DIR, exist_ok=True)

from knowledge.supabase_client import _supa_all_rows, _supa_select, get_supabase

# Webhook bug appended similarity context: "... Related knowledge: [high] Fact | [high] Fact".
# Must match the exact pipe-delimited bracketed format to avoid false positives.
_CORRUPT_PAT = re.compile(
    r'\.\s+Related knowledge:\s+\[(?:high|normal|low|critical)\]', re.IGNORECASE)

def _norm(text):
    return (text or '').strip().lower()

def dedup_ids(facts):
    """Ids of younger exact-text copies. The oldest copy's id is never returned."""
    groups = defaultdict(list)
    for f in facts:
        if _norm(f['text']):
            groups[_norm(f['text'])].append(f)
    out = []
    for group in groups.values():
        keep = min(group, key=lambda x: x.get('stored_at') or '')['id']
        out.extend(sorted({f['id'] for f in group} - {keep}))
    return out

def corrupt_ids(facts):
    """Corrupted facts whose clean text is already stored as another fact."""
    texts = {_norm(f['text']) for f in facts}
    out = []
    for f in facts:
        m = _CORRUPT_PAT.search(f['text'] or '')
        if m and len(f['text']) > 400 and _norm(f['text'][:m.start() + 1]) in texts:
            out.append(f['id'])
    return out

def delete_fact(url, key, fact_id, reason):
    """Append the full row to DELETED_LOG, then delete. False if the row is already gone."""
    rows = _supa_select(f'id=eq.{fact_id}&select=*')
    if not rows:
        return False
    with open(DELETED_LOG, 'a') as f:
        f.write(json.dumps({'ts': datetime.now(timezone.utc).isoformat(),
                            'reason': reason, 'row': rows[0]}, default=str) + '\n')
    req = urllib.request.Request(
        f'{url}/rest/v1/knowledge?id=eq.{fact_id}', method='DELETE')
    req.add_header('apikey', key)
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Prefer', 'return=minimal')
    urllib.request.urlopen(req, timeout=10)
    return True

# ── State helpers ────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"last_run_ts": None, "last_fact_count": 0}

def save_state(state):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)

# ── Main ─────────────────────────────────────────────────────────────────
def main():
    state = load_state()
    last_count = state.get('last_fact_count', 0)

    # Fetch all facts
    try:
        facts = _supa_all_rows(select='id,text,stored_at,sector_path', page=500)
    except Exception as e:
        print(f"❌ knowledge_maintenance: fetch failed: {e}")
        sys.exit(1)

    current_count = len(facts)

    # High-water mark check — silent exit if nothing new
    if current_count <= last_count:
        # Still run dedup in case duplicates were added that replaced deletions
        # but only if count is exactly the same (could have added + deleted)
        if current_count == last_count:
            save_state({"last_run_ts": datetime.now(timezone.utc).isoformat(),
                        "last_fact_count": current_count})
            sys.exit(0)  # silent — nothing to do

    report = []
    url, key = get_supabase()

    # ── Step 1: Exact-text dedup ─────────────────────────────────────────
    to_delete = dedup_ids(facts)
    deleted = 0
    if to_delete:
        for fact_id in to_delete:
            try:
                deleted += delete_fact(url, key, fact_id, 'dedup')
            except Exception as e:
                report.append(f"  dedup FAIL {fact_id[:8]}: {e}")

        if deleted:
            report.append(f"🧹 Dedup: removed {deleted} duplicates "
                         f"({current_count} → {current_count - deleted} facts)")

        # Refresh facts list after deletion
        try:
            facts = _supa_all_rows(select='id,text,stored_at,sector_path', page=500)
        except Exception:
            pass

    # ── Step 2: Sector_path backfill ─────────────────────────────────────
    unlabeled = [f for f in facts if not f.get('sector_path')]
    classified = 0
    abstained = 0

    if unlabeled:
        try:
            from knowledge.storage import _classify_sector  # Jev tree walk by default, centroid fallback
            paths = [_classify_sector((f['text'] or '')[:1500]) for f in unlabeled]

            updates = [(f['id'], p) for f, p in zip(unlabeled, paths) if p]
            abstained = sum(1 for p in paths if not p)

            for fact_id, path in updates:
                payload = json.dumps({'sector_path': path}).encode()
                req = urllib.request.Request(
                    f'{url}/rest/v1/knowledge?id=eq.{fact_id}',
                    data=payload, method='PATCH')
                req.add_header('apikey', key)
                req.add_header('Authorization', f'Bearer {key}')
                req.add_header('Content-Type', 'application/json')
                req.add_header('Prefer', 'return=minimal')
                try:
                    urllib.request.urlopen(req, timeout=10)
                    classified += 1
                except Exception as e:
                    report.append(f"  backfill FAIL {fact_id[:8]}: {e}")

            if classified or abstained:
                report.append(f"🏷  Backfill: {classified} classified, "
                             f"{abstained} abstained "
                             f"(of {len(unlabeled)} unlabeled)")
        except Exception as e:
            report.append(f"⚠️  Backfill skipped: {e}")

    # ── Step 3: Corrupted fact check ──────────────────────────────────────
    # Only when the clean text survives as another fact; otherwise it's the only copy.
    for fact_id in corrupt_ids(facts):
        try:
            if delete_fact(url, key, fact_id, 'corrupt'):
                report.append(f"🗑  Deleted corrupted fact {fact_id[:8]}")
        except Exception as e:
            report.append(f"  corrupt delete FAIL {fact_id[:8]}: {e}")

    # ── Save state & report ───────────────────────────────────────────────
    final_count = current_count - deleted
    save_state({
        "last_run_ts": datetime.now(timezone.utc).isoformat(),
        "last_fact_count": final_count
    })

    if report:
        new_facts = current_count - last_count
        print(f"📚 Knowledge maintenance ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)")
        print(f"   Store: {last_count} → {final_count} facts (+{new_facts} new)")
        print()
        print('\n'.join(report))
    # else: silent — nothing worth reporting

if __name__ == '__main__':
    main()
