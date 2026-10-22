# Store-gate training lessons (Laya 0.4.7, ModernBERT-large, 2026-10-14)

Program log: ~/.hermes/references/laya-program.md. Data: ~/.hermes/cache/laya-exp/.

1. Student tracks teacher. Test AUC plateaued (~0.77) across epochs 4/8, 2.2k/2.6k rows, 9 seeds, 8x more updates. Only label quality moved it: teacher balanced-acc 0.72 -> 0.82 moved AUC 0.77 -> 0.82. Before tuning training, measure the teacher on a live-verified set.
2. Text-only labellers cap ~0.82-0.85 balAcc: live-only errors (work end-user cases, missing files, fixed bugs) are invisible from text. Best text-only recipe: verifier prompt verbatim, verdict-first (data/dev2/vf_prompt.md) + skeptical keep-audit (label_v3a.py r1), averaged as soft target {0,.5,1}; laya soft-CE accepts probability targets.
3. Calib set MUST be in-distribution with the target. Out-of-distribution calib (older handlabel rows) ran ~7 pts lower precision at equal threshold -> calib-transfer recall 0.26-0.34; swapping in live-verified rows from the same pool -> 0.48-0.61 at P>=0.95.
4. Dedup by normalized text can silently replace gold rows with soft copies — check src counts after build.
5. Live-verifier IAA: 88% raw / kappa 0.57; disagreements all on keep-ish rows. Adjudicate before treating a test set as truth.
6. Store-gate recall ceiling, measured R6–R9 (pooled 359-row CV recall @ P ≥ 0.95):
   - Best plain ModernBERT-large: 0.715, oracle 0.742. Best Laya (vanilla weights + gold cross-fit): 0.673.
   - These failed to move it: tie removal, LLM relabel, gold ×3 upweight, teacher→gold-only two-stage (worse, 0.558), +150 active-learning rows with 3-vote adjudicated labels (worse, 0.618), and a Laya+plain rank ensemble (0.688).
   - When even the oracle threshold is below the target, it's a ranking ceiling, not threshold picking. Stop adding levers and report it.
   - Uncertainty-sampled rows are exactly the rows verifiers disagree on (adjudicator keep rates 0.28 vs 0.48). Adding them as hard labels hurt recall. If you collect more gold, sample at random or label uncertain rows by a consensus process that's been shown to reach high agreement.
6. INT8 dynamic ONNX collapses ModernBERT-large (|dp| 0.25, AUC 0.55-0.67; laya #790). fp32 ONNX: perfect parity, 1.7 GB/seed, ~257 ms p95 single-thread on the Hermes host. 3-seed fp32 ensemble needs ~5 GB -> ship one seed chosen by CALIB AUC.
7. KB shadow: most top flags were `Session:` digests the one-line rule already catches — always measure marginal value beyond existing rules.
8. Pre-register predictions + an attribution test (shuffled-label sanity, TF-IDF+LR, frozen embedder+LR, SetFit, plain backbone fine-tune) before claiming the framework does the work.
9. Kaggle T4: ModernBERT-large plain HF fine-tune at max_len 512 OOMs; use dynamic padding + micro 4 x accum 16 + grad checkpointing. Delete checkpoints in kernel output or download fails.
