"""Hermes Knowledge Store — Supabase pgvector-backed semantic memory.

The public API is re-exported here so `import knowledge` and `from knowledge import search, ...`
work. The CLI entry point lives in scripts/knowledge.py.
"""
from knowledge.config import DB_DIR
from knowledge.supabase_client import (
    get_supabase, _supa_get, _supa_post, _supa_insert, _supa_select, _supa_all_rows,
)
from knowledge.embed import get_model, embed
from knowledge.chunker import chunk_overlap
from knowledge.storage import store, body_hash, _as_tags
from knowledge.search import search, _detect_query_sector, _extract_query_metadata
from knowledge.classifier import classify, classify_batch, explain
from knowledge.unified_search import unified_search
from knowledge.eval import eval_benchmark
from knowledge.stats import status, count_facts, recent

__all__ = [
    'DB_DIR', 'get_supabase', '_supa_get', '_supa_post', '_supa_insert', '_supa_select',
    '_supa_all_rows', 'get_model', 'embed', 'chunk_overlap', 'store', 'body_hash', '_as_tags',
    'search', '_detect_query_sector', '_extract_query_metadata', 'classify', 'classify_batch',
    'explain', 'unified_search', 'eval_benchmark', 'status', 'count_facts', 'recent',
]
