# Update-window transient failures

Moved from SKILL.md PITFALL 8 (2026-10-13). Content is correct; moved to reduce main
skill file size.

## Symptom

Cron jobs (especially Daily Knowledge Capture — anything on a `custom:<name>` provider)
fail during the update window with:
```
File ".../agent/agent_runtime_helpers.py", line ~1284, in anthropic_prompt_cache_policy
    model_lower = eff_model.lower()
AttributeError: 'dict' object has no attribute 'lower'
```
`eff_model` (= `agent.model`) is a dict instead of the model-name string.

## Why it happens

Transient while the gateway runs a **half-updated state**: the core code has been
swapped but the gateway hasn't been cleanly restarted with the *restored* patches yet
(PITFALL 1/loader window), so model resolution returns the custom provider's
model-config dict instead of the model string.

**Key insight: this is NOT a durable code bug — it SELF-HEALS after the gateway
restarts with the correct patches loaded.** Before patching anything, check whether
jobs that have run SINCE the clean restart show `last_status: ok`:
```bash
python3 - <<'PY'
import json
d=json.load(open(os.path.expanduser('~/.hermes/cron/jobs.json')))
for j in d.get('jobs',[]):
    if 'custom' in str(j.get('provider','')):
        print(j['name'], j.get('last_status'), j.get('last_run_at'))
PY
```
If the hourly jobs (Daily Knowledge Capture) already recovered post-restart, the
remaining `error` rows are just STALE STATUS from jobs that fire less often (02:30,
07:00) and simply haven't re-run yet. Trigger one (`hermes cron run <job_id>`) to
confirm rather than rewriting cron prompts or core code. Resist the urge to "fix" a
race that already resolved — verify against the live post-restart state first.

## Reproduce-to-rule-out

Build the agent exactly as the scheduler does (`resolve_runtime_provider(requested="custom:<name>")`
→ `AIAgent(model=job_model, provider=runtime['provider'], …)`) in an isolated `python3 -c`
and confirm `agent.model` is a string. A clean repro = the code path is fine = the failure
was the update-window race, not a standing bug.
