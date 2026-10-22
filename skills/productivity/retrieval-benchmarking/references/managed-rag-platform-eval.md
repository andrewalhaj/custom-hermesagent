# Evaluating a managed retrieval layer (Azure AI Search / Foundry IQ) against an owned index

Use when advising on, or simulating, a move of a document corpus onto a managed RAG service.
The questions are the same as for the local store: what is configurable, where the text
the model sees gets cut, and how to measure before a resource exists in the real tenant.
Verify version-specific claims against the current REST spec before quoting them.

## What the managed layer exposes (knowledge sources, GA REST 2026-04-01)

- **Managed sources (Blob/ADLS, SharePoint, OneLake) — `ingestionParameters`:** embedding
  model deployment, `contentExtractionMode` (`minimal` text crack vs `standard`
  layout-aware/OCR/tables), image verbalization on/off + `chatCompletionModel`, ACL
  carry-through (`ingestionPermissionOptions`, preview), schedule. **No chunk size,
  overlap or chunking strategy** — the generated indexer decides.
- **`searchIndex` source = the escape hatch:** build the index with a classic indexer +
  skillset (Text Split `maximumPageLength`/`pageOverlapLength`, or Document Layout for
  heading-bounded chunks), choose analyzers, vector config, semantic configuration,
  scoring profiles; register it as a knowledge source. The agent, MCP endpoint and
  per-user auth work unchanged. Rule: managed ingestion for doc-level "which file"
  search; own the index for the corpus technicians get answers from.
- **Retrieval orchestration** (query planning, routing across sources, answer synthesis,
  reasoning effort) is configured, not programmed. Custom reranker, custom fusion,
  multi-granularity return at query time, or metadata logic the planner cannot express
  all require calling the index directly from your own code — drop to that per failure
  class the eval exposes, never by default.

## Where the model's input gets cut

The local store's failure was per-hit truncation (`_hit_cap`); the managed layer's is
**omission**: `maxOutputSizeInTokens` (`maxOutputSize` in newer previews) is a total
budget enforced per document, all-or-nothing — a found document whose grounding content
exceeds the remaining budget is dropped from the response and only the activity log
shows it was retrieved. Vendor's own fix: index large documents as smaller chunks with
stable identifiers — which again means owning the index when ingestion is managed. Both
failure modes are invisible to doc-hit S@k; score answer containment on the actual
`retrieve` response with the production budget, and read the activity log for dropped
documents.

## Where a hosted judge (Jev) fits

Anywhere the code path is owned: ingest-time labelling (`choice` tree-walk → filterable
index fields; the planner can filter on fields it is told about), publish-time quality
gate (`noul`: complete procedure? names the equipment?), supersede detection between
revisions, and as the eval judge. Not inside managed chunking (the judge never sees the
text before it is cut) and not as a query-time reranker inside the managed loop (no hook;
and the cross-encoder beat it on the reference set anyway). Every call ships text to the
vendor — at an employer, data classification/DPA before the first call.

## Simulating the migration without the real tenant or data

1. **Owned-stack dry run, zero cloud:** stand-in corpus of public manuals shaped like the
   real one (vendor PDFs), the frozen question set, the existing chunker/hybrid/reranker/
   judge harness. Decides chunk size, hybrid vs vector, reranker value. Employer data stays
   off the personal stack.
2. **Free-tier sandbox:** AI Search Free tier (50 MB / 3 indexes, no SLA, region-dependent;
   semantic ranker and agentic retrieval available on Free in select regions with query
   limits) + Blob of the same stand-ins; run the set through the real `retrieve` call with
   the real output budget. Compare managed ingestion vs a self-built index on the same
   docs — that comparison is the "own the index or not" decision, made on receipts.
3. There is no local emulator for the managed layer; level 1 says what should work, level
   2 says what the service actually does with it.
