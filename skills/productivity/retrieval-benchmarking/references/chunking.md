# Chunking for our RAG stores: decision guide + experiment design

Full cited report: `~/.hermes/references/chunking-rag-research-20261014.md`.

## Decide per store, from measured sizes first
Before discussing strategies, profile each store: row/file count, text-length median/p90/max, and how much of each document the search path actually reads.
- **Knowledge store (Supabase facts): don't chunk.** Facts are atomic and short (median ~65 tokens, already the factoid-optimal size). The few long rows are `Session:` digests the store gate drops. At this scale chunking cannot move the benchmark beyond its CI. The 2,000-char cut on session digests is a writer-side truncation, not a retrieval problem.
- **References store: chunk.** `_search_references()` in `scripts/knowledge/unified_search.py` reads only the first 500 chars + filename, scores by word-set overlap, and is not recursive. The median reference is ~10× that window, so most content is unreachable.
- **Skills store:** keyword header overlap. Same fix pattern; separate routing problem.
- Grep for existing infra before proposing a build: `scripts/knowledge/chunker.py` (`chunk_overlap`: ~800-char paragraph chunks, heading breadcrumb, 1-para overlap, body_hash dedup), `indexing.py` (`index_markdown(contextualize=True)` → `store_contextualized`), and `prefix.py` (Haiku contextual prefix + `_synthetic_prefix` fallback). They existed unused.

## Strategy rules (literature-backed)
- Structure-aware (heading) chunking at medium size (~200–500 tokens) for markdown runbooks and factoid queries. Try ~2× that size as a second arm.
- Contextual prefixes (doc title + section breadcrumb, or an LLM-written 50–100-token context) are the highest-yield add-on (Anthropic: −35% failures, embeddings only). Benchmark the free synthetic prefix against the LLM one; ship the LLM version only if it wins beyond the CI.
- Skip semantic chunking: on real documents it does not beat fixed/structural splitting and costs embeddings per sentence.
- Skip late chunking with Ollama: it needs token-level states and mean pooling. Ollama's embed API returns one pooled vector, and Qwen3-Embedding pools on the last token.
- One sentence of overlap buys nothing measurable; don't tune it.

## Experiment design (reusable)
1. Write PREDICTIONS.md (arms, metric, kill rule, cost order) before any index or query exists.
2. Freeze the queries first, with ground truth = file + a verified answer substring + its char offset. Split them into DEEP (answer past the baseline's read window) and CONTROL (answer inside it), and verify the substrings programmatically.
3. Arms: A0 = the live function imported unmodified; chunk arms (no prefix / synthetic prefix / LLM prefix / larger chunks). Indexes go in local files or a separate table, never in `knowledge` (that would contaminate the fact benchmark and the store-gate data).
4. Primary metric = doc-level Success@5 on DEEP queries, collapsing chunk hits to distinct files. That is fair to a whole-file baseline. Secondary: answer-in-returned-text@5, MRR, and CONTROL Success@5 (a regression guard, max −1 query).
5. Use the production query-embedding format (the instruction prefix for Qwen3-Embedding) and throttle embedding batches: the Studio serves the live agent.
6. Wiring into `unified_search` / `prefetch` is a per-turn change: flag + log-only first, cap the reference slots in prefetch so chunks can't crowd out facts, and get a separate go-ahead.

## Chunk/snippet selection (score at the injection budget)
- Doc-level S@5 can be near ceiling while the injected text still misses the answer. Prefetch shows `text[:250]` per hit (max 5 hits across all stores); memory_search shows ~1,000 chars. Score answer-in-text@250 and @1000: the literal answer substring, whitespace-collapsed, case-insensitive. Keep deep doc S@5 ≥ 0.93 as the guard.
- Measured on the references store, deep queries:
  - Top-5 dense chunks: AiT@250 0.30.
  - **Winner:** chunk-level hybrid, RRF(k=20) of dense + BM25 over chunk text, then the best 250-char window inside each chunk by query-term overlap. AiT@250 0.57 (+0.27, CI [+0.07, +0.47]), doc S@5 unchanged.
- Bigger chunks or a one-per-file cap alone don't move the 250 budget, and neighbour expansion only helps at 1,000. Pick the snippet, not just the chunk.
- Pre-register the decision rule in `SELECTION_PREDICTIONS.md`: highest deep AiT@250 among arms that keep the S@5 guard; ties within 1 query go to the arm with no extra reranker call.

## Live wiring constraints
- `prefetch` calls `unified_search` in-process in the gateway, with a 3 s timeout. Log-only shadow work therefore runs in a never-joined daemon thread and appends JSONL under a lock. The flag is read at call time and defaults to off.
- Assert the embedding dim (2560) before overwriting an index. The embedder has a local fallback with a different dim, and a mismatched index fails silently at cosine time.
- New code loads only after a gateway restart. Andrew runs that from outside, and it kills in-flight delegate children. Hand him the restart for a gap between fan-outs, and hand over crontab lines rather than installing them.
