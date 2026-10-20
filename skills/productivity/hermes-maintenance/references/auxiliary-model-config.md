# Auxiliary model config — compression thrash, aux fallbacks, block reasons

Covers `auxiliary.compression` / `web_extract` / `title_generation` in `config.yaml`.
Load when the journal shows repeated compactions, "Session compressed N times",
"compression is currently blocked", or "Summary model ... failed ... Falling back".

## Journal line → mechanism (read these before touching anything)

- **`Compression model X (...) context is N tokens, but the main model's compression
  threshold was M. Auto-lowered this session's threshold to N`** → Hermes probes aux
  feasibility before the first compaction and CLAMPS the session threshold to the aux
  model's context. An aux model smaller than `threshold × main ctx` means compaction
  every N tokens instead of M — the "compressed 3 times, accuracy may degrade" spiral.
  The clamp is per-session; a config fix only applies to NEW sessions (`/new`).
- **`Summary model 'X' failed (500 ... 'does not logits computation')` → fallback to
  main model** → the aux `base_url` serves a non-generative model (reranker/embedding
  llama.cpp). Every summary falls back to the main model, so you pay the main model
  for summaries AND still suffer the clamp — worst of both. Any non-2xx here has the
  same consequence; the fallback keeps the session alive but hides the misconfig.
- **`blocked (structural_backoff:NNN)`** → not a hang. The last compaction found nothing
  eligible outside `protect_first_n`/`protect_last_n`, so a 300 s structural no-op
  backoff stops per-turn rescans; the warning fires if the context is still over
  threshold during it. Self-clears once enough new turns exist.
  `cooldown:NNN` = summary-failure cooldown; `ineffective` = anti-thrash breaker
  tripped. Manual `/compress` bypasses backoff and cooldown (not the breaker).
- **Supabase memory vs compression are different layers.** `memory_search`/`memory_add`
  (cold store, `memory-prefetch` threads) is durable recall across sessions; it never
  removes tokens from the live window. Only compaction (summary replacing old turns)
  does. "Not offloading to Supabase" is the wrong question for a thrash symptom.

## Procedure

1. **Read the aux blocks** (never print `api_key`):
   `sed -n '/^auxiliary:/,/^[a-z]/p' ~/.hermes/config.yaml | grep -v api_key`
2. **Probe the configured endpoint LIVE with the configured model name** — config
   metadata lies about what a port serves:
   `curl -s <base_url>/models` (what is loaded) · llama.cpp `curl -s <host>/props`
   (`n_ctx`, model path) · Ollama `curl -s <host>:11434/api/tags` ·
   a 5-token chat completion against `<base_url>/chat/completions` with the exact
   `model` string — `OK` + usage proves the pair works; a 500/404 is the real cause.
   Count the fallback errors before/after any recent core update
   (`journalctl --user -u hermes-gateway.service --since ... | grep -c 'Falling back'`)
   to separate a pre-existing misconfig from a regression.
3. **Decide by context arithmetic.** The aux compression model needs ctx ≥ the main
   model's threshold or it clamps. With a large-context main model (Claude, 500k-class
   threshold) no local model qualifies → set `auxiliary.compression.provider` to the
   main provider (`anthropic`, `model: ''`, `base_url: ''`). Summaries were already
   coming from the main model via fallback; this just restores the real threshold,
   and prompt caching makes it the cheaper path. Local models remain the right choice
   for `web_extract` / `title_generation` — point those at the endpoint that actually
   serves the generative model (Ollama `/v1`, exact tag incl. `-64k` suffix).
4. **Apply with `hermes config set`** — the `patch`/`write_file` tools refuse
   `config.yaml` (security guard). Gated: back up first from a scratch script
   (`cp -p config.yaml config.yaml.bak-<ts>`), then one `hermes config set
   auxiliary.<block>.<key> <value>` per key (`''` to clear model/base_url).
5. **Verify**: `diff config.yaml.bak-<ts> config.yaml` must show ONLY the intended
   lines; parse with the runtime venv python under `HERMES_DISABLE_LAZY_INSTALLS=1`
   (system `python3` has no `yaml` — a silent empty probe is not a pass); re-run the
   live completion probe from step 2 against the new values.
6. **No gateway restart** — auxiliary config is read per call. The current session
   keeps its clamped threshold; tell the user `/new` when a session has already been
   compacted ≥3× (you are answering from a handoff summary at that point).
