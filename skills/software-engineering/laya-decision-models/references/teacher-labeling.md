# Teacher-model labelling (scaling a judge's training set)

Use when the labelled set is too small to fine-tune and the plan is to have an LLM label more rows. The order matters: size the real pool, pilot, measure, then run with a guard.

## 1. Count the real candidate pool before quoting any volume or cost

The judge must be trained on the distribution it will gate. For the store gate that is the output of the live extractor, not raw chat text:

```python
sys.path.insert(0, os.path.expanduser('~/.hermes/scripts'))
from session_capture import extract_facts   # pure regex: no store, no embedding calls
# iterate state.db messages (role in user/assistant, length > 80), extract_facts(text),
# dedupe on lowercase text, and drop rows whose text is already in the labelled set
```

Do not call `capture_session` / `is_duplicate`: they hit the knowledge store and the Studio embedder. Across the whole history the extractor produced only ~300 new candidates, an order of magnitude below a "3k rows" plan. Say the pool size BEFORE promising a dataset size. Ways to go beyond the pool: an LLM extracting candidates directly from the sessions (closest to real data, costs a pass over every message) or synthetic paraphrase (cheap, but copies the labeller's biases).

## 2. Pilot 100 rows and extrapolate from token counts

- Batch 25 rows per call, the rubric in the system prompt, output only `[{"i":n,"label":"keep|drop"}]` (minimal output tokens).
- Sum `usage` (input/output/cache) from every response and extrapolate from that. Measured on single-sentence extractor facts: ~50 input + ~10 output tokens per row (100 rows = 4,958 in / 976 out, 12 s). Rows are much shorter than a chat message, so do not estimate from message size.
- The plan gauges report whole percent, so a pilot cannot move them. Use them as a bound, never as the measurement.
- Script template: `~/.hermes/cache/laya-exp/teacher_pilot.py` (writes `data/teacher_pilot.jsonl` + `data/teacher_pilot_usage.json`). Run it with the gateway runtime python inside a small `systemd-run --user --scope -p MemoryMax=2G`.
- Spot-check pilot labels before scaling; they are unvalidated until a second labeller or the user agrees.

## 3. Run on the shared subscription only with a guard

Teacher calls through the OAuth login share the same plan allowance as the live agent and its subagents, and overage is disabled, so exhausting the weekly cap stops the agent too. Calling pattern and gauge headers: `token-optimization` → `references/claude-plan-usage.md`.

- One request in flight, ≥ 3 s between calls, exponential backoff on 429.
- Read the utilization headers on every response. Andrew's standing ceiling: neither the 5h nor the weekly gauge may pass 0.75 — at 5h ≥ 0.75 sleep until the 5h reset, at 7d ≥ 0.75 sleep until the weekly reset. Ask before using a different ceiling.
- Checkpoint progress (last processed message id per session + append-only JSONL) so a pause, crash or restart loses nothing.
- Measuring progress for an ETA (read-only, one call): `wc -l labels.jsonl`, `progress.json` → `last_msg_id`, then from `state.db` opened `mode=ro` via python `sqlite3` (the sqlite3 CLI may be absent) count user/assistant messages with `id <= last_msg_id` vs all of them; the fraction done, times the label rate (labels per minute since the unit started), gives the remaining time and projected total. Last utilization: the final `CALL` line of `usage.log`. Re-estimate the pool from this, not from the original plan: the full history yields ~1,200 rows, not the ~3k first quoted.
- Concurrency: 2–3 requests in flight is fine while the 5h gauge is low; total tokens are the same, only wall time drops. The guard still reads headers on every response.
- Model split: verbatim-span extraction is a selection task, so a cheaper model (Haiku) is enough; keep the strong model for keep/drop labelling. Roughly halves the backlog's weekly cost.
- Restarting the job's own unit (`systemctl --user restart <name>`) still trips the write gate. Include "restart the teacher unit after script changes" in the original greenlight, or ask; then arm the gate with the approval note.
- Long-running jobs run as a transient user unit, not a gateway child, so gateway restarts don't kill them: `systemd-run --user --unit=<name> -p MemoryMax=1G -p Restart=on-failure -p RestartSec=60 <python> teacher.py --run`; stop with `systemctl --user stop <name>`. No persistent unit files or cron.

## 4. Generate candidates EXTRACTIVELY, label in a separate call

- When an LLM proposes candidates (including deliberate weak/noise ones), every candidate must be a verbatim span (one or two consecutive sentences) of the source chunk; reject in code anything that is not a substring after whitespace normalisation, and log the rejection rate. LLM-written noise carries the LLM's style, and the student learns the style instead of the decision (same failure as templated negatives).
- Label in a separate call from extraction so the labeller isn't grading its own picks; record a 1–3 confidence and re-label a random 5% in a fresh call to estimate self-agreement.
- Store per row the source role, message timestamp, session id and message ids — needed later to test a richer input format (fact + date + newest related fact).
- Dedupe on normalised text, against the existing labelled set too. No embedding calls (the Studio serves live lookups), no KB writes.
- A separate Anthropic API key removes the contention entirely (costs money, no shared-pool impact).
- Parse robustness: on any JSON parse failure, append the raw response to a `parse_errors.jsonl` (chmod 600 — it holds session text) so the cause is diagnosed from data, and report the parse-error rate per phase. Every failed call is spent usage that yields nothing.
- On any rubric change, write a cutover marker (label count + UTC timestamp) so old-rubric labels stay identifiable; re-label them in the background rather than mixing silently.
- Rubric must name the drop categories live verification found Claude keeping: imperative/task instructions, bare code lines or shell commands, generic advice not specific to this user's systems, truncated fragments, one-off debug traces/PIDs, recommendations rather than decisions made.

## 5. Free labels before paid ones

Before spending plan usage, look for decisions already recorded on disk: facts that cleanup/stale-data jobs deleted from the KB (known drops), rows carrying `[SUPERSEDED …]` markers (known-answer pairs for the pairwise supersede gate), and applied-vs-discarded pending-memory reviews. Verify the logs exist before counting on them.

## 6. Label-quality gate, early

As soon as a few hundred teacher labels exist, live-verify ~60 of them (same subagent recipe as the test set: verdict + evidence per row). If more than ~10% flip, tighten the rubric before the backlog finishes — a keep-biased teacher wastes the whole run. The label mix alone is not evidence: a drop rate below the verified rate (~40% here) only hints at bias.

- Score the teacher per CLASS, not overall: text-only Claude drop calls were ~95% precise while its keep calls were only ~46% right (wrong keeps were mostly NOT_A_FACT on wording, the rest stale), whatever model extracted the row. The keep class is where the student learns noise.
- Agreement measured on the dev/QC rows did not transfer: a rubric at 90% on the QC holdout scored 71% on a fresh random sample of the final pool, because the pool changed after QC was drawn (new extractor, more rows). Validate a rubric on a sample drawn from the pool you will actually train on, as late as possible.
- Live verifiers beat the teacher on wording-only errors partly because their schema forced a verdict (CURRENT / STALE / NOT_A_FACT …) before the label. Use verdict-first output for the teacher too, and for a second skeptical pass over keep calls only (a row must name something concrete in the user's own systems that will still matter in 3 months; UNSURE stays keep). Accept such a pass only if holdout keep-precision rises ≥ 15 points at keep-recall ≥ 0.70, and write it to a new labels file rather than overwriting.

## 7. Label bake-off (pick the labeller before a relabel)

Use when the student has plateaued at the teacher's level (SKILL.md plateau diagnostic). One fan-out, ~7 min, negligible plan usage:

1. Draw a fresh dev set (~150 rows) at random from the TRAINING pool with a new seed. Exclude test, calib and QC ids AND their normalised texts. Scrub secrets, then write `key.json` `[{n, id, text}]`.
2. In parallel: 5 live-verifier subagents, 30 rows each (`n` ranges, same brief as the test set, blind to every label file) = truth. Plus 1 subagent producing every candidate label set blind on the same rows, each to its own `cand_<name>.jsonl`. Candidates: the current labels, the skeptical keep-audit, and the verifier's own instructions run text-only (verdict first, no tools).
3. Fix the acceptance bar in a PREDICTIONS file before scoring: accuracy ≥ 0.85 AND keep-precision ≥ 0.70. Report accuracy, per-class precision/recall and balanced accuracy for each candidate. Balanced accuracy is what predicts the student's AUC, because test sets here are ~80% drop and raw accuracy rewards dropping everything.
4. If none passes, read the best candidate's errors by the TRUTH verdict. If they are mostly NOT_A_FACT/STALE rows only a live probe exposes (employer end-user cases, files that no longer exist, fixed bugs), text-only labelling has hit its ceiling (balanced accuracy ~0.82–0.85). Prompt tweaks won't fix that.

Measured on dev2 (125 drop / 25 keep):
- v3 rubric: balanced accuracy 0.72, keep-precision 0.33.
- Skeptical audit: accuracy 0.84, balanced accuracy 0.76.
- Verifier prompt text-only: balanced accuracy 0.82, drop precision 0.97, keep recall 0.88, keep precision 0.43.
- Post-hoc: averaging two labellers into a soft target {0, 0.5, 1} ranked dev at AUC 0.87. That only pays off if the trainer accepts probability targets; check the soft-CE path in the source before relying on it, and fall back to dropping the 0.5 rows.
- After scoring, the live-verified dev rows are most valuable as the CALIB set: they come from the training/test distribution, unlike older verified rows. Remove their texts from train, and move the older verified rows into train as gold instead. If you add dev rows to train as gold, make gold win the text dedup; otherwise the existing teacher-labelled copies silently survive.

### Tie-breaker bake-off on train-only gold (once dev rows have become calib)

Once the dev set is calib, it is part of the eval pool and must never select a labeller again. Run the next bake-off on gold that lives only in TRAIN (e.g. the hand-labelled rows), with the labeller blind to everything but the text.
- Arms: each existing prompt on its original model, the best prompt on a stronger model or two, and the best prompt plus ~8 few-shot examples. Draw the examples only from teacher rows where the existing labellers AGREED, never from the gold being scored.
- Report balanced accuracy with a bootstrap CI.
- Floor: balanced accuracy ≥ 0.80 before the winner touches data. When the winner is an existing prompt on a newer model, "beats the baselines" means beats the other labeller and the old model. Say so rather than letting the condition pass on a technicality.
- Measured: the verifier prompt on a current Sonnet scored balanced accuracy 0.89, above an Opus model (0.86), above the same prompt with 8 few-shot examples (0.87), and above the skeptical keep-audit (0.84). A bigger model or more examples did not beat the better prompt on the mid-tier model.
- Map the winner's verdict + 1–5 confidence onto soft targets (e.g. conf ≥ 4 → 0.85/0.15, else 0.7/0.3), so a tie becomes a confident but not hard label. Append the never-relabelled rows the same way, deduped on normalised text against calib/test.
- Before using any live-verifier fan-out, compare keep rates across the parallel verifiers. Rows are shuffled, so the parts should match. In R9, two of five verifiers kept 57–63% while three kept 23–27% of the same mix: the lenient ones kept bare code lines and edit narration as "UNVERIFIABLE but durable". Adjudicate with a blind second pass over every keep plus ~20 control drops, and break disagreements with a third verifier. Reusable brief: `~/.hermes/cache/laya-exp/r9/VERIFIER_BRIEF.md`; add the strict NOT_A_FACT paragraph used in the adjudication pass.
- After relabelling, compare the new training keep share with the eval pool's. The relabelled rows ran ~46% keep vs ~21% keep in the live-verified pool, because the train-only gold was ~47% keep and the prompt was tuned on it. A large prior shift is a warning: retrain and score on the pool before trusting the relabel. If precision at the top drops, reweight classes back toward the pool prior.
