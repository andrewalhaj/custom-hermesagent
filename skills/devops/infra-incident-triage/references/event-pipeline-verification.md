# Event-Pipeline Leg-by-Leg Verification (webhook → issue → card → notify)

Proven 2026-08-13: this recipe found a delivery leg that had been silently dead for 3+ weeks
while every upstream leg was green. **A pipeline is only "firing on all cylinders" when the
FINAL delivery leg shows a success response — upstream green is a false positive for
end-to-end health.**

Applies to any event chain (here: Sentry → dispatcher webhook → Linear issue → kanban card →
Telegram notify). Verify each leg with its own evidence class; never infer a leg from its
neighbor.

## The recipe (all read-only)

1. **Route registration (static):** grep the code for the route decorators and compute the
   FULL path including router prefixes (`APIRouter(prefix="/hooks")` mounted under `/api`
   → `/api/hooks/sentry`). Don't probe a path you guessed.
2. **Route liveness (no side effects):** `curl -X OPTIONS` (or GET) the path.
   **405 = route exists and is POST-only — that's a PASS.** 401/403 = exists + guarded, PASS.
   404 = not registered, FAIL. Never POST a fake SIGNED event without a go-ahead (see the
   side-effect section below); an unsigned POST is safe and tests the auth leg.
   Pitfall: a SPA catch-all can return 200 HTML for GET on any path — a 200 with an HTML
   body is NOT proof the API route exists; the 405 on OPTIONS is the real signal.
3. **Evidence of recent firing:** `journalctl -u <svc> --since '14 days ago' | grep -iE
   'sentry|linear|<pipeline-terms>'` — look for a single event's timestamps walking the whole
   chain (webhook received → issue created → card dispatched within seconds). One traced
   event end-to-end beats a hundred green status checks.
4. **Downstream API auth:** minimal authenticated read (Linear: `{ viewer { id name } }`
   GraphQL). Report auth OK/401 only — never print the key.
5. **Destination store:** query the terminal store (kanban SQLite) for a row matching the
   traced event's title + timestamp. The row existing with the right created_at closes the loop.
6. **Delivery leg (the one everyone skips):** grep logs for the *provider's response*, not
   the send attempt. `telegram notify failed: 400` on every attempt = the leg is dead even
   though the pipeline "fired." Absence of success responses over a window IS the finding.

## The execution leg (card → decomposer → worker → done → autoclose)

Intake green (card row exists) says nothing about the fix stage. When Linear issues sit in
Backlog "despite self-healing", trace the card itself:

1. `tasks` row: `status`, `started_at`, `consecutive_failures`, `last_failure_error`. A card
   that is `archived`/`done` with `started_at=None` never ran — it was archived by hand or by
   a cleanup, not by a worker. There is no actor column; `task_events` (`kind` =
   `decomposed`/`archived`/…, `created_at`) gives the timeline, nothing more.
2. `task_events kind='decomposed'` → `payload.child_ids`; check every child the same way. The
   root never starts itself; the children do the work.
3. Worker transcript: `~/.hermes/kanban/logs/<task_id>.log`. Count `Unnamed call` lines vs
   real `⚡ <tool_name>` lines. **Many `Unnamed call` / `tool_call requires 'calls'` and zero
   named calls = the profile's model cannot drive the deferred-tool wrapper**; it then exits
   rc=0 without `kanban_complete` (logged as "protocol violation") and the failure limit
   archives it. Fix on the profile, not the card: `tools.tool_search.enabled: false` for
   small local models so board tools are exposed directly; escalate to a stronger worker
   model if it still flails. Check which profile the decomposer actually assigned the
   children to before claiming the worker fix is exercised.
4. Decomposer: `grep kanban_decompos ~/.hermes/logs/agent.log`. The aux client logs the
   provider/model/base_url it used on every call — compare that against what the endpoint
   actually serves (`curl <base_url>/models`). **A 500 `"does not logits computation"` in
   milliseconds means the port is serving a reranker/embedding model, not a chat model**;
   point `auxiliary.kanban_decomposer` (and `triage_specifier`) at the chat endpoint.
   Auxiliary settings are re-read per call — no gateway restart needed to prove the fix.
5. Re-test with ONE card: set the root back to `status='triage'` (the status the Linear hook
   inserts), zero `consecutive_failures`, clear `last_failure_error`, log an `unarchived`
   event, then watch `agent.log` for `auto-decompose … → N children` within two dispatch
   ticks and the first child's log for a named `kanban_complete`. Leave the sibling cards
   archived until the chain reaches `done` and the Linear autoclose fires.

Do DB edits from a `.py` in scratch (`python3 script.py`), not an inline heredoc: the
lifecycle guard must scan every file a terminal command references and refuses when the
command names a >1 MiB file such as `kanban.db`.

## Known pitfall: Telegram sendMessage 400 "can't parse entities"

Any service composing Telegram messages with `**`/`_`/`[` from DYNAMIC content (error
strings, titles) and sending with `parse_mode` set will eventually hit
`400 Bad Request: can't parse entities: Can't find end of the entity starting at byte offset N`
— raw error text contains unbalanced markup. Because senders typically log this as a WARNING
and continue, **delivery dies silently while the pipeline reports success.**
Robust fix pattern: escape dynamic content per MarkdownV2 rules AND add a fallback — on a
400 entity-parse error, retry the identical message with `parse_mode` stripped. Formatting
must never be able to kill delivery.

## Auth-leg probe without side effects

An UNSIGNED POST to an HMAC-guarded webhook is rejected before any handler logic runs, so it
is safe on a live route and separates two failures: **503 "secret not configured" = the
process never loaded the secret** (env problem — check `/proc/<pid>/environ`, the unit's
`EnvironmentFile`), **401 "Invalid signature" = auth is live**. A secret present in an app
`.env` file proves nothing about the running process.

## Signed end-to-end test (only with a go-ahead — it has side effects)

- Tell the user in advance what it will create. The dispatcher's Sentry handler posts a
  Telegram alert and appends to `data/sentry_messages.json` for EVERY action, even a no-op one;
  only `created` (with an issue id) goes on to Linear.
- Use a unique synthetic issue id and an obvious title ("E2E pipeline verification (safe to
  close)"). Trace it: `created Linear issue` → `linear webhook: dispatched … (id=t_…)` in the
  journal.
- The kanban card is claimed by a worker within ~15 s. Pull it off immediately:
  `hermes kanban reclaim <id>` then `hermes kanban complete <id> --force --result "synthetic
  E2E test"` — completion fires the Linear autoclose + reverse sync, which verifies the
  return legs too.
- Clean up: remove the synthetic id from `data/sentry_linear_map.json` and its entries from
  `data/sentry_messages.json` (back up first); tell the user which Telegram messages to delete.

## Env notes (this infra)

- Dispatcher: `~/hermes-dispatcher`, served by the SYSTEM unit `hermes-dispatcher.service`
  on :8787 (bare uvicorn under systemd). Health route is **`/api/health`** (`{"ok":true}`);
  bare `/health` 302s to the SPA — a monitor probing `/health` is watching a redirect, not health.
- Dispatcher modules resolve `LINEAR_API_KEY` inconsistently: `linear.py`, `linear_autoclose.py`,
  `linear_triage.py` fall back to reading `~/.hermes/.env`; `hooks.py` (Sentry → Linear),
  `chat.py`, `kanban.py`, `linear_sync.py`, `kanban_linear_poller.py` read the process env only.
  The key is now also in the dispatcher `.env` (loaded via the unit's `EnvironmentFile` drop-in),
  so a key rotation must update BOTH files. `linear_sync: LINEAR_API_KEY not set — outbound
  poller disabled` at startup means the env-only modules are dead.
- `sqlite3` CLI is not installed on the host — query kanban.db via Python stdlib `sqlite3`.
