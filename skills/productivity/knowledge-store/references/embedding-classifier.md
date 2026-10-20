# Embedding-Based Taxonomy Classifier (Phase 1A, 2026-06-26)

The `_detect_query_sector` keyword regex was replaced with an embedding-based classifier
that can **abstain** (return `[]`) — turning a similarity sorter into a write-time gate.
Lives in `knowledge/classifier.py`. Design doc: `~/.hermes/references/taxonomy-architecture-v1.md`.
Build spec: `~/.hermes/references/classifier-build-spec.md`.

## Module map (post-refactor — knowledge.py is now a PACKAGE)

`knowledge.py` is a thin CLI shim. The real code is the `knowledge/` package:

| Module | Owns |
|---|---|
| `supabase_client.py` | `get_supabase`, `_supa_get/post/insert/select/all_rows` |
| `embed.py` | `get_model`, `embed`, LRU cache, `get_manifest_key` |
| `chunker.py` | `chunk_overlap`, heading stack, normalize |
| `prefix.py` | `_manifest_prefix`, `_synthetic_prefix`, `generate_prefix` |
| `storage.py` | `store`, `store_contextualized`, cold-store guard |
| `search.py` | `search`, `_search_layered`, `_single_search`, `_build_search_context`, `_detect_query_sector` (now delegates to classifier), `_FIB_WEIGHTS` |
| `graph.py` | `build_graph` (unified — was duplicated at L1748+L2077), `graph_neighbors`, traversal |
| `indexing.py` | `index_markdown`, `index_vault`, `contextualize_file`, `auto_index` |
| `eval.py` | `eval_benchmark` |
| `utils.py` | `status`, `count_facts`, `recent`, `stale_check`, `summarize` |
| `flags.py` | all `_*_enabled()` env-gate functions |
| `classifier.py` | `classify`, `classify_batch`, `explain`, `build_centroids` |

Pitfalls in the SKILL.md body that cite `knowledge.py` line numbers (e.g. "search() at
line ~676", "`_single_search`") now refer to `knowledge/search.py` — the logic is the same,
the file moved. `import knowledge` and `from knowledge import search, store, ...` still work
(re-exported via `knowledge/__init__.py`); the CLI `python3 knowledge.py search "..."` is
unchanged.

## Classifier architecture

4-stage pipeline (theory: Snell prototypes + Sun kNN-reject + Guo/Geifman calibration):

```
Stage 0  hash cache (SQLite, DB_DIR/classification_cache.db)   ~0ms
Stage 1  keyword fast-path, high-conf only (>=0.85)            ~0ms
Stage 2  prototype centroid match → top-5 candidates           ~50ms
Stage 3a kNN reject: dist to k=5 nearest anchors → abstain     +10ms
Stage 3b cross-encoder rerank (flag RERANK_CLASSIFY=true)      +100ms
```

- `taxonomy.json` — canonical tree, anchors (full sentences) + anti_examples per node.
  63 nodes, 402 anchors at build time.
- `centroids.pkl` — L0/L1/L2 mean embeddings (gitignored, regenerate from taxonomy).
- `anchors.pkl` — flat (N,768) matrix of ALL anchor embeddings, for the kNN reject scorer.
- `classification_threshold.json` — calibrated thresholds from the risk-coverage sweep.

Build / rebuild: `python3 knowledge/build_centroids.py --output-dir knowledge/ [--calibrate]`.

## ⚠️ Centroid cancellation — the bug that makes L0 scores collapse to ~0 (root-caused 2026-06-26)

**Symptom:** after `build_centroids`, `explain()` shows ALL L0 cosine scores in the ~0.01–0.02
range (e.g. infrastructure=0.0072, monitoring=−0.0256), every query abstains with
"centroid L0 score 0.02 < threshold 0.4", and the centroid vector's max abs component is tiny
(~0.12 vs a real embedding's ~0.5). Meanwhile a DIRECT `cosine(query, matching_anchor)` is a
healthy ~0.52 — so embeddings are fine; the centroids are broken.

**Root cause (a permanent property of prototype classifiers, NOT an env bug):** an L0 centroid
built as the normalized mean of MANY semantically-diverse unit vectors collapses toward zero.
With 74 infrastructure anchors at mean pairwise cosine ~0.12 (very spread — they span
hosting + networking + storage + all leaves), the pre-normalization mean vector has norm ~0.36;
the diverse directions cancel. After normalization it's a unit vector pointing in a low-signal
"average" direction that resembles no specific anchor. The more anchors and the more spread,
the worse the cancellation. This will recur EVERY rebuild if centroids naively average all
descendant anchors.

**Fix (two parts, both required):**
1. **Cascade with an SVD fallback in `build_centroids()`.** Build L0/L1 centroids by
   aggregating all child anchors, then: compute the mean; if `norm(mean) < 0.15` (the
   cancellation signal), use the **first principal component** of the anchor matrix instead of
   the mean (`np.linalg.svd(mat - mean, full_matrices=False)` → `Vt[0]`, then normalize). PC1
   captures the dominant shared direction even when the plain mean cancels. L2 leaves (few,
   tight anchors) keep the plain normalized mean.
2. **Lower the L0/L1/L2 thresholds.** The 0.40 default was calibrated for direct-embed
   comparison, not aggregated centroids whose scores are inherently lower. Set
   `L0_THRESHOLD=0.25, L1_THRESHOLD=0.20, L2_THRESHOLD=0.18` (or run `--calibrate` to derive
   them from labeled facts via the risk-coverage sweep — preferred once labeled data exists).

After the fix the same infrastructure query scored cleanly and 9/10 smoke tests passed
(the one miss, "knowledge.py search semantic pgvector embed" → storage/database instead of
knowledge/retrieval, is a genuinely ambiguous query, not a bug).

**General lesson:** whenever you build a class prototype by averaging embeddings, check the
PRE-normalization mean norm. A tiny norm means the class is multimodal/spread and the mean is
garbage — switch to PC1, or split the class. Never trust a normalized centroid without
checking what its mean norm was before normalization.

## Reject scorer (kNN-OOD, Sun 2022)

The abstain decision is `mean cosine distance to the k=5 nearest anchor rows across ALL nodes`
(reuses `anchors.pkl`). If that distance exceeds the calibrated `knn_distance_threshold`, the
input is out-of-distribution → return `[]`. This is what makes the classifier safe as a
write-time gate: novel/unrelated text abstains rather than being force-fit to the nearest node.
Verified: `classify("asdfjkl qwerty random gibberish")` → `[]`.

## Calibration (do NOT hardcode the threshold)

`build_centroids.py --calibrate` sweeps centroid + kNN thresholds against facts that already
carry a `sector_path` (labeled validation), measures selective_risk vs coverage at each point
(Geifman risk-coverage), and writes the threshold at selective_risk ≤ 5% to
`classification_threshold.json`. Abstention quality is a SEPARATE metric from accuracy — report
the risk-coverage curve, not just P@5 (a classifier that's accurate but never abstains is
useless as a gate).

**⚠️ Calibrating on classifier-self-labeled facts is CIRCULAR and inflates risk (2026-06-26).**
The sweep needs LABELED validation data. If the only "labels" are sector_paths the classifier
itself just backfilled (see "batch backfill" below), the calibration is measuring the
classifier against its own opinions — the risk numbers come out high (30% at low thresholds,
best-achievable ≤5% risk only at 34% coverage) and the thresholds it picks are meaningless.
The tell: the sweep can't find a threshold with both low risk AND useful coverage. Real
calibration needs a human-validated sample, or at minimum facts whose sector_path came from a
DIFFERENT source (webhook, manual enrichment) than the classifier under test. Until then keep
the hand-set `L0=0.25/L1=0.20/L2=0.18` defaults rather than trusting a circular sweep.

## Stay frozen — never fine-tune the embedder (Kumar LP-FT, arXiv 2202.10054)

Fine-tuning the embedder to sharpen classification distorts the distance geometry the kNN
reject scorer depends on (~7% worse OOD on average). The whole design is frozen-embedding +
centroid/kNN head precisely to sidestep this. If you upgrade the embedder (e.g. mpnet →
Qwen3-Embedding-4B on the LH02), swap the CHECKPOINT and re-embed — never fine-tune.

## Reranker selection (if Stage 3b is enabled)

Size does NOT predict rerank quality. `gte-reranker-modernbert-base` (149M, single forward
pass) beats much larger autoregressive yes/no rerankers (Qwen3-Reranker) on Hit@1 AND is
faster. Bake off `gte-modernbert` vs `mxbai-rerank-base-v1` (already loaded) vs
`bge-reranker-v2-m3` on a labeled set; pick by accuracy-per-ms. Don't default to the biggest.

## ⚠️ The classifier is NOT good enough to use as a SECTOR pre-filter yet (proven 2026-06-26)

`SECTOR_FILTER=true` pre-filters the RPC candidate pool to facts whose sector matches the
query's classified sector. With the classifier at its current anchor quality (~30%
misclassification on the calibration sweep), enabling the filter **regressed** retrieval:

| Config | P@5 | MRR |
|---|---|---|
| No filter (baseline) | **0.62** | **0.538** |
| SECTOR_BOOST + SECTOR_FILTER | 0.58 | 0.472 |

A pre-filter built on a not-yet-accurate classifier blocks the RIGHT answers (the classifier
puts the query in the wrong sector, so the correct fact gets filtered out before retrieval sees
it). **A broken filter is strictly worse than no filter.** Rule: benchmark any retrieval filter
against the no-filter baseline BEFORE enabling it in production, and keep it behind an env flag
so it can be turned off instantly. SECTOR_FILTER stays OFF until the classifier clears a
meaningful accuracy bar (and the anchors are audited for spread — see centroid cancellation).

## ⚠️ `_build_search_context` two-bug pattern — both silently kill recall (fixed 2026-06-26)

After the package refactor, `_build_search_context()` was always computing AND passing both a
`metadata_filter` and a `sector_filter` into `_single_search` unconditionally. Two distinct
bugs, each returns 0 results:

1. **`metadata_filter` against an unpopulated store = 0 hits.** The earlier "wire the dead
   metadata_filter through" fix made `_extract_query_metadata` actually reach the RPC. But the
   store does NOT have populated `hosts/ports/services` metadata on most facts, so the RPC's
   `WHERE metadata @> filter` matched nothing → 0 results for any query that hit a metadata
   regex (e.g. "VPS-B VPS Hetzner" extracted `{hosts:['vps-b']}`, no fact tagged vps-b →
   nothing). **Fix:** gate metadata pre-filtering behind a `METADATA_FILTER=true` env flag
   (`_metadata_filter_enabled()` in flags.py) — OFF until the store has comprehensive metadata
   indexing. Wiring a filter through is not the same as the store being ready for it.

2. **Env flag checked at IMPORT time, not CALL time, so runtime toggles are ignored.**
   `_build_search_context` set `sector_filter` whenever `query_path` existed, ignoring
   `SECTOR_FILTER`. And the flag's effect only took hold if read at call time — a module
   imported before the env var was set sees the stale value. (This is the same import-vs-call
   pitfall already documented for ENRICH_QUERY/RERANK in the SKILL body — it recurs in every
   new gate.) **Fix:** `sector = query_path[0] if (query_path and _sector_filter_enabled()) else None`
   with `_sector_filter_enabled()` calling `os.environ.get()` at invocation.

Net effect of both fixes: P@5 0.62 → **0.74** with zero other changes. The diagnostic that
found it: trace `_build_search_context(query)` output directly and check whether
`metadata_filter`/`sector_filter` are non-None when they shouldn't be, then confirm the raw
`_single_search` with those filters returns 0 while without them returns the right fact.

## ⚠️ Exact-duplicate facts (same text, different UUID) swamp RRF — dedup by normalized text (2026-06-26)

Memory entries stored repeatedly across many sessions accumulate as EXACT-text duplicates with
different UUIDs (the body_hash/UUID dedup doesn't catch them when they entered via different
paths). One session found 179 duplicates across 10 groups in a 335-fact store: 36× "Fleet
repos...", 33× "LH01 = Intel 2018...", 33× "Dispatcher: ~/hermes-dispatcher/...",
33× "Makes fast architectural decisions...". These high-frequency duplicates dominate RRF
scores and crowd specific facts out of the top-K for unrelated queries.

**Dedup procedure (normalize-text grouping, keep oldest):**
```python
from collections import defaultdict
facts = _supa_all_rows(select='id,text,stored_at', page=500)
groups = defaultdict(list)
for f in facts:
    groups[f['text'].strip().lower()].append(f)   # normalize: strip + lowercase
dupes = {k: v for k, v in groups.items() if len(v) > 1}
# keep earliest stored_at (most established), DELETE the rest via REST DELETE per id
```
335 → 156 facts, 0 errors. Note: dedup alone may not move P@5 if the surviving copy still
ranks the same — its value is removing cross-query noise (the same wrong fact stops winning
unrelated queries) and shrinking the store, not necessarily lifting the headline number.

## ⚠️ "Deleted or just not surfacing?" — the benchmark-failure triage (2026-06-26)

When `eval_benchmark` shows failures, do NOT assume retrieval degraded. Split failures into two
buckets FIRST:

1. **Stale benchmark entry** — the `expected_substring` points at a fact that no longer exists,
   OR at the WRONG fact (e.g. two queries both expecting a generic kanban fact, or a git-commit
   notification fact that was webhook noise). The retrieval is fine; the test ground-truth rotted.
   Fix the benchmark entry, not the system.
2. **Genuine retrieval failure** — the expected fact EXISTS in the store but ranks below top-K.

Diagnostic: for each failure, check if the expected fact's substring still matches any row in
`_supa_all_rows`. EXISTS + low top_score (0.08–0.47) = genuine retrieval miss (often noise facts
or duplicates winning — see dedup above). DELETED/wrong-fact = stale benchmark.
This is the content-keyed-benchmark lesson (already in SKILL body: key on `expected_substring`,
not `expected_id`) applied to triage: when the substrings themselves point at the wrong fact,
the benchmark — not retrieval — is what's stale. Several "failures" this session were the
benchmark expecting the wrong fact; fixing 4 entries lifted MRR 0.564 → 0.617.
