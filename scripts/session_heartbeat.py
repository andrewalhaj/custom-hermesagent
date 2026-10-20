#!/usr/bin/env python3
"""Mid-session fact capture (cron, 15 min): new assistant turns in active sessions -> facts -> Supabase.

Watermark {session_id: last message id} only moves past messages whose facts were all handled.
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

# ── Paths ──────────────────────────────────────────────────────────────
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
STATE_DB = os.path.join(HERMES_HOME, "state.db")
SCRIPTS_DIR = os.path.join(HERMES_HOME, "scripts")
WATERMARK_FILE = os.path.join(HERMES_HOME, "references", "session-heartbeat-watermark.json")
AUDIT_LOG = os.path.join(HERMES_HOME, "references", "session-heartbeat-audit.md")

sys.path.insert(0, SCRIPTS_DIR)
from session_state import connect_ro, retrieval_flags_off, save_json

# Dedicated process: forcing the slow retrieval flags off here affects nothing else.
retrieval_flags_off()
from knowledge import store, search
from session_capture import extract_facts
from junk_filter import MIN_FACT_LENGTH

# ── Config ─────────────────────────────────────────────────────────────
SIMILARITY_THRESHOLD = 0.92
MAX_FACTS_PER_RUN = 20       # cap to avoid flooding Supabase
MAX_MESSAGES_PER_SESSION = 50  # don't process ancient sessions in one go
MAX_FACTS_PER_SESSION = 15

# ── Watermark ──────────────────────────────────────────────────────────

def load_watermark() -> dict:
    """Return {session_id: last_processed_message_id}."""
    try:
        with open(WATERMARK_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_watermark(wm: dict) -> None:
    save_json(WATERMARK_FILE, wm)


# ── Audit ──────────────────────────────────────────────────────────────

def audit(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"- {ts}: {msg}"
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Core ───────────────────────────────────────────────────────────────

def get_active_sessions(db: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return [(session_id, source), ...] for sessions still in progress."""
    rows = db.execute(
        "SELECT id, source FROM sessions "
        "WHERE ended_at IS NULL AND source NOT IN ('subagent', 'batch') "
        "ORDER BY started_at DESC"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def get_new_messages(
    db: sqlite3.Connection,
    session_id: str,
    after_id: int,
) -> list[dict]:
    """Return assistant messages since after_id, capped."""
    rows = db.execute(
        "SELECT id, role, content FROM messages "
        "WHERE session_id = ? AND id > ? AND role = 'assistant' AND length(content) > 50 "
        "ORDER BY id ASC LIMIT ?",
        (session_id, after_id, MAX_MESSAGES_PER_SESSION),
    ).fetchall()
    return [{"id": r[0], "role": r[1], "content": r[2]} for r in rows]


def is_duplicate(text: str) -> bool:
    """Check semantic dedup against Supabase."""
    try:
        existing = search(text, top_k=3)
        if not existing:
            return False
        best = existing[0].get("score", 0)
        return best > SIMILARITY_THRESHOLD
    except Exception:
        return False  # if Supabase is down, store anyway


def process_session(
    db: sqlite3.Connection,
    session_id: str,
    source: str,
    watermark: dict,
) -> tuple[int, int, int]:
    """Extract facts from new messages in this session. Returns (stored, skipped, last_id).

    last_id only moves past messages whose facts were all handled, so an
    embedder/Supabase outage or the per-run cap defers facts instead of dropping them.
    """
    last_id = watermark.get(session_id, 0)
    stored = skipped = 0

    for m in get_new_messages(db, session_id, last_id):
        facts = [(f, t) for f, t in extract_facts(m.get("content") or "")
                 if len(f) >= MIN_FACT_LENGTH]
        # is_duplicate() → search() costs ~2-5s/fact; stop at the cap and resume
        # from this message next run. A lone oversized message still goes through.
        if stored + skipped and stored + skipped + len(facts) > MAX_FACTS_PER_SESSION:
            break
        for fact, tag in facts:
            if is_duplicate(fact):
                skipped += 1
                continue
            try:
                store(
                    fact,
                    tags=[tag, "session-heartbeat", source],
                    priority="normal",
                    source=session_id,
                    provenance="extracted",
                )
                stored += 1
            except Exception as exc:
                # Matched on the message, not ValueError: JSONDecodeError from a
                # sick backend is a ValueError too and must be retried.
                if "[cold-store-guard]" in str(exc):
                    skipped += 1  # guard rejection is permanent, don't retry
                    continue
                # Store is down: keep last_id before this message so it is retried.
                # Facts stored earlier in it are caught by the dedup on retry.
                audit(f"store failed in {session_id} at message {m['id']}, will retry: {exc}")
                return stored, skipped, last_id
        last_id = m["id"]

    return stored, skipped, last_id


# ── Main ───────────────────────────────────────────────────────────────

def main() -> None:
    wm = load_watermark()
    db = connect_ro(STATE_DB)

    # Wall-clock budget: cron kills us at 120s. Bail early with watermark saved
    # rather than being killed mid-run with progress lost (root cause of the
    # Jul 6–8 failure loop: timeout → watermark never saved → same backlog
    # re-processed forever).
    RUN_BUDGET_SECONDS = 90
    start = time.monotonic()

    try:
        sessions = get_active_sessions(db)
    except Exception as exc:
        audit(f"ERROR reading sessions: {exc}")
        db.close()
        return

    if not sessions:
        db.close()
        return  # silent — nothing active

    total_stored = total_skipped = total_sessions = 0
    budget_hit = False

    for session_id, source in sessions:
        if time.monotonic() - start > RUN_BUDGET_SECONDS:
            budget_hit = True
            break
        try:
            stored, skipped, last_id = process_session(db, session_id, source, wm)
        except Exception as exc:
            audit(f"ERROR session {session_id}: {exc}")
            continue

        # A batch that yields 0 facts still advances (watermark-stall bug);
        # process_session holds last_id only where a store failed.
        wm[session_id] = last_id
        save_watermark(wm)  # persist per-session: progress survives a timeout
        if stored or skipped:
            total_stored += stored
            total_skipped += skipped
            total_sessions += 1

        # Cap total facts per run
        if total_stored + total_skipped >= MAX_FACTS_PER_RUN:
            break

    db.close()

    if total_stored or total_skipped:
        audit(
            f"processed {total_sessions} session(s): "
            f"{total_stored} stored, {total_skipped} skipped"
            + (" [budget hit — remainder deferred to next run]" if budget_hit else "")
        )


if __name__ == "__main__":
    main()
