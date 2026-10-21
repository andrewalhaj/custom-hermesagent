#!/usr/bin/env python3
"""Distill sessions into one digest each -> Supabase (cron, 4h). Silent unless it distilled something.

Ended sessions: watermark on ended_at, saved per session, held below the first failure so it retries.
Live telegram/gateway sessions: re-distilled at most every ACTIVE_REDISTILL_HOURS.
"""

import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

from session_state import connect_ro, retrieval_flags_off, save_json

# ── Paths ──────────────────────────────────────────────────────────────
STATE_DB = os.path.expanduser("~/.hermes/state.db")
WATERMARK_FILE = os.path.expanduser(
    "~/.hermes/references/session-distill-watermark.json"
)

# ── Constants ──────────────────────────────────────────────────────────
MIN_UA_MESSAGES = 20       # minimum user+assistant messages to qualify
MAX_ENDED_PER_RUN = 25     # backlog drains over several runs instead of hitting the 600s cron timeout
MAX_DIGEST_CHARS = 2000    # hard cap on digest length
MAX_WATERMARK_IDS = 500    # keep the processed_session_ids list trimmed
FIRST_RUN_LOOKBACK_DAYS = 7
TAGS = "session-digest,compaction-flush"
_BARE_OUTCOMES = {"done", "error", "pass", "passed", "fail", "failed", "success",
                  "complete", "completed", "finish", "finished"}
PRIORITY = "normal"  # regex digests are heuristic; not "high"
ACTIVE_SESSION_MIN_MESSAGES = 50
ACTIVE_REDISTILL_HOURS = 3

# ── Helpers ────────────────────────────────────────────────────────────


def _ua_count(conn, sid):
    return conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role IN ('user', 'assistant')",
        (sid,),
    ).fetchone()[0]


def load_watermark():
    """Load watermark from JSON file. Returns (last_processed_ts, ids, active_last_distilled).

    ids is an insertion-ordered dict so trimming in save_watermark drops the oldest ids,
    never ones at the current watermark timestamp.
    """
    if not os.path.exists(WATERMARK_FILE):
        return None, {}, {}
    try:
        with open(WATERMARK_FILE) as f:
            data = json.load(f)
        ts = data.get("last_processed_ts")
        ids = dict.fromkeys(data.get("processed_session_ids", []))
        active_last_distilled = data.get("active_last_distilled", {})
        return ts, ids, active_last_distilled
    except (json.JSONDecodeError, KeyError, ValueError):
        return None, {}, {}


def save_watermark(last_processed_ts, processed_ids, active_last_distilled):
    """Save watermark to JSON file. Trims processed_ids to last MAX_WATERMARK_IDS."""
    ids_list = list(processed_ids)[-MAX_WATERMARK_IDS:]
    data = {
        "last_processed_ts": last_processed_ts,
        "processed_session_ids": ids_list,
        "active_last_distilled": active_last_distilled,
    }
    save_json(WATERMARK_FILE, data)
    return ids_list


def get_qualifying_sessions(since_ts, processed_ids):
    """Return sessions that qualify for distillation, ordered by ended_at asc.

    Criteria:
      - ended (any end_reason: reset, agent/cli close, orphan reap)
      - id does NOT start with 'cron_'
      - ≥20 user+assistant messages in the session
      - ended_at >= since_ts (if since_ts is set); >= so a group sharing the
        watermark timestamp that a cap or failure split is still picked up
      - id NOT in processed_ids
    """
    conn = connect_ro(STATE_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT s.id, s.source, s.title, s.ended_at, s.message_count
        FROM sessions s
        WHERE s.ended_at IS NOT NULL
          AND s.id NOT LIKE 'cron_%'
          AND (? IS NULL OR s.ended_at >= ?)
        ORDER BY s.ended_at ASC
        """,
        (since_ts, since_ts),
    )
    candidates = list(cur.fetchall())
    qualifying = []
    for row in candidates:
        sid = row["id"]
        if sid in processed_ids:
            continue
        if _ua_count(conn, sid) >= MIN_UA_MESSAGES:
            qualifying.append(dict(row))
            if len(qualifying) >= MAX_ENDED_PER_RUN:
                break
    conn.close()
    return qualifying


def get_active_qualifying_sessions(active_last_distilled):
    """Return active (ongoing) sessions qualifying for distillation.

    Criteria:
      - ended_at IS NULL
      - source contains 'telegram' or 'gateway'
      - id does NOT start with 'cron_'
      - >= ACTIVE_SESSION_MIN_MESSAGES user+assistant messages
      - not distilled within the last ACTIVE_REDISTILL_HOURS
    """
    conn = connect_ro(STATE_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT s.id, s.source, s.title, s.ended_at, s.message_count
        FROM sessions s
        WHERE s.ended_at IS NULL
          AND (LOWER(s.source) LIKE '%telegram%' OR LOWER(s.source) LIKE '%gateway%')
          AND s.id NOT LIKE 'cron_%'
        ORDER BY s.id ASC
        """
    )
    candidates = list(cur.fetchall())
    qualifying = []
    for row in candidates:
        sid = row["id"]
        last_distilled = active_last_distilled.get(sid, 0)
        if last_distilled > time.time() - ACTIVE_REDISTILL_HOURS * 3600:
            continue
        if _ua_count(conn, sid) >= ACTIVE_SESSION_MIN_MESSAGES:
            d = dict(row)
            d["is_active"] = True
            qualifying.append(d)
    conn.close()
    return qualifying


def get_session_messages(session_id):
    """Return all user and assistant messages for a session, ordered by id."""
    conn = connect_ro(STATE_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT id, role, content
        FROM messages
        WHERE session_id = ? AND role IN ('user', 'assistant')
        ORDER BY id ASC
        """,
        (session_id,),
    )
    messages = [dict(r) for r in cur.fetchall()]
    conn.close()
    return messages


def distill_session(session, messages):
    """Extract a curated digest from session messages.

    Returns a plain-text digest string, or None if not substantive enough.
    """
    is_active = session.get("is_active", False)
    title = (session.get("title") or "").strip()
    ended_at = session.get("ended_at")
    if ended_at:
        date_str = datetime.fromtimestamp(ended_at, tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )
    else:
        date_str = "unknown-date"

    # Derive title from first user message if missing
    if not title:
        for m in messages:
            if m["role"] == "user":
                title = m["content"].strip()[:80]
                break
    if not title:
        title = "untitled"

    if is_active:
        title = f"Live: {title}"

    # Collect assistant content for extraction
    assistant_texts = [m["content"] for m in messages if m["role"] == "assistant"]
    all_assistant = "\n".join(assistant_texts)
    user_texts = [m["content"] for m in messages if m["role"] == "user"]
    all_user = "\n".join(user_texts)

    # ── Decisions: scan assistant turns for decision verbs ──
    # Strategy: find the sentence containing the keyword, not just the keyword.
    # Split on sentence boundaries, then filter for sentences containing decision signals.
    DECISION_SIGNALS = re.compile(
        r"(?i)\b(decided|verdict|greenlit|green-?lit|approved|proceeding|going ahead"
        r"|will (?:now )?(?:proceed|implement|build|deploy|install|create|add|update"
        r"|fix|patch|rewrite|refactor|migrate|remove|delete|configure|set up|switch)"
        r"|I(?:'ll| will) (?:build|implement|deploy|install|create|add|update|fix"
        r"|patch|rewrite|refactor|migrate|configure)"
        r"|verdict is|verdict:|SKIP|not installing|ruled out)\b"
    )
    decisions = set()
    for text in assistant_texts:
        # Split into sentences on . ! ? or newlines; keep delimiters via lookahead
        sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if DECISION_SIGNALS.search(sent):
                # Take the sentence, capped at 160 chars
                decisions.add(sent[:160])

    # Also capture key user directives
    user_directives = set()
    for pat in [
        r"(?i)\b(?:please|can you|I need|I want|build|deploy|install|create|add|review|check|fix|update|migrate|configure)\s+(.+?)(?:[.;\n]|$)",
    ]:
        for m in re.finditer(pat, all_user):
            user_directives.add(m.group(0).strip()[:120])

    # ── Outcomes: tool results, files written, systems changed ──
    outcome_patterns = [
        r"(?i)\b(?:wrote|created|saved|written|built|deployed|installed|configured|patched|fixed|updated|migrated|refactored|rewrote|archived|removed|deleted)\s+(.+?)(?:[.;\n]|$)",
        r"(?i)\b(?:file|script|config|binary|endpoint|service|container|database|table|index)\s+(?:is|was|now|has been)\s+(.+?)(?:[.;\n]|$)",
        r"(?i)\b(?:verdict|verdict is|result|outcome)\s*:\s*(.+?)(?:[.;\n]|$)",
        r"(?i)\b(?:pass(?:ed)?|fail(?:ed)?|success|error|completed?|finished?|done)\b",
        r"(?i)\b(?:test(?:s)?\s+(?:pass|fail|green|red|succeed))\b",
    ]
    outcomes = set()
    for pat in outcome_patterns:
        for m in re.finditer(pat, all_assistant):
            o = m.group(0).strip()[:150]
            if " " not in o and (len(o) < 4 or o.lower() in _BARE_OUTCOMES):
                continue  # bare status word carries no information
            outcomes.add(o)

    # ── Skipped: things ruled out ──
    skip_patterns = [
        r"(?i)\b(?:skip(?:ping)?|SKIP|not installing|not building|ruled out|rejected|won't|will not|not going to|declined|passed on)\s+(.+?)(?:[.;\n]|$)",
    ]
    skipped = set()
    for pat in skip_patterns:
        for m in re.finditer(pat, all_assistant):
            skipped.add(m.group(0).strip()[:120])

    # ── Context: session arc summary ──
    # First user message as the opener, last assistant message conclusion hint
    opener = user_texts[0][:200].strip() if user_texts else ""
    closer = ""
    for m in reversed(assistant_texts):
        stripped = m.strip()
        if stripped:
            closer = stripped[:200]
            break

    if not (decisions or outcomes or skipped or user_directives) and len(opener) <= 10:
        return None

    # ── Build digest ──
    parts = []
    parts.append(f"Session: {title} | {date_str}")

    if decisions:
        parts.append(f"Decisions: {', '.join(sorted(decisions)[:8])}")
    if outcomes:
        parts.append(f"Outcomes: {', '.join(sorted(outcomes)[:6])}")
    if skipped:
        parts.append(f"Skipped: {', '.join(sorted(skipped)[:4])}")
    if user_directives:
        parts.append(f"Directives: {', '.join(sorted(user_directives)[:4])}")

    # Context summary
    context = f"Context: Session started with user asking to {opener[:120]}"
    if closer and closer != opener[:200]:
        context += f". Concluded with: {closer[:120]}"
    parts.append(context)

    digest = "\n".join(parts)

    if len(digest) > MAX_DIGEST_CHARS:
        digest = digest[: MAX_DIGEST_CHARS - 3] + "..."
    return digest


def _supersede_previous(digest, tags_list, new_id):
    """Best-effort: delete this session's earlier digests (tagged, or untagged 'Live:' rows by exact title)."""
    sid = next((t[8:] for t in tags_list if t.startswith("session:")), None)
    if not sid or not new_id:
        return
    try:
        from urllib.parse import quote
        from knowledge import _supa_select, get_supabase
        from knowledge_maintenance import delete_fact
        title = digest.split("\n", 1)[0].rsplit(" | ", 1)[0][len("Session: "):]
        # shortcut: old Live rows carry no tag, so same-titled sessions share them; moot once they age out
        live = f"Session: Live: {title.removeprefix('Live: ')} | unknown-date"
        rows = _supa_select("select=id&or=" + quote(f'(tags.cs.["session:{sid}"],text.like.{live}*)'))
        for r in rows:
            if r["id"] != new_id:
                delete_fact(*get_supabase(), r["id"], reason=f"superseded-by:{new_id}")
    except Exception as e:
        print(f"[session_distill] supersede failed: {e}", file=sys.stderr)


def store_digest(digest, tags=TAGS):
    """Store a digest in Supabase via the knowledge package. Returns True on success."""
    retrieval_flags_off()  # keep within the cron time budget
    try:
        from knowledge import store as _kb_store  # lazy: the session queries don't need it
        tags_list = [t.strip() for t in tags.split(",") if t.strip()]
        new_id = _kb_store(digest, tags=tags_list, priority=PRIORITY, source="session-distill", provenance="digest")
        _supersede_previous(digest, tags_list, new_id)
        return True
    except ValueError as e:
        # The cold-store guard's rejection is final; retrying would block the watermark forever.
        print(f"[session_distill] knowledge store error: {e}", file=sys.stderr)
        return "[cold-store-guard]" in str(e)
    except Exception as e:
        print(f"[session_distill] knowledge store error: {e}", file=sys.stderr)
        return False


def main():
    watermark_ts, processed_ids, active_last_distilled = load_watermark()

    # First run: watermark absent → look back 7 days only
    if watermark_ts is None:
        watermark_ts = (
            datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)
        ).timestamp()

    sessions = get_qualifying_sessions(watermark_ts, processed_ids)
    active_sessions = get_active_qualifying_sessions(active_last_distilled)

    if not sessions and not active_sessions:
        # Silent exit — nothing to do
        return 0

    distilled = 0
    failed = False
    max_ts = watermark_ts
    new_ids = processed_ids.copy()

    for session in sessions:
        sid = session["id"]
        digest = distill_session(session, get_session_messages(sid))
        # None = not substantive enough; still marked processed
        if digest is not None and not store_digest(digest, tags=f"{TAGS},session:{sid}"):
            # Stop here so the watermark stays below this session and it retries next run
            print(
                f"[session_distill] Failed to store digest for session {sid}",
                file=sys.stderr,
            )
            failed = True
            break
        if digest is not None:
            distilled += 1
        new_ids[sid] = None
        max_ts = max(max_ts, session["ended_at"])
        # Saved per session so a cron timeout keeps the work already done
        save_watermark(max_ts, new_ids, active_last_distilled)

    for session in active_sessions:
        sid = session["id"]
        messages = get_session_messages(sid)
        if not messages:
            continue

        digest = distill_session(session, messages)
        if digest is None:
            continue

        if store_digest(digest, tags=f"session-digest,active-session,session:{sid}"):
            distilled += 1
            active_last_distilled[sid] = time.time()
        else:
            print(
                f"[session_distill] Failed to store digest for active session {sid}",
                file=sys.stderr,
            )

    if active_sessions:
        save_watermark(max_ts, new_ids, active_last_distilled)

    if distilled > 0:
        print(f"Distilled {distilled} sessions → Supabase")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
