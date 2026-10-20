#!/usr/bin/env python3
"""CLI over the knowledge package (Supabase pgvector). `import knowledge` gets the package,
not this file: a package dir wins over a same-named module, so this is only reached as a script.
search tries the warm kb_daemon first (~0.1s) and falls back in-process on any daemon failure.
"""
import os
import sys

import knowledge as kb

USAGE = """Usage: knowledge.py <command> [args]
  status | recent [N] | search <query> | store <text>
  eval: use kbench.py eval
Env: KNOWLEDGE_TAGS=a,b  KNOWLEDGE_PRIORITY=high  KNOWLEDGE_TOP_K=5  KB_NO_DAEMON=1"""


def _prefix(h):
    return f" [{h['context_prefix']}]" if h.get('context_prefix') else ''


def search(args):
    query, top_k = ' '.join(args), int(os.environ.get('KNOWLEDGE_TOP_K', '5'))
    hits = None
    if os.environ.get('KB_NO_DAEMON') != '1':
        try:
            from kb_client import daemon_search
            hits = daemon_search(query, top_k=top_k)
        except Exception:
            hits = None
    if hits is None:
        hits = kb.search(query, top_k=top_k)
    for h in hits:
        print(f"[{h['score']}] [{h['priority']}]{_prefix(h)} {h['text'][:200]}")
        print(f"  tags: {h['tags']}  id: {h['id']}\n")


def store(args):
    tags = [t.strip() for t in os.environ.get('KNOWLEDGE_TAGS', '').split(',') if t.strip()]
    priority = os.environ.get('KNOWLEDGE_PRIORITY', 'normal')
    print(f"Stored: {kb.store(' '.join(args), tags=tags, priority=priority, source='cli', provenance='explicit')}")


def recent(args):
    for r in kb.recent(int(args[0]) if args else 10):
        print(f"[{r['priority']}]{_prefix(r)} {r['id']}: {r['text']}")


COMMANDS = {
    'status': lambda a: kb.status(),
    'recent': recent,
    'search': search,
    'store': store,
}
NEEDS_ARG = {'search', 'store'}

if __name__ == '__main__':
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else 'status'), sys.argv[2:]
    if cmd not in COMMANDS or (cmd in NEEDS_ARG and not args):
        print(USAGE, file=sys.stderr)
        sys.exit(1)
    COMMANDS[cmd](args)
