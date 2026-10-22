# Swarm worker fleet scaling — clone procedure + sizing + gate inheritance

How to add/remove swarm-worker profiles against the Studio llama-server, how many to
run, and the (non-obvious) facts about what safety machinery a cloned worker inherits.

## Cloning new workers (verified procedure)
Workers are plain profile dirs under `~/.hermes/profiles/swarm-worker-<letter>/`.
A full worker is ~8.3MB (carries its own skills/ tree). To add d…p from an existing a:
```bash
cd ~/.hermes/profiles
for letter in d e f g h i j k l m n o p; do
  name="swarm-worker-$letter"
  cp -r swarm-worker-a "$name"
  # rename in profile.yaml description (cosmetic, keeps the board readable)
  python3 -c "import os,yaml; p=yaml.safe_load(open('$name/profile.yaml')); \
    p['description']=p['description'].replace('swarm worker','swarm worker $letter'); \
    open('$name/profile.yaml','w').write(yaml.dump(p, allow_unicode=True))"
  # clear inherited logs/sessions so the clone doesn't carry a's history
  > "$name/logs/agent.log"; > "$name/logs/errors.log"
done
```
Verify each clone points at the right model/endpoint:
```bash
for l in d e f g h i j k l m n o p; do python3 -c "import os,yaml; \
  m=yaml.safe_load(open(os.path.expanduser('~/.hermes/profiles/swarm-worker-$l/config.yaml')))['model']; \
  print(f'$l: {m[\"default\"]} @ {m[\"provider\"]} -> {m[\"base_url\"]}')"; done
```
All should read `qwen2.5-32b @ custom:mac-studio-llama -> http://<studio-host>:8080/v1`.

**No gateway restart needed.** Worker profiles are read fresh on each kanban dispatch.
(Default-profile config + custom_provider changes DO need a restart; spawned worker
profiles do not.)

## How many workers? (the sizing model)
Two numbers decide it, and they are DIFFERENT questions:
- **effective_slots** = how many concurrent decodes the GPU usefully sustains. On this
  M2 Max it is NOT the `--parallel` cap. The node is memory-bandwidth-bound; aggregate
  throughput plateaus around **P=8 (~17.5 t/s)**, with slots 9–12 real but inefficient.
- **decode_duty_cycle** = fraction of a real task's wall time spent decoding on the GPU
  (vs tool calls, file reads, kanban writes, web fetches). This is the multiplier that
  converts slots → workers and is the genuinely missing measurement.

```
workers = effective_slots / decode_duty_cycle
```

Guidance by workload shape:
- duty cycle ~90%+ (pure decode, almost no tools) → 12–14 workers.
- duty cycle ~70% (analysis: read files, synthesize, write back — light fast-local I/O)
  → 12/0.70 ≈ 17 → **16**.
- duty cycle <60% (research-heavy: web search, slow external APIs, lots of I/O)
  → answer moves toward 24–32.

**Asymmetric-risk rule: miss HIGH.** An idle GPU slot on the Studio is the expensive
waste; an over-provisioned dispatcher thread just sleeps cheaply on the mini waiting for
a free slot. So when unsure between two counts, pick the larger.

Chosen this session: **16 workers (a–p)** for an analysis-dominant workload (~70% duty
cycle estimated from session style — workers had no real task history to measure from).

### Measuring duty cycle for real (when task history exists)
The clean probe is SERIAL (1 worker, tasks one at a time) so per-task timing isn't skewed
by contention. Capture per task: total wall, sum of llama.cpp `predicted_ms` across EVERY
LLM call, and non-decode time. duty = decode/wall. CAVEAT: five gateway aux roles
(`web_extract`, `compression`, `title_generation`, `triage_specifier`, `kanban_decomposer`)
also hit `:8080` — keep the conversation quiet during each measurement window or the
`/metrics` counters get polluted. Build representative cards yourself if the board is empty
(workers may have ZERO completed tasks — check `kanban.db` before assuming history exists).

## What a cloned worker INHERITS automatically (gate/hook audit)
This is the "is my new worker safe?" answer — verified by tracing the load path.

**The three guards are PROCESS-WIDE, not per-profile.** They load via
`venv/lib/python3.11/site-packages/sitecustomize.py` at Python interpreter startup, into
the single shared `hermes-agent` venv that runs the gateway, ALL workers, ha-bot, verifier.
`sitecustomize.py` adds `~/.hermes/patches` to sys.path and calls `apply_patches()` for
each guard. Nothing per-profile to wire — a freshly-cloned worker is covered the instant
it spawns.

| Guard | Inherited? | Notes |
|---|---|---|
| **write_gate** | ✅ process-wide | Patches `AIAgent._execute_tool_calls`. Worker AGENTS.md says "no write gate" — that's a BEHAVIORAL instruction to the model, NOT a bypass. The gate is physically present; a gated action with no greenlight stays blocked. |
| **memory_checkpoint** | ✅ process-wide, profile-aware | Calls `_active_hermes_home()` per write (reads live `HERMES_HOME`), so each worker monitors ITS OWN memory store, not the default's. |
| **delegation / skill-review / domain-ownership / kanban / delegation-nudge checkpoints** | ✅ process-wide | All armed at startup via the same sitecustomize chain. |
| **on_session_start hooks** | ❌ NOT inherited | The `hooks.on_session_start` key lives in the DEFAULT profile's config.yaml. Cloned worker config (copied from worker-a) lacks it, so `heal-skill-descriptions.sh` doesn't run for workers. This is fine — it's skill-hygiene maintenance, irrelevant to task workers. |

**The one global kill-switch to check:** `HERMES_WRITE_GATE=off` (and the
`HERMES_*_CHECKPOINT=off` vars) in a worker's `.env` would disable the guard for that
worker. Workers cloned from a clean a/b/c don't set them. Audit after cloning:
```bash
grep -h "HERMES_WRITE_GATE\|HERMES_MEMORY_CHECKPOINT\|HERMES_DOMAIN_CHECKPOINT" \
  ~/.hermes/profiles/swarm-worker-*/.env 2>/dev/null | grep -v '^#'
# empty output = all workers inherit defaults (gates ON)
```

## Decommissioning stale profiles
Before `rm -rf` of any profile, check it isn't carrying unique state: diff its cron jobs
against the default (`pre-update-*` snapshots tend to hold exact dupes of live crons →
safe). Profile deletion is a GATED action (state-changing). Always size-verify the freed
space with `du -sh` before AND after rather than estimating.
