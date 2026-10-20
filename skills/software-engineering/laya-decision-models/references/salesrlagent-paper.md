# arXiv:2503.23303 — SalesRLAgent (Nandakishor M, Deepmost Innovations, Mar 2025)

Structured notes, not a copy. Single-author, 11 KB TeX, CC0, no code release, comparators anonymised. Same author as the Laya repo; read it as the design rationale behind Laya-style "System 1" predictors.

## Claim in one line

For a domain decision (here: will this sales conversation convert?), a small specialised predictor trained on synthetic data beats LLM+RAG on accuracy and latency, and can say when it does not know.

## Problem framing (§I, §IV-A)

- LLM+RAG products (Kapa.ai, Mendable, Inkeep named) answer questions but do not estimate probabilities, track turn-by-turn dynamics, or know their knowledge boundary.
- Reframe as a sequential decision problem: state = conversation at turn t; action = probability estimate; reward = prediction accuracy.

## Data (§III)

- 1.2M synthetic conversations from GPT-4o, 15 industry templates, programmatic variation (length, style, objections), multi-agent simulation. 3–27 turns, median 8. 56% negative outcomes.
- Each record: transcript, speaker roles, binary outcome, per-turn conversion probability, simulated engagement metrics, industry/category.
- Embeddings: Azure OpenAI 3072-d, uncustomised; domain features layered on top.
- State vector: recency-weighted history embeddings + turn features (question density, sentiment) + engagement signals (response time, length) + sales-technique tags + objection/interest flags.

## Model (§IV-B – IV-D)

- State encoder → policy net (probability) + value net + meta-learning confidence module.
- Training traits named, algorithm not: strong policy regularisation, conservative updates, adaptive LR by difficulty, distribution-aware learning.
- Schedule: supervised warm start → RL with shaped rewards → curriculum (simple first) → adversarial counter-examples → ensembles → balanced batches. ~6 h on CPU.
- Confidence ("knows when it doesn't know"): similarity to training data, ensemble agreement, structural pattern match, novelty detection. Motivated by confident-wrong predictions on unusual conversations in early deployment.

## Serving (§V)

- Target < 100 ms: 8-bit quantisation, embedding cache, incremental state update, CPU-only multi-user, async guidance generation.
- "Orchestration layer": directed graph of retrieval nodes, vector search + cache, prompt shaped by current conversion probability.

## Reported numbers (§VI) — unverified, no code

- Accuracy / AUC: RF 0.67/0.71, XGBoost 0.69/0.74, GPT-4 zero-shot 0.59/0.63, GPT-4 few-shot 0.62/0.68, Commercial A 0.71/0.76, B 0.73/0.78, Hybrid LLM+ML 0.75/0.81, SalesRLAgent base 0.92/0.94, full 0.967/0.98.
- Latency: GPT-4 API 3450 ms, GPT-3.5 980, Claude 2750, local LLM 1650, SalesRLAgent CPU 85 ms (12 req/s).
- A/B: 217 reps, 12,433 conversations, 90 days → +43.2% conversion, −22% cycle length, +14% deal size, +9% CSAT.
- Ablation (accuracy drop): −embeddings 0.89 (−0.077); −sequential modelling 0.86 (−0.107); −meta-learning 0.94 (−0.027); −orchestration 0.92 (−0.047); −caching 0.95 (−0.017); basic ML only 0.68 (−0.287).

## Decision rules worth keeping

- Separate *judging* from *generating*: let the LLM write, let a small model decide, score, or gate.
- Sequential state beats per-item classification when the decision depends on trajectory (ablation's biggest single drop).
- Ship a confidence/abstention path from day one; the author's own failure mode was confident-wrong on novel inputs.
- Synthetic labels from a strong LLM can be enough to train a specialised judge — the same move as using Claude as the labeling oracle for Laya experiments.
- CPU is sufficient for sub-100 ms judging; GPU is a training-time concern.

## What to distrust

- No held-out real-data evaluation; test set is synthetic from the same generator.
- "Specialised RL algorithm" is never specified; results are not reproducible from the text.
- Commercial comparators anonymised; the 43.2% uplift has no control-group statistics.
- Author's later open-source work (Laya) reports base checkpoints near chance zero-shot — consistent with this paper's gains coming from training data, not architecture.

## Limitations the author lists (§VII-B)

English-only, text-only, no per-rep personalisation, correlation not causality, single-conversation not relationship-level.
