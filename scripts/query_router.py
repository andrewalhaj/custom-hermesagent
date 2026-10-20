#!/usr/bin/env python3
"""Hermes Query Router — unified, tiered retrieval across all Hermes knowledge sources.

Tiers:
  instant   — Supabase/pgvector semantic search only (fastest, one embedding call)
  classic   — All sources: Supabase KB + MEMORY.md + USER.md + reference-doc grep
  agentic   — Classic search + suggested next reads (top knowledge source files)

Flags:
  --json    Machine-readable JSON output
  --help    Show usage

Sources:
  1. Supabase pgvector  (primary semantic store, hybrid vector+FTS)
  2. MEMORY.md          (~2.4KB hot store, §-separated entries)
  3. USER.md            (~1.9KB user-profile store)
  4. Reference docs     (~/.hermes/references/*.md, 28 files) — grep fallback

  (Honcho is queried separately by the agent via honcho_search — see note in --help)

READ-ONLY: This tool never writes to any knowledge store.
"""

import os
import sys
import json
import re
import subprocess
import warnings
from io import StringIO

# ── Suppress HuggingFace / sentence-transformers stderr noise ──────────────
warnings.filterwarnings("ignore")

# Redirect stderr during imports to suppress model-loading chatter
_real_stderr = sys.stderr
sys.stderr = StringIO()

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import knowledge
finally:
    # Restore stderr but capture any import-time noise
    _import_stderr = sys.stderr.getvalue()
    sys.stderr = _real_stderr

# ═══════════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════════

HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
MEMORY_PATH = os.path.join(HERMES_HOME, "memories", "MEMORY.md")
USER_PATH = os.path.join(HERMES_HOME, "memories", "USER.md")
REFERENCES_DIR = os.path.join(HERMES_HOME, "references")
CONFIDENT_SCORE = 0.80

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _suppress_stderr():
    """Context manager / decorator helper to squelch stderr during knowledge calls."""
    return StringIO()


def search_knowledge(query, top_k=8):
    """Run Supabase pgvector knowledge search, suppressing stderr noise. Returns list of dicts."""
    saved = sys.stderr
    sys.stderr = StringIO()
    try:
        return knowledge.search(query, top_k=top_k)
    finally:
        sys.stderr = saved


def scan_memory_entries(filepath):
    """Read §-separated entries from a memory file. Returns list of strings."""
    entries = []
    if not os.path.exists(filepath):
        return entries
    try:
        with open(filepath) as f:
            text = f.read()
    except Exception:
        return entries
    # Split on lines that are exactly '§'
    parts = re.split(r"\n§\n|\n§$|^§\n", text)
    for p in parts:
        p = p.strip()
        if p:
            entries.append(p)
    return entries


def _page_from_source(source):
    """Extract a display-friendly page name from a source path."""
    if not source:
        return "unknown"
    return os.path.basename(source).replace(".md", "")


# ═══════════════════════════════════════════════════════════════════════════
# Tier: instant
# ═══════════════════════════════════════════════════════════════════════════


def tier_instant(query, as_json=False):
    """Knowledge-store-only search, top_k=8."""
    hits = search_knowledge(query, top_k=8)

    if as_json:
        return json.dumps({"tier": "instant", "query": query, "results": hits}, indent=2)

    lines = [f"═══ QUERY ROUTER · instant · \"{query}\" ═══\n"]
    if not hits:
        lines.append("  (no results)")
        return "\n".join(lines)

    for h in hits:
        score = h["score"]
        marker = "✓" if score >= CONFIDENT_SCORE else "·"
        prefix = h.get("context_prefix", "")
        src = _page_from_source(h.get("source", ""))
        tag_str = ", ".join(h.get("tags", [])) if h.get("tags") else ""

        if score >= CONFIDENT_SCORE:
            lines.append(
                f"  {marker} [{score:.3f}] [knowledge:{src}] {prefix}"
            )
            lines.append(f"    {h['text'][:300]}")
        else:
            lines.append(
                f"  {marker} [{score:.3f}] [knowledge:{src}] (sub-floor) {h['text'][:180]}"
            )
        if tag_str:
            lines.append(f"    tags: {tag_str}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Tier: classic
# ═══════════════════════════════════════════════════════════════════════════


def _classic_results(query):
    """Query all sources, de-duplicate, merge into single ranked list of dicts."""
    results = []

    # 1. Knowledge store (top_k=8)
    knowledge_hits = search_knowledge(query, top_k=8)
    seen_sources = set()
    for h in knowledge_hits:
        src = h.get("source", "")
        results.append(
            {
                "source": "knowledge",
                "source_file": src,
                "page": _page_from_source(src),
                "score": h["score"],
                "text": h["text"],
                "tags": h.get("tags", []),
                "priority": h.get("priority", "normal"),
                "context_prefix": h.get("context_prefix", ""),
                "id": h["id"],
            }
        )
        if src:
            seen_sources.add(src)

    # 2. MEMORY.md keyword scan
    query_lower = query.lower()
    query_terms = re.findall(r"[a-zA-Z0-9_]{3,}", query_lower)
    memory_entries = scan_memory_entries(MEMORY_PATH)
    for entry in memory_entries:
        entry_lower = entry.lower()
        # Score: number of query terms found in entry
        matches = sum(1 for t in query_terms if t in entry_lower)
        if matches > 0:
            results.append(
                {
                    "source": "memory",
                    "source_file": MEMORY_PATH,
                    "page": "MEMORY.md",
                    "score": min(0.99, 0.5 + 0.1 * matches),
                    "text": entry[:300],
                    "tags": [],
                    "priority": "normal",
                    "context_prefix": "",
                    "id": "",
                }
            )

    # 3. USER.md keyword scan
    user_entries = scan_memory_entries(USER_PATH)
    for entry in user_entries:
        entry_lower = entry.lower()
        matches = sum(1 for t in query_terms if t in entry_lower)
        if matches > 0:
            results.append(
                {
                    "source": "user",
                    "source_file": USER_PATH,
                    "page": "USER.md",
                    "score": min(0.99, 0.5 + 0.1 * matches),
                    "text": entry[:300],
                    "tags": [],
                    "priority": "normal",
                    "context_prefix": "",
                    "id": "",
                }
            )

    # 4. Reference docs grep (ripgrep)
    if os.path.isdir(REFERENCES_DIR):
        try:
            rg_cmd = [
                "rg",
                "--no-heading",
                "--line-number",
                "--max-count=3",
                "-i",
                "-F",
                "--",
                query,
                REFERENCES_DIR,
            ]
            proc = subprocess.run(
                rg_cmd, capture_output=True, text=True, timeout=10
            )
            for line in proc.stdout.strip().splitlines():
                if not line.strip():
                    continue
                # rg output: filepath:lineno:text
                m = re.match(r"^(.+?):(\d+):(.*)", line)
                if m:
                    fpath = m.group(1)
                    lineno = m.group(2)
                    match_text = m.group(3).strip()[:300]
                    # Skip if this source file is already well-covered by the knowledge store
                    if fpath in seen_sources:
                        continue
                    results.append(
                        {
                            "source": f"refdoc:{os.path.basename(fpath)}",
                            "source_file": fpath,
                            "page": os.path.basename(fpath),
                            "score": 0.45,  # below knowledge-store floor
                            "text": f"L{lineno}: {match_text}",
                            "tags": [],
                            "priority": "normal",
                            "context_prefix": "",
                            "id": "",
                        }
                    )
        except Exception:
            pass

    # Sort by score descending
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def tier_classic(query, as_json=False, results=None):
    """Formatted classic results; pass `results` to reuse an earlier _classic_results()."""
    if results is None:
        results = _classic_results(query)

    if as_json:
        return json.dumps(
            {"tier": "classic", "query": query, "results": results}, indent=2
        )

    lines = [f"═══ QUERY ROUTER · classic · \"{query}\" ═══\n"]
    if not results:
        lines.append("  (no results)")
        return "\n".join(lines)

    for i, r in enumerate(results):
        score = r["score"]
        if r["source"] == "knowledge":
            marker = "✓" if score >= CONFIDENT_SCORE else "·"
            lines.append(
                f"  {i+1}. [{r['score']:.3f}] [{r['source']}:{r['page']}] {r['text'][:250]}"
            )
        else:
            lines.append(
                f"  {i+1}. [MATCH] [{r['source']}] {r['text'][:250]}"
            )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Tier: agentic
# ═══════════════════════════════════════════════════════════════════════════


def tier_agentic(query, as_json=False):
    """Classic search + suggested next reads."""
    # 1. Run classic search
    results = _classic_results(query)
    classic_results_raw = tier_classic(query, as_json=False, results=results)

    # 2. Top 3 knowledge source files are the suggested next reads
    top_sources = []
    seen = set()
    for h in results:
        if h["source"] != "knowledge":
            continue
        src = h.get("source_file", "")
        if src and src not in seen:
            top_sources.append(src)
            seen.add(src)
        if len(top_sources) >= 3:
            break

    # 3. Build output
    if as_json:
        return json.dumps(
            {
                "tier": "agentic",
                "query": query,
                "classic_results": results,
                "next_reads": top_sources,
            },
            indent=2,
        )

    lines = [f"═══ QUERY ROUTER · agentic · \"{query}\" ═══\n"]

    # Section: Retrieval Plan
    lines.append("── RETRIEVAL PLAN ──")
    lines.append(f"  Query: \"{query}\"")
    lines.append(f"  Sources: knowledge store (top_k=8), MEMORY.md, USER.md, reference-doc grep")
    lines.append(f"  Top knowledge source files:")
    for i, src in enumerate(top_sources):
        lines.append(f"    {i+1}. {_page_from_source(src)}  ({src})")
    lines.append("")

    # Section: First-Pass Results (classic)
    lines.append("── FIRST-PASS RESULTS ──")
    lines.append(classic_results_raw)
    lines.append("")

    # Section: Suggested Next Reads
    lines.append("── SUGGESTED NEXT READS ──")
    if top_sources:
        for src in top_sources:
            lines.append(f"    - {src}")
    else:
        lines.append("  (no knowledge source files — check the reference-doc grep hits above)")

    lines.append("")

    # Section: Agent Action Guidance
    lines.append("── ACTION GUIDANCE ──")
    lines.append("  The agent should:")
    lines.append(f"  1. Read top-ranked knowledge hits with read_file on their source files")
    lines.append(f"  2. Cross-reference MEMORY.md / USER.md matches for personal context")
    lines.append(f"  3. Load relevant Hermes skills for procedural knowledge")
    lines.append(f"  4. Query Honcho separately via honcho_search for conversation history")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

USAGE = """Hermes Query Router — unified, tiered retrieval across all Hermes knowledge sources.

Usage:
  query_router.py <tier> "<query>" [--json]
  query_router.py --help

Tiers:
  instant   Supabase hybrid vector+FTS search (fastest, one embedding call).
            Default tier if none given.

  classic   Query ALL sources in one pass: Supabase KB (top_k=8) + MEMORY.md
            keyword hits + USER.md keyword hits + reference-doc grep.
            De-duplicates and presents a single ranked, source-attributed list.

  agentic   Structured retrieval plan for an LLM agent:
            1. Run classic search
            2. Produce "suggested next reads" (top 3 knowledge source files)
            Does NOT call an LLM itself — the calling agent is the LLM.

Sources queried:
  Supabase pgvector  Primary semantic store (hybrid vector+FTS)
  MEMORY.md          Hot memory store (~2.4KB, §-separated)
  USER.md            User-profile store (~1.9KB, §-separated)
  Reference docs     ~/.hermes/references/*.md (28 files) — grep fallback

  Honcho             Conversation memory — queried SEPARATELY by the agent via
                     honcho_search MCP tool. This script does NOT query Honcho.

Flags:
  --json    Output machine-readable JSON instead of formatted text.
  --help    Show this help message.

READ-ONLY: This tool never writes to any knowledge store.
"""


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(USAGE)
        sys.exit(0)

    # Parse flags
    as_json = "--json" in args
    positional = [a for a in args if not a.startswith("--")]

    # Determine tier
    tier = "instant"  # default
    query = ""

    if len(positional) == 1:
        # Just a query, default tier = instant
        query = positional[0]
    elif len(positional) >= 2:
        tier = positional[0].lower()
        query = " ".join(positional[1:])
    else:
        print(USAGE)
        sys.exit(1)

    if tier not in ("instant", "classic", "agentic"):
        print(f"Unknown tier: '{tier}'. Choose instant, classic, or agentic.", file=sys.stderr)
        print(USAGE)
        sys.exit(1)

    if not query.strip():
        print("Error: empty query", file=sys.stderr)
        sys.exit(1)

    # Route to tier
    if tier == "instant":
        output = tier_instant(query, as_json=as_json)
    elif tier == "classic":
        output = tier_classic(query, as_json=as_json)
    elif tier == "agentic":
        output = tier_agentic(query, as_json=as_json)
    else:
        output = f"Unknown tier: {tier}"

    print(output)


if __name__ == "__main__":
    main()
