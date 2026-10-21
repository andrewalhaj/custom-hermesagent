# Ragas testset generation for the reference corpus

Use Ragas only to *generate* a frozen query set over documentation (references/*.md + allowlisted
reports). Score it with our own deterministic `eval_refs.py`; never use an LLM judge as a ship/kill
gate — judge noise at n≈60–150 cannot resolve 0.02 deltas and judges are lenient on fluent answers.
Ragas end-to-end faithfulness is a *baseline to watch*, not a gate.

## Setup (isolated venv, never the gateway venv)
- `cache/laya-exp/rg1/.venv` (uv, CPython 3.12). Ragas 0.4.x needs the langchain **0.3.x** line — a fresh
  resolve pulls langchain 1.x and breaks `ragas.testset` imports; pin and lock (`requirements.lock.txt`).
- `rapidfuzz` wheel may be absent: a pure-Python shim module satisfies Ragas's OverlapScoreBuilder.
- LLM: wrap our own `llm.py` Haiku caller (the subscription OAuth token needs the Claude-Code request
  shape) in a custom LangChain LLM class — do not point `langchain-anthropic` at the token.
- Embeddings for the Ragas knowledge graph: wrap the same embed function `ref_chunks.py` uses in a
  LangChain `Embeddings` subclass; one pass over the corpus, keep Studio load minimal.
- Installing into the venv is a gated write when run from a subagent — the orchestrator installs,
  the worker generates.

## Procedure
1. Check plan utilization before the generation batch (token-optimization skill); pause ≥0.65.
2. Enumerate the indexed corpus from the *index manifest*, not the plan — docs changed after the
   index build must be excluded or they score as misses; count what is actually there.
3. Generate 2–3× the target, then QA-filter (`build_set.py`): drop questions answerable from the
   first 500 chars when a deep query was intended, drop near-duplicates, run the secret guard on
   both question and answer (DTG reports are in the corpus) — **sanitize answers only, never queries**.
4. Freeze `benchmark_references.json` + `qrels_references.json` and commit them in `knowledge_db/`
   **before** any arm runs. Record predictions in the experiment PLAN first.
5. Arms: off / on at the shipped cap / on with the cap lifted. The shipped-cap arm is the number that
   describes live behaviour; the lifted arm is the ceiling. Report both, and the per-slot token cost.
6. Report deep and control splits separately with the delta CI; the bar is the CI lower bound > 0 on
   deep queries with no control losses.

## Reading the numbers
- Questions are written *from* the ground-truth chunk, so the off-arm baseline is pessimistic and
  the magnitude is inflated. Direction and CI are the usable result; do not quote the off-arm S@5 as
  a real-world number.
- Expect Ragas to over-deliver on count and deep share (≈150 kept from a 220 raw target, ~80% deep)
  and to cost ~5 Haiku calls per kept query.
