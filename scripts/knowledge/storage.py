"""Writes to the cold store: store(), the cold-store schema guard, the KNN
duplicate gate, and tag normalization."""
import os
import json
import uuid
import hashlib
from datetime import datetime, timezone

from knowledge.config import flag
from knowledge.embed import embed
from knowledge.supabase_client import _supa_insert, _supa_post, _supa_select, get_supabase
from secret_mask import mask_secrets

KNN_GATE_THRESHOLD = float(os.environ.get('KNN_GATE_THRESHOLD', '0.92'))
# Near-dup supersede only for short facts: at >=0.92 every short pair in a 150-row dry run was a rewording,
# but parallel session digests (same template, different rows) reach 0.93-0.99 while being distinct.
KNN_MAX_CHARS = 300


def _as_tags(val):
    """Normalize a row's tags field to a list. Supabase jsonb returns a list already,
    but tolerate legacy JSON-string values too."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []

def body_hash(chunk_body):
    """SHA-256 hex digest of the chunk body for cache-deduplication."""
    return hashlib.sha256(chunk_body.encode('utf-8')).hexdigest()[:16]

# ── Cold-store schema guard (HERMES-GUARD cold-store-validator) ─────────────
# Fail-closed validator: every write to the cold store passes through
# _coldstore_validate() before tbl.add(). Returns (ok, reason).
# On error inside the guard, rejects the write (fail closed).
# Audit log: ~/.hermes/references/cold-store-audit.log
# To disable: export HERMES_COLDSTORE_GUARD=off
import re as _re, datetime as _dt

_COLDSTORE_GUARD_ENABLED = (
    os.environ.get('HERMES_COLDSTORE_GUARD', 'on').lower()
    not in ('off', '0', 'false', 'no')
)
_COLDSTORE_AUDIT_LOG = os.path.expanduser(
    '~/.hermes/references/cold-store-audit.log'
)

# Narrative/affect/methodology self-referential genre — reject these.
# Tuned NARROW: first-person self-diagnosis and affect only, NOT descriptive prose.
# Legitimate session digests contain decisions, outcomes, code facts — those must ACCEPT.
_COLDSTORE_BANNED_RE = _re.compile(
    r'\bI (felt|feel|sensed|thought I|found myself|noticed myself|caught myself)\b'
    r'|\bI was (feeling|struggling|overwhelmed|panicking|spiral)\b'
    r'|\b(a pull|the pull|felt (safer|risky|exposed|like a|like the))\b'
    r'|\b(grind|grinding|grinded) (works|helped|through|approach)\b'
    r'|\b(trust my|my own methodology|my instinct|my instincts|my gut)\b'
    r'|\b(learned to|reinforced that|trained me|i now (know|believe|trust) that)\b'
    r'|\bself.diagnos\b'
    r'|\b(frantic|frantically|panic.spiral|momentum (carried|pulled|took))\b'
    r'|\bwild.?card (moment|move|play)\b',
    _re.IGNORECASE,
)

# Hard minimum: reject entries shorter than 10 chars (empty/placeholder writes)
_COLDSTORE_MIN_CHARS = 10


def _coldstore_log(entry_id, decision, reason, source=''):
    """Append one audit line. Silently no-ops on any I/O error."""
    try:
        os.makedirs(os.path.dirname(_COLDSTORE_AUDIT_LOG), exist_ok=True)
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
        with open(_COLDSTORE_AUDIT_LOG, 'a') as fh:
            fh.write(
                f"{ts} id={entry_id or '-'} source={source or '-'} "
                f"decision={decision} reason={reason}\n"
            )
    except Exception:
        pass


def _coldstore_validate(text, source=''):
    """
    Returns (ok: bool, reason: str).
    ok=True  → write allowed.
    ok=False → write rejected; reason is the human-readable cause.
    Fails closed: any internal exception → (False, 'guard-error:…').
    """
    if not _COLDSTORE_GUARD_ENABLED:
        return True, ''
    try:
        if not text or not str(text).strip():
            return False, 'empty-text'
        t = str(text).strip()
        if len(t) < _COLDSTORE_MIN_CHARS:
            return False, f'too-short:{len(t)}'
        m = _COLDSTORE_BANNED_RE.search(t)
        if m:
            return False, f'banned-narrative-affect:{m.group(0)!r}'
        return True, ''
    except Exception as exc:
        return False, f'guard-error:{exc}'

def _knn_gate(text, vector):
    """Return ('exact' | 'near' | None, dup_id). Fails open on any exception.

    exact: same body_hash. near (texts <= KNN_MAX_CHARS only): real cosine >= KNN_GATE_THRESHOLD against
    the RPC's top 3 — the RPC's `similarity` is an RRF rank score (1/(20+rank), max ~0.048), never a cosine.
    """
    try:
        same = _supa_select(f'select=id&limit=1&body_hash=eq.{body_hash(text)}')
        if same:
            return 'exact', same[0]['id']
        if len(text) > KNN_MAX_CHARS:
            return None, None
        hits = _supa_post('/rest/v1/rpc/match_knowledge_v4',
                          {'query_embedding': vector, 'query_text': text[:200], 'match_count': 3},
                          prefer='')
        ids = ','.join(json.dumps(r['id']) for r in hits or [])
        if not ids:
            return None, None
        best, best_id = 0.0, None
        for r in _supa_select(f'select=id,vector&id=in.({ids})'):
            v = r['vector']
            sim = _cosine(vector, json.loads(v) if isinstance(v, str) else v)
            if sim > best:
                best, best_id = sim, r['id']
        if best >= KNN_GATE_THRESHOLD:
            return 'near', best_id
    except Exception:
        pass
    return None, None


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = sum(x * x for x in a) ** 0.5, sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _classify_sector(text):
    """Return sector_path list for text, or [] on any failure."""
    if flag('KNOWLEDGE_SKIP_SECTOR_CLASSIFY'):
        return []
    try:
        from knowledge.classifier import classify
        return classify(text) or []
    except Exception:
        return []

def _supersede_refused(old_id, new_row):
    """PROV_SUPERSEDE_GUARD: a lower-ranked row (extracted/digest/unknown) must not delete a higher one."""
    if not flag('PROV_SUPERSEDE_GUARD'):
        return False
    try:
        from knowledge.provenance import rank
        old = _supa_select(f'select=id,tags,source,text&id=eq.{old_id}')
        return bool(old) and rank(new_row) < rank(old[0])
    except Exception:
        return False  # fails open to today's behaviour, like the gate

def store(text, tags=None, priority='normal', source='', provenance=None):
    """Store a fact in the knowledge DB.
    Passes through _coldstore_validate(); raises ValueError on rejection.
    """
    # Credentials never reach the store; masking keeps the rest of a fact or digest.
    text = mask_secrets(text)
    entry_id = uuid.uuid4().hex
    ok, reason = _coldstore_validate(text, source)
    _coldstore_log(entry_id, 'allow' if ok else 'reject', reason or 'ok', source)
    if not ok:
        raise ValueError(f'[cold-store-guard] write rejected: {reason}')

    tags = [t for t in tags or [] if not str(t).startswith('prov:')]  # the agent must not self-label
    if provenance:  # only the writer's own code passes this, so the stamp is trusted
        from knowledge.provenance import LABELS  # lazy: provenance imports this module
        if provenance not in LABELS:
            raise ValueError(f'unknown provenance label: {provenance!r}')
        tags.append(f'prov:{provenance}')
    vector = embed([text])[0]
    vector = vector.tolist() if hasattr(vector, 'tolist') else list(vector)
    kind, dup_id = _knn_gate(text, vector)
    if kind == 'exact':
        _coldstore_log(entry_id, 'knn-dedup', f'exact:dup={dup_id}', source)
        return dup_id

    row = {
        'id': entry_id,
        'text': text,
        'vector': vector,
        'tags': tags,
        'priority': priority,
        'source': source,
        'stored_at': datetime.now(tz=timezone.utc).isoformat(),
        'context_prefix': '',
        'body_hash': body_hash(text),
        'sector_path': _classify_sector(text) or None,
    }
    _supa_insert(row)
    if kind == 'near' and _supersede_refused(dup_id, row):
        _coldstore_log(entry_id, 'knn-dedup', f'supersede-refused:old={dup_id}:rank', source)
    elif kind == 'near':
        # The newer statement wins ("port is 8081" replaces "port is 8080"); the old row is backed up first.
        try:
            from knowledge_maintenance import delete_fact
            delete_fact(*get_supabase(), dup_id, reason=f'superseded-by:{entry_id}')
            _coldstore_log(entry_id, 'knn-dedup', f'supersede:cos>={KNN_GATE_THRESHOLD}:old={dup_id}', source)
        except Exception as e:
            _coldstore_log(entry_id, 'knn-dedup', f'supersede-failed:old={dup_id}:{e}', source)
    return row['id']
