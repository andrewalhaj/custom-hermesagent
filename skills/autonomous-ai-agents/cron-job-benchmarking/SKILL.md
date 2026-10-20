---
name: cron-job-benchmarking
description: "Use when backtesting a recurring agent job's prompt"
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cron, benchmarking, backtesting, prompt-iteration, delegation]
    related_skills: [kanban-swarm-dispatch, retrieval-benchmarking]
    created_by: agent
---

# Cron Job Benchmarking — the benchmark→grade→patch loop

Iteratively validating and improving a scheduled agent job (cron) whose output makes predictions or recommendations that reality later grades — market scanners, forecasts, monitors, pickers. Proven end-to-end on the pre-market stock scanner (Sep 2026): 3 backtest rounds + live grading took the job from v1 to v7 with out-of-sample-validated rules.

## When to use

- A new cron job produces gradeable output (predictions, picks, alerts) and the user wants it validated before/while trusting it.
- User says "keep benchmarking", "test it against real results", "how accurate was it".

## The loop

1. **Reconstruct past runs via subagents.** Fan out `delegate_task` — one child per historical day/period — each rebuilding what the job WOULD have output using only sources from that time ("morning news coverage only"). Firewall them from outcomes: explicitly instruct "do NOT report how it turned out — the parent grades separately." Have each child verify its own date/weekday (calendar slips are common) and end with a machine-parseable line (`TICKERS: X, Y, Z`) so grading can be scripted.
2. **Grade against ground truth yourself** (never the children): fetch actual outcomes via `execute_code` loops, grade on the metric a USER of the output would experience (for trades: from-the-open fill, not close-vs-prior-close — a gap already captured is not tradeable).
3. **Persist a running scorecard** at `~/.hermes/references/<job>-benchmark.md`: per-round grades, aggregate stats, prompt version history, "watch items" (patterns seen once). Context compacts; the file survives and lets future sessions grade live runs against the same standard.
4. **Patch the job prompt** (`cronjob action=update`) only when a failure pattern has ≥2 independent occurrences. One-off anomalies go in the scorecard as watch items, not rules — that's the overfitting guard. Bake each rule's EVIDENCE into the prompt text itself ("(backtest: X faded 7/8 times)") so the rationale survives in the job, not just the chat.
5. **Out-of-sample test**: after deriving a rule from rounds 1..N, the next round is data the rule never saw. A rule that holds there is validated; one that doesn't gets reverted, not defended.
6. **Stop at convergence**: a full round producing no new failure patterns (only refinements) = loop closed. More backtest rounds past that = churn plus rising overfit risk on thinning historical data. Live runs become the ongoing validation.

## Pitfalls

- **Reconstructed data is thinner than live data** (fewer surviving articles the further back you go) — backtest hit rates are a FLOOR, say so explicitly.
- **In-sample flattery**: retro-applying new rules to the data that produced them inflates the win rate. Report both raw and rules-applied numbers, label which is which.
- **Washes vs fails**: outcomes that went nowhere are not losses. Report both win/total and win/decided; explain the difference when the user asks (they will).
- **Rule-evidence counting**: track n per rule. n=1 or n=2 rules stay labeled as such even when the story is clean.
- **Test fire after creation**: `cronjob action=run` with a transient prompt override adapts the job to off-schedule conditions (e.g. "market closed — reconstruct this morning instead of reading live pages"). The override is per-fire, never persisted.
- **A rule that costs money gets reverted, not defended** — the benchmark decides, not the rule's history. State predictions before results land (Andrew audits metric changes for self-serving bias; never change the measuring stick mid-task).

## Reporting style (Andrew)

Scorecard tables (call | plan | result | grade), named winners/losers with magnitudes, the pattern found, the patch applied, honest caveats (sample size, data thinning) every round. Past-tense: grade, patch, then report what changed.

## References

- `references/premarket-scanner.md` — the worked example: scanner job design, final ruleset A–H with evidence, grading method, benchmark-file location.
