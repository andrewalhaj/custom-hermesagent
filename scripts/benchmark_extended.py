#!/usr/bin/env python3
"""Extended retrieval benchmarks: Recall@10 + Multi-hop recall + Graph traversal.

Usage:
  python3 ~/.hermes/scripts/benchmark_extended.py              # all benchmarks
  python3 ~/.hermes/scripts/benchmark_extended.py --json        # machine-readable
  python3 ~/.hermes/scripts/benchmark_extended.py --skip-recall # multi-hop only
  python3 ~/.hermes/scripts/benchmark_extended.py --skip-graph  # recall only
"""

import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import knowledge

NEO4J_URI = os.environ.get('NEO4J_URI', '')
NEO4J_USER = os.environ.get('NEO4J_USER', os.environ.get('NEO4J_USERNAME', 'neo4j'))
NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD', '')
NEO4J_DATABASE = os.environ.get('NEO4J_DATABASE', 'neo4j')
if NEO4J_URI and '://' in NEO4J_URI and NEO4J_DATABASE == 'neo4j':
    NEO4J_DATABASE = NEO4J_URI.split('://')[1].split('.')[0]


def get_neo4j_driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def find_neighbors(driver, fact_text, depth=1):
    """Find all RELATED_TO neighbors of a fact. Returns list of text snippets."""
    neighbors = set()
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            node = session.run(
                'MATCH (f:Fact) WHERE f.text STARTS WITH $text '
                'RETURN f.text AS text LIMIT 1',
                text=fact_text[:200],
            ).single()
            if not node:
                return []
            neighbors.add((node['text'] or '')[:120])
            result = session.run(
                f'MATCH (f:Fact)-[:RELATED_TO*1..{depth}]-(n:Fact) '
                'WHERE f.text STARTS WITH $text '
                'RETURN DISTINCT n.text AS text',
                text=fact_text[:200],
            )
            for record in result:
                neighbors.add((record['text'] or '')[:120])
    except Exception as e:
        print(f"  neo4j error: {e}", file=sys.stderr)
        return []
    return list(neighbors)


def find_multi_hop_chains(driver, min_hops=2, max_hops=3, limit=15):
    """Find fact chains A→B→C via RELATED_TO. Returns [{start_text, end_text, hops, chain_texts}]."""
    chains = []
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run(
                f'MATCH p=(a:Fact)-[:RELATED_TO*{min_hops}..{max_hops}]->(b:Fact) '
                'WHERE a.community <> b.community '
                'RETURN a.text AS start_text, b.text AS end_text, length(p) AS hops '
                'LIMIT 30'
            )
            for record in result:
                start = (record['start_text'] or '')[:120]
                end = (record['end_text'] or '')[:120]
                if not start or not end or start == end:
                    continue
                path_result = session.run(
                    f'MATCH p=(a:Fact)-[:RELATED_TO*{min_hops}..{max_hops}]->(b:Fact) '
                    'WHERE a.text STARTS WITH $start AND b.text STARTS WITH $end '
                    'RETURN [n IN nodes(p) | n.text] AS texts LIMIT 1',
                    start=start[:100], end=end[:100]
                ).single()
                chain_texts = []
                if path_result and path_result['texts']:
                    chain_texts = [(t or '')[:120] for t in path_result['texts']]
                chains.append({
                    'start_text': start,
                    'end_text': end,
                    'hops': record['hops'],
                    'chain_texts': chain_texts,
                })
    except Exception as e:
        print(f"  neo4j multi-hop error: {e}", file=sys.stderr)
    return chains[:limit]


def keyword_from_text(text):
    """Extract 3-5 salient keywords for a query from fact text."""
    words = re.findall(r'[A-Za-z0-9_/.-]+', text)
    stop = {'the','a','an','is','are','was','were','be','been','being',
            'have','has','had','do','does','did','will','would','could',
            'should','may','might','can','shall','to','of','in','for',
            'on','with','at','by','from','as','into','through','during',
            'and','or','not','but','if','then','else','when','up','out',
            'this','that','these','those','it','its','i','me','my','we',
            'our','you','your','he','she','they','them','their','his','her'}
    keywords = [w for w in words if w.lower() not in stop and len(w) > 2][:5]
    return ' '.join(keywords)


def run_recall_10(driver, benchmark_path, json_out=False):
    """Recall@10 with vs without graph expansion."""
    with open(benchmark_path) as f:
        queries = json.load(f)

    results = []
    total_related = 0
    total_found_wo = 0
    total_found_w = 0
    total_found_t = 0

    for q in queries:
        query_text = q['query']
        sub = q.get('expected_substring', '')

        # Find full target text from Supabase
        target_text = sub
        try:
            probe = knowledge.search(sub, top_k=3)
            for p in probe:
                if sub.lower() in (p.get('text') or '').lower():
                    target_text = p['text'][:200]
                    break
        except Exception:
            pass

        relevant_texts = [target_text[:120]] + find_neighbors(driver, target_text)

        # --- BASELINE (no graph) ---
        os.environ.pop('GRAPH_EXPAND', None)
        os.environ.pop('GRAPH_TRAVERSE', None)
        hits_wo = knowledge.search(query_text, top_k=10)
        found_wo = sum(1 for rt in relevant_texts
                       if any(rt.lower()[:60] in (h.get('text') or '').lower() for h in hits_wo))

        # --- GRAPH_EXPAND ---
        os.environ['GRAPH_EXPAND'] = 'true'
        os.environ.pop('GRAPH_TRAVERSE', None)
        hits_w = knowledge.search(query_text, top_k=10)
        found_w = sum(1 for rt in relevant_texts
                      if any(rt.lower()[:60] in (h.get('text') or '').lower() for h in hits_w))

        # --- GRAPH_TRAVERSE ---
        os.environ.pop('GRAPH_EXPAND', None)
        os.environ['GRAPH_TRAVERSE'] = 'true'
        hits_t = knowledge.search(query_text, top_k=10)
        found_t = sum(1 for rt in relevant_texts
                      if any(rt.lower()[:60] in (h.get('text') or '').lower() for h in hits_t))

        total_related += len(relevant_texts)
        total_found_wo += found_wo
        total_found_w += found_w
        total_found_t += found_t

        r_wo = found_wo / len(relevant_texts) if relevant_texts else 0
        r_w = found_w / len(relevant_texts) if relevant_texts else 0
        r_t = found_t / len(relevant_texts) if relevant_texts else 0

        results.append({
            'query': query_text,
            'relevant_count': len(relevant_texts),
            'recall_baseline': round(r_wo, 4),
            'recall_expand': round(r_w, 4),
            'recall_traverse': round(r_t, 4),
        })

        if not json_out:
            print(f"  {query_text[:55]:<55}  R@{len(relevant_texts):>2}: "
                  f"{r_wo:.2f} → expand:{r_w:.2f} → traverse:{r_t:.2f}")

    n = len(results)
    macro_wo = sum(r['recall_baseline'] for r in results) / n if n else 0
    macro_w = sum(r['recall_expand'] for r in results) / n if n else 0
    macro_t = sum(r['recall_traverse'] for r in results) / n if n else 0

    summary = {
        'benchmark': 'recall@10',
        'queries': n,
        'macro_recall_baseline': round(macro_wo, 4),
        'macro_recall_expand': round(macro_w, 4),
        'macro_recall_traverse': round(macro_t, 4),
        'micro_recall_baseline': round(total_found_wo / total_related, 4) if total_related else 0,
        'micro_recall_expand': round(total_found_w / total_related, 4) if total_related else 0,
        'micro_recall_traverse': round(total_found_t / total_related, 4) if total_related else 0,
        'total_relevant_facts': total_related,
        'results': results,
    }

    if not json_out:
        print(f"\n  Macro R@10:  {macro_wo:.4f} → expand:{macro_w:.4f} → traverse:{macro_t:.4f}")
        print(f"  Micro R@10:  {summary['micro_recall_baseline']:.4f} → expand:{summary['micro_recall_expand']:.4f} → traverse:{summary['micro_recall_traverse']:.4f}")
        print(f"  Facts surfaced:  {total_found_wo}/{total_related} → {total_found_w}/{total_related} → {total_found_t}/{total_related}")

    return summary


def run_multi_hop(driver, json_out=False, mode='baseline'):
    """Multi-hop recall: can retrieval reach both ends of a fact chain?

    mode='baseline': standard vector search
    mode='traverse': GRAPH_TRAVERSE=true (1-hop interleaved neighbors)
    mode='chain':    GRAPH_TRAVERSE_CHAIN=true (2-hop iterative chain traversal)
    """
    chains = find_multi_hop_chains(driver, min_hops=2, max_hops=3, limit=15)

    if not chains:
        print("  No multi-hop chains found in Neo4j", file=sys.stderr)
        return {'benchmark': 'multi-hop', 'chains_tested': 0, 'error': 'no_chains'}

    # Set mode
    os.environ.pop('GRAPH_EXPAND', None)
    os.environ.pop('GRAPH_TRAVERSE', None)
    os.environ.pop('GRAPH_TRAVERSE_CHAIN', None)
    if mode == 'traverse':
        os.environ['GRAPH_TRAVERSE'] = 'true'
    elif mode == 'chain':
        os.environ['GRAPH_TRAVERSE_CHAIN'] = 'true'

    results = []
    hits_start = 0
    hits_end = 0
    hits_both = 0

    for i, chain in enumerate(chains):
        query = keyword_from_text(chain['start_text'])
        hits = knowledge.search(query, top_k=10)

        start_found = False
        end_found = False
        chain_texts_lower = [(t or '').lower() for t in chain.get('chain_texts', [])]

        for h in hits:
            ht = (h.get('text') or '').lower()
            if chain['start_text'].lower()[:80] in ht:
                start_found = True
            if chain['end_text'].lower()[:80] in ht:
                end_found = True
            for ct in chain_texts_lower:
                if ct[:60] in ht:
                    if ct[:60] == chain['start_text'].lower()[:60]:
                        start_found = True
                    elif ct[:60] == chain['end_text'].lower()[:60]:
                        end_found = True

        if start_found:
            hits_start += 1
        if end_found:
            hits_end += 1
        if start_found and end_found:
            hits_both += 1

        results.append({
            'chain_id': i,
            'hops': chain['hops'],
            'query': query,
            'start_found': start_found,
            'end_found': end_found,
            'both_found': start_found and end_found,
            'start_snippet': chain['start_text'][:80],
            'end_snippet': chain['end_text'][:80],
            'graph_source': [h.get('graph_source', '') for h in hits if h.get('graph_source')][:3],
        })

        if not json_out:
            sources = ','.join(set(h.get('match_source', '') for h in hits if h.get('match_source')))
            status = ("✓both" if (start_found and end_found)
                      else ("✓start" if start_found else ("✓end" if end_found else "✗")))
            print(f"  [{status}] {query[:48]:<48}  start={'Y' if start_found else 'N'}  "
                  f"end={'Y' if end_found else 'N'}  hops={chain['hops']}  src={sources or 'vector'}")

    total = len(chains)
    summary = {
        'benchmark': f'multi-hop-{mode}',
        'chains_tested': total,
        'start_recall': round(hits_start / total, 4) if total else 0,
        'end_recall': round(hits_end / total, 4) if total else 0,
        'both_recall': round(hits_both / total, 4) if total else 0,
        'results': results,
    }

    if not json_out:
        print(f"\n  Start recall: {hits_start}/{total} = {summary['start_recall']:.4f}")
        print(f"  End recall:   {hits_end}/{total} = {summary['end_recall']:.4f}")
        print(f"  Both recall:  {hits_both}/{total} = {summary['both_recall']:.4f}")
        print(f"  (Mode: {mode})")

    return summary


def main():
    json_out = '--json' in sys.argv
    skip_graph = '--skip-graph' in sys.argv
    skip_recall = '--skip-recall' in sys.argv

    benchmark_path = os.path.join(knowledge.DB_DIR, 'benchmark_queries.json')
    if not os.path.exists(benchmark_path):
        print(f"No benchmark file at {benchmark_path}", file=sys.stderr)
        sys.exit(1)

    driver = None
    try:
        if NEO4J_URI and NEO4J_PASSWORD:
            driver = get_neo4j_driver()
            driver.verify_connectivity()
    except Exception as e:
        print(f"Neo4j unavailable: {e}", file=sys.stderr)
        driver = None

    if not driver:
        print("Neo4j required. Set NEO4J_URI/NEO4J_PASSWORD.", file=sys.stderr)
        sys.exit(1)

    all_summaries = {}

    # ── RECALL@10 ──
    if not skip_recall:
        if not json_out:
            print("=" * 72)
            print("RECALL@10 — baseline vs GRAPH_EXPAND vs GRAPH_TRAVERSE")
            print("=" * 72)
        t0 = time.perf_counter()
        all_summaries['recall@10'] = run_recall_10(driver, benchmark_path, json_out)
        if not json_out:
            print(f"\n  ({time.perf_counter() - t0:.1f}s)")

    # ── MULTI-HOP: baseline ──
    if not skip_graph:
        if not json_out:
            print("\n" + "=" * 72)
            print("MULTI-HOP — BASELINE (vector only)")
            print("=" * 72)
        t0 = time.perf_counter()
        all_summaries['multi_hop_baseline'] = run_multi_hop(driver, json_out, mode='baseline')
        if not json_out:
            print(f"\n  ({time.perf_counter() - t0:.1f}s)")

    # ── MULTI-HOP: GRAPH_TRAVERSE ──
    if not skip_graph:
        if not json_out:
            print("\n" + "=" * 72)
            print("MULTI-HOP — GRAPH_TRAVERSE (1-hop interleaved neighbors)")
            print("=" * 72)
        t0 = time.perf_counter()
        all_summaries['multi_hop_traverse'] = run_multi_hop(driver, json_out, mode='traverse')
        if not json_out:
            print(f"\n  ({time.perf_counter() - t0:.1f}s)")

    # ── MULTI-HOP: GRAPH_TRAVERSE_CHAIN ──
    if not skip_graph:
        if not json_out:
            print("\n" + "=" * 72)
            print("MULTI-HOP — GRAPH_TRAVERSE_CHAIN (2-hop iterative chain traversal)")
            print("=" * 72)
        t0 = time.perf_counter()
        all_summaries['multi_hop_chain'] = run_multi_hop(driver, json_out, mode='chain')
        if not json_out:
            print(f"\n  ({time.perf_counter() - t0:.1f}s)")

        # ── SUMMARY ──
        if not json_out:
            bl = all_summaries['multi_hop_baseline']
            tr = all_summaries['multi_hop_traverse']
            ch = all_summaries['multi_hop_chain']
            print("\n" + "=" * 72)
            print("MULTI-HOP COMPARISON")
            print("=" * 72)
            print(f"  {'':<20} {'baseline':>10} {'traverse':>10} {'chain':>10}")
            print(f"  {'Start recall':<20} {bl['start_recall']:>10.4f} {tr['start_recall']:>10.4f} {ch['start_recall']:>10.4f}")
            print(f"  {'End recall':<20} {bl['end_recall']:>10.4f} {tr['end_recall']:>10.4f} {ch['end_recall']:>10.4f}")
            print(f"  {'Both recall':<20} {bl['both_recall']:>10.4f} {tr['both_recall']:>10.4f} {ch['both_recall']:>10.4f}")

    if driver:
        driver.close()

    if json_out:
        print(json.dumps(all_summaries, indent=2))


if __name__ == '__main__':
    main()
