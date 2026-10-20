"""Background-fact benchmark (PROGRAM step 7.1): does prefetch() recall MEMORY.md facts on its own?

Each fact gets a direct question (names the fact) and an indirect one (a task that needs it but doesn't
name it). Hit = a needle appears in the real supabase plugin's prefetch() block, i.e. what the agent sees.
Run (gateway venv):  HF_HUB_OFFLINE=1 /usr/local/lib/hermes-agent/venv/bin/python scripts/knowledge/factbench.py
"""
import functools, importlib.util, json, os, sys, urllib.parse
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))

# knowledge_db/factbench.json (gitignored: the facts are private infra) = [{needles, direct, indirect}].
# Facts snapshot of hot memory + cold refs as of 2026-10-16 (both now live in Supabase). Needles: lowercase substrings
# specific to the fact; no commas/parens (they break the PostgREST ilike filter).
FACTS_PATH = HERMES_HOME / "knowledge_db" / "factbench.json"


def load_facts(path=FACTS_PATH):
    return [(f["needles"], f["direct"], f["indirect"]) for f in json.loads(Path(path).read_text())]


PREDICTIONS = {"direct": ">=0.90", "indirect": "<=0.50"}  # on record in PROGRAM.md before the first run


def load_prefetch():
    """The live plugin's prefetch, loaded by path: plugins/supabase would shadow the pip `supabase` package."""
    spec = importlib.util.spec_from_file_location(
        "hermes_supabase_plugin", HERMES_HOME / "plugins" / "supabase" / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SupabaseMemoryProvider().prefetch


@functools.cache  # both passes ask the same 20 needles; each call scans every reference file
def stored_in(needle):
    """Where the fact lives today: 'kb' (Supabase knowledge rows) and/or 'refs' (references/*.md, also searched)."""
    from knowledge.supabase_client import _supa_select
    where = []
    if _supa_select("select=id&limit=1&text=ilike." + urllib.parse.quote(f"*{needle}*")):
        where.append("kb")
    if any(needle in p.read_text(errors="ignore").lower() for p in (HERMES_HOME / "references").rglob("*.md")):
        where.append("refs")
    return where


def run(prefetch, facts, where=stored_in):
    rows, hits = [], {"direct": 0, "indirect": 0}
    for needles, direct, indirect in facts:
        row = {"needle": needles[0], "stored": where(needles[0])}
        for kind, q in (("direct", direct), ("indirect", indirect)):
            block = (prefetch(q) or "").lower()
            row[kind] = any(n in block for n in needles)
            hits[kind] += row[kind]
        rows.append(row)
    n = len(facts)
    return {"n": n, "direct": hits["direct"] / n, "indirect": hits["indirect"] / n,
            "predictions": PREDICTIONS, "rows": rows}


def kb_only(q):
    """Diagnostic: the knowledge store's own top 5, cut like prefetch does — what prefetch could see
    if unified_search's cross-store merge didn't rank it."""
    from knowledge.search import search
    return "\n".join(" ".join(h.get("text", "").split())[:250] for h in search(q, top_k=5))


if __name__ == "__main__":
    sys.path.insert(0, str(HERMES_HOME / "scripts"))
    facts = load_facts()
    for label, fn in (("prefetch (live path)", load_prefetch()), ("kb-only top5 (diagnostic)", kb_only)):
        res = run(fn, facts)
        print(f"== {label}   [direct indirect]")
        for r in res["rows"]:
            print(f"{'HIT ' if r['direct'] else 'miss'} {'HIT ' if r['indirect'] else 'miss'}  "
                  f"{r['needle']:<26} stored={'+'.join(r['stored']) or 'NOWHERE'}")
        print(f"n={res['n']}  direct={res['direct']:.0%}  indirect={res['indirect']:.0%}  "
              f"predicted direct {PREDICTIONS['direct']}, indirect {PREDICTIONS['indirect']}\n")
        if "--json" in sys.argv:
            print(json.dumps({"label": label, **res}))
