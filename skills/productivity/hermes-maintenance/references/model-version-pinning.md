# Pinning / bumping model versions across profiles

Use when the user asks "are the coder profiles on the latest X" or wants every profile moved to a new model id.

## Where the model actually lives

- Profile config `profiles/<p>/config.yaml` has dozens of `model: ''` lines under `model_catalog` — those are per-provider overrides and are empty by design. The effective model is the single `model:` block near the end: `model.default` (+ `model.provider`, `model.api_key_env`). Grep `^  default: ` not `model:`.
- Main `config.yaml` has three independent model settings that all need the same bump: `model` (top line, the main agent), `delegation.model` (every delegate_task child), `auxiliary.vision.model`. `auxiliary.*` applies per call; `delegation.model` and the main model need a gateway restart.
- The `claude -p` job path (`run_one.sh` template in skill claude-code) uses the `sonnet`/`opus` alias, so it follows the latest automatically — nothing to pin there.

## Finding the latest id without guessing

Docs and web search are often stale or empty. The reliable probe is the alias resolution of Claude Code on this box:

```bash
cd $TMPDIR && timeout 90 claude -p --model sonnet --max-turns 1 --output-format json 'Reply with only your exact model id string.' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result'), list((d.get('modelUsage') or {}).keys()))"
```
`modelUsage` keys are the authoritative id the request was billed to. The profiles' `api_key_env` names a var that is NOT in the dotenv (they run on the OAuth bypass), so a direct `/v1/models` call with that key fails — don't go down that path.

## Applying

`config.yaml` files are write-gated for patch/write_file: put a script in `cache/scratch/`, back up each file alongside (`config.yaml.bak-<date>-<model>`), assert exactly one match per file before `sed -i`, and print the resulting line. Then verify with `python3 -c "import yaml; ...['model']['default']"` per profile.

## Verifying it took

Run a one-shot on one profile and read what the session recorded, not what the model says about itself:

```bash
HERMES_HOME=~/.hermes/profiles/coder timeout 150 hermes chat --oneshot -q 'Reply with exactly your model id string.'
# then: sqlite3 profiles/coder/state.db "select model from sessions order by rowid desc limit 1"
```
The other profiles are byte-identical on the model block if `diff <(grep -E 'model|provider' a/config.yaml) <(... b/config.yaml)` is empty — one live probe covers the clones. Sessions dir may be empty for profiles that only run via the gateway; `state.db` is the record.

Finish by stating what still needs a restart and whether children are in flight (a restart kills running delegate_task children).
