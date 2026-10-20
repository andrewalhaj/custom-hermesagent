# Hybrid search pitfalls (Postgres FTS + pgvector)

Rules for any keyword + vector store: memory recall hooks, doc chunk search, RAG apps.

## Keyword side

- **Never feed a whole prompt to `websearch_to_tsquery` / `plainto_tsquery`.** Both AND every word, so one word missing from the row means zero hits. Paraphrase and vague queries score 0%. Build an OR of the prompt's own lexemes instead; Postgres stems and drops stopwords for you:

  ```sql
  WITH q AS (
    SELECT to_tsquery('english',
             array_to_string(tsvector_to_array(to_tsvector('english', %(query)s)), ' | ')) AS tsq
  )
  SELECT m.id, ts_rank_cd(m.content_tsv, q.tsq) AS score
  FROM memories m, q
  WHERE m.content_tsv @@ q.tsq
  ORDER BY score DESC LIMIT %(k)s;
  ```

  Guard first: if `to_tsvector` yields no lexemes (stopword-only prompt), skip FTS and return `[]`, because an empty `to_tsquery` raises. Keep `websearch_to_tsquery` only for an explicit exact/phrase mode.
- **Index prefix + body.** For chunks, the tsvector must cover the path/heading prefix as well as the body, or heading words never match.

## Vector side

- **Never store a NULL or zero vector silently.** When the embed call fails, keep the row, log a WARNING with its id, and backfill `WHERE embedding IS NULL` later (idempotent, batched). A NULL row is invisible to vector search forever; a zero vector poisons cosine ranking.
- **Embedding latency is usually auth, not the model.** Check the per-call token source first. A token fetched per call (cache file decrypt, or a CLI subprocess on a miss), plus a new HTTP client (fresh TLS) per call, turns ~200 ms into ~2 s. Fix: an in-process token memo until expiry minus a buffer, and one reused keep-alive client. A hook that runs as a new process per prompt only benefits from an on-disk token cache and lazy imports.
- **Vector search always returns top-k.** Add a minimum-similarity cutoff for anything that auto-injects context, calibrated on no-answer queries. OpenAI `text-embedding-3-*` cosine scores sit in a compressed mid range, so a guessed cutoff like 0.8 rejects everything.
- **Changing embedding model:** `text-embedding-3-large` accepts `dimensions=1536` (Matryoshka), so it fits an existing `vector(1536)` column with only a re-embed. pgvector HNSW/IVFFlat indexes cap `vector` at 2,000 dims (`halfvec` 4,000), so a full 3,072-dim model forces a column-type change. Upgrade only when a benchmark that can discriminate shows a gap.

## Fusion

- RRF with k=20 (see `rrf-k20-migration.md`). If the keyword side returns nothing, hybrid equals vector-only by construction. Log per-query FTS hit counts so a dead keyword side is visible.
- A cross-encoder reranker is optional, off by default and pluggable. Fall back to fused order on any error or timeout, and turn it on only after an eval on the user's own corpus shows a CI-positive gain within the latency budget. CPU rerankers on a laptop must be timed on the target hardware; published latencies are estimates.
