"""Who vouches for a knowledge row: extracted | digest | explicit | imported | unknown.

Derived at read time from (source, tags, text shape), so legacy rows need no migration. A `prov:<label>`
tag wins when present (store() strips caller-supplied ones). `explicit` means an agent/human called a write
tool, not that the user said it. Design: reports/provenance-design-20261019.md section 5.
"""
import re

from knowledge.storage import _as_tags

LABELS = ('extracted', 'digest', 'explicit', 'imported', 'unknown')
RANK = {'explicit': 2, 'imported': 2, 'extracted': 1, 'digest': 1, 'unknown': 0}

_SESSION_ID = re.compile(r'\d{8}_\d{6}_[0-9a-f]{8}')
_DIGEST_SHAPE = re.compile(r'Session: .+ \| \d{4}-\d{2}-\d{2}')
_IMPORT_SOURCES = ('memory-offload', 'memory-compaction', 'backfill', 'manual-enrichment', 'e2e-test')


def provenance(row):
    """Label for a row/hit dict with optional tags, source, text (rules R1-R7)."""
    tags, source, text = _as_tags(row.get('tags')), row.get('source') or '', row.get('text') or ''
    for t in tags:
        if t.startswith('prov:') and t[5:] in LABELS:
            return t[5:]
    if source == 'session-distill' or any(t.startswith('session-digest') for t in tags) \
            or _DIGEST_SHAPE.match(text):
        return 'digest'  # R1
    if 'session-heartbeat' in tags or _SESSION_ID.fullmatch(source):
        return 'extracted'  # R2
    if source == 'memory_tool':
        return 'explicit'  # R3
    if source.startswith(_IMPORT_SOURCES) or source.endswith('-webhook'):
        return 'imported'  # R4
    return 'unknown'  # R5-R7 (restore rows stay unknown until recovered from deleted_facts.jsonl)


def rank(row):
    return RANK[provenance(row)]
