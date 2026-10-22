# Hosted zero-shot judge (TypeSafe Jev) — integration procedure

For wiring a hosted noul/choice/score judge into a write path (store gate, supersede, digest selection) or a read path (reranker). The benchmark discipline from SKILL.md (predictions file first, sealed set, paired bootstrap) applies unchanged; this file is the engineering around it.

## 1. One module owns the policy

- One client module (`scripts/knowledge/jev.py` here) holds the HTTP call, the model pin (`jev-1.14.0`; `latest` moves monthly), the privacy filter, and EVERY question text + threshold as named constants (`STORE_GATE_QUESTIONS`, `SUPERSEDE_QUESTIONS`, `distill_question()`, `rerank_question()`). Callers import; they never carry their own prompt text or read a question JSON from `cache/` — a prune or an experiment edit would silently change live policy.
- Raw `urllib` over the vendor SDK: the request is three fields (`model`, `state`, `questions`), not worth a dependency.
- Credentials via the project's env helper (`knowledge.config.env`) so cron/daemon processes without an `EnvironmentFile` still find the key in the dotenv.
- Write the measured reason for each framing next to the question constant (one line). The next reader will otherwise re-litigate it.

## 2. Measure the framing before trusting the cookbook

The vendor skill recommends "content in `state`, questions reference it by path (`entries[3]`)". That framing LOST on our digest-item task (junk scored 0.62–0.78, a real decision 0.11) while putting the item inside the question's `instructions` with only the title as state separated junk (≤ 0.15) from records (≥ 0.59). Asking "is this X *from this session*" scored everything < 0.25 because the state cannot confirm provenance.

Procedure: 10 hand-picked probe items (half obvious junk, half obvious keeps) × 2–3 framings in one script, print a side-by-side table, pick the framing with the widest gap, record the table in the code comment. Takes one call per framing (~0.25 s) and avoids shipping a selector that keeps `FINISHED` and drops the decision.

Composition: when a verdict needs two conditions (same subject AND supersedes), ask two nouls in one request and AND them in code — do not fold both into one question.

## 3. Fan-out is one request

One request can carry hundreds of questions (73 noul questions in 0.24 s p50). Per-candidate loops are the wrong shape for rerankers and selectors; `{"c0": q, "c1": q, …}` over one `state` is. The store gate stays one-request-per-fact only because volume is 1–5 facts per heartbeat.

## 4. Fail modes, per slot

| Slot | On vendor error / timeout | On withheld text |
|---|---|---|
| Write gate | store everything (fail open) | store |
| Supersede / delete | do nothing (fail closed) | do nothing |
| Selector / ranking | previous behaviour (alphabetical / bge) | neutral score, not sent |

A judge failure must never block a write or stall a turn: reranker timeout = the existing reranker's budget (5 s), degrading to it.

## 5. Rollback = behaviour + data

- Behaviour: one `Environment="FLAG=…"` line per judge in a gateway drop-in (`~/.config/systemd/user/hermes-gateway.service.d/*.conf`). Rollback is remove line → `daemon-reload` → restart from outside the session (`systemctl --user start --no-block hermes-gw-restart.service`; without `--no-block` the call hangs when the gateway dies under it). Confirm with `/proc/<MainPID>/environ`, not with the drop-in file.
- Data: every drop/delete writes the FULL text plus a `backend`/`reason` field to an append-only log (`logs/store_gate_shadow.jsonl`, `knowledge_db/deleted_facts.jsonl` with `reason=jev-supersede:<new_id>`). A replay script (`scripts/jev_restore.py dropped|superseded [--since] [--apply]`) lists by default and re-inserts verbatim with `--apply`. Test both paths (0 rows) before the flip. Derived data (digests) needs no replay — regenerate.
- Write the rollout table (flag, what it does, threshold, fail mode, behaviour rollback, data rollback) to `references/<program>-rollout.md` BEFORE the restart, and record the decision in memory at decision time.

## 6. Tests inherit the gateway's environment

A shell opened from the agent inherits the live process env (`STORE_GATE_BACKEND=jev`, `JEV_WITHHOLD=off`, …). Tests that describe one backend or the privacy filter must pin it themselves with an autouse fixture (`monkeypatch.setattr(module, "BACKEND", "local")`, `monkeypatch.setenv("JEV_WITHHOLD", "on")`) in EVERY test file touching the module — a fixture in one file does not cover the next. Also monkeypatch audit-log writers (`_coldstore_log`) in judge tests, or the test verdicts land in the live audit log and look like production events.

When a patch widens a Supabase `select=` string, grep the test tree for the old literal: fake clients match on the exact query prefix.

## 7. Deciding slots

- A hosted reranker is not rejected on the "per-pair latency" prior: with fan-out it beat the local cross-encoder on wall time. It is also not accepted on one query set: the pre-registered rule is "paired ΔMRR CI lower bound > 0 on EVERY set, no regression", and on the reference set it lost (−0.024, 8 wins / 15 losses) after winning the KB set (+0.040, CI spanning 0). Before acting on any ship rule, `ls -lt` the results directory and read every finished arm — a summary that says "not yet run" describes the past, and a flip made on half the evidence costs a restart to undo. What also gates it: per-turn vendor dependency, which text leaves the box (state the mechanism: which rows, in which requests, how often), and that the local reranker becomes an untested fallback.
- Prefer the local reranker in production when the hosted judge is also the eval scorer: a judge grading a pipeline it ranks has correlated errors and flatters itself.
- Skill suggestion from roster descriptions failed (top-1 0.22 on 60 real turns): the discriminating signal is not in descriptions written as "Use when stating facts". Fix description quality before adding any judge; a wrong hint is worse than none.
- Report each slot as **implemented** (code in a live path and the first log row seen), **piloted**, or **decided** — nothing softer.

## 8. The judge as an eval scorer (offline)

`eval_recall.py --judge` adds a second column: for each question, one noul over `{question}` with the recalled block in the instructions ("do the recalled notes contain the information needed to answer the question?"). Gold substring hits stay the primary metric; the judge scores what gold cannot (paraphrase / open-ended answers) and the report prints agreement plus the two disagreement lists. Read the disagreements by direction: judge-yes/gold-miss clustering in the paraphrase bucket means gold substrings are too brittle there, not that recall failed; judge-no/gold-hit means the substring matched without the answer being present. Offline only, ~$0.001/question — never on the serving path.
