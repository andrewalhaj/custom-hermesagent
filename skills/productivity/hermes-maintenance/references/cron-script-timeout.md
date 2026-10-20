# Cron script timeout — resolution chain + the pre-summarization pattern

## The 120s default and where the knob lives (verified 2026-08-13)
`no_agent` / pre-run cron scripts are killed by the SCHEDULER at a default **120s** — the failure
line in jobs.json is `Script timed out after 120s: <path>`. A script's own internal
`subprocess timeout=600` never fires; the outer kill wins.

Resolution order in `/usr/local/lib/hermes-agent/cron/scheduler.py::_get_script_timeout()`:
1. patched module `_SCRIPT_TIMEOUT`
2. env `HERMES_CRON_SCRIPT_TIMEOUT`
3. **config.yaml → `cron.script_timeout_seconds`** ← the durable lever
4. default 120

Fix applied 2026-08-13: `cron.script_timeout_seconds: 600` added to the `cron:` block in
`~/.hermes/config.yaml` (gated edit). Typical culprit for >120s scripts: embedding-model cold load
(~2-3 min) inside a benchmark/eval.

## Agent-job context overflow → script-driven pre-summarization
Failure: agent cron job dies with `RuntimeError: Context length exceeded (N tokens). Cannot
compress further.` Root cause pattern: the job's `skills:` list force-loads a huge skill (the
~103KB knowledge-store skill ≈ 25-30K tokens) into a small-context model (32K), leaving no room
for work.

**The fix pattern (verified on job e49150c365e4):** don't load the skill — port its LOGIC into a
script. Write `~/.hermes/scripts/<job>_audit.py` that does the mechanical scans and prints a
compact (<2K-token) summary; set the job's `script=` to it (script stdout auto-injects into the
agent prompt as context); set `skills: []`; replace the prompt with a slim one that reviews only
the injected summary. Result: ~30K tokens of context → <1K, job fits with huge margin.

Generalization: any agent cron job on a local/small-ctx model should receive **pre-computed
summaries, not raw skills or raw data**. The agent's job is judgment over a digest, not scanning.
