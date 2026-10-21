# Self-hosted web stack (Firecrawl + SearXNG + Camofox) — triage

Hermes `web_search` and `web_extract` both ride the self-hosted stack when
`config.yaml → web.backend: firecrawl` and `.env → FIRECRAWL_API_URL=http://localhost:3002`.
The two tools take DIFFERENT paths, so "scraping is off" needs splitting first:

- `web_extract` → Firecrawl `/v1/scrape` → Playwright/Camofox → target site.
- `web_search`  → Firecrawl `/v1/search` → **SearXNG** (`localhost:8888`) → public engines.

**Empty `web_search` with a working `web_extract` = SearXNG upstream, not Firecrawl.**
Do not restart containers for that symptom; the containers are fine.

## Diagnostic chain (read-only, ~3 calls)

1. Confirm which backend and URL Hermes uses (don't assume cloud):
   `grep -n 'backend' config.yaml` and `grep ^FIRECRAWL_API_URL .env`. A local URL means the
   cloud key is irrelevant to the symptom even if `api.firecrawl.dev` answers 200.
2. Split scrape vs search against the local API:
   ```bash
   curl -s -m 60 -X POST http://localhost:3002/v1/scrape -H 'Content-Type: application/json' -d '{"url":"https://example.com","formats":["markdown"]}' | head -c 300
   curl -s -m 60 -w '\nHTTP %{http_code}\n' -X POST http://localhost:3002/v1/search -H 'Content-Type: application/json' -d '{"query":"test","limit":3}' | head -c 600
   ```
   Search returning an empty body / HTTP 000 at the timeout while scrape returns markdown → step 3.
3. Ask SearXNG directly; its JSON names the failing engines:
   `curl -s 'http://localhost:8888/search?q=test&format=json' | head -c 400` → read
   `unresponsive_engines` (e.g. `["brave","too many requests"]`, `["duckduckgo","CAPTCHA"]`).
   All engines rate-limited/CAPTCHA'd = this box's egress IP is burned; a restart changes nothing.
4. Container state — set the socket explicitly, the default `DOCKER_HOST` on this box points at a
   missing Docker Desktop socket and `docker ps` fails with a misleading error:
   `DOCKER_HOST=unix:///var/run/docker.sock docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -iE 'firecrawl|searx|camofox'`
   Expected: `firecrawl-api-1`, `-redis-1`, `-rabbitmq-1`, `-playwright-service-1`, `-nuq-postgres-1`, `searxng`, `camofox-browser`.
   Supervising unit: `hermes-web-stack.service` (system scope, `active (exited)` is normal — it's a compose wrapper).
5. Firecrawl's own view: `docker logs --tail 30 firecrawl-api-1 | grep -i search` — `Using searxng search` with
   no results line confirms the hand-off.

## Fix options (all config mutations → report + greenlight + backup first)

- **Metered, zero infra (the applied fix):** set `web.search_backend: tavily` (or `exa`) — NOT `web.backend`,
  which would move extract off local Firecrawl too. Precedence in `tools/web_tools.py::_get_search_backend`:
  `web.search_backend` > `web.backend` > autodetect; the key is read per call, so no gateway restart.
  Keys already in `.env`; the provider plugin lives in `plugins/web/tavily/`. config.yaml refuses
  `patch`/`write_file` → `cp` backup, then a python `re.subn` on the exact `search_backend:` line
  (assert one match), `yaml.safe_load` to prove validity, then prove it with the real `web_search`
  tool, not curl — the tool path is what was broken.
- **Pre-check the key:** `curl -X POST https://api.tavily.com/search -d '{"api_key":"$TAVILY_API_KEY","query":"x"}'`
  → 200 before flipping config.
- **Firecrawl cloud for search only:** the cloud key works on `/v1/search`; costs credits per query.
- **Repair SearXNG:** add engines (google, bing, mojeek, qwant) or route its outbound traffic through a VPS
  egress so the burned IP stops mattering. Free, but it re-burns under unthrottled use.

## Workaround while search is down

Go straight to the canonical page with `web_extract` (vendor pricing/spec pages, docs) — scrape still works.
For retail listings use `browser_exec` (see the procurement skill's Amazon recipe).
