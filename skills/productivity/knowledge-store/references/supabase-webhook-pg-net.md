# Supabase → Hermes Webhook: pg_net Trigger Pattern

Built 2026-06-24. Connects Supabase `INSERT` on `public.knowledge` → async HTTP POST to Hermes `/api/hooks/knowledge` → auto-appends `knowledge.py search "…" [auto]` pointer to MEMORY.md.

## Architecture

```
knowledge.py store → Supabase INSERT
                    → pg_net trigger fires (AFTER INSERT, async)
                    → net.http_post(https://hermes.example.com/api/hooks/knowledge)
                    → Cloudflare Tunnel → Hermes dispatcher (:8787)
                    → routes/hooks.py verifies bearer token
                    → derives search term, appends [auto] pointer to MEMORY.md
```

## Supabase Side: pg_net Trigger

### Enable the extension
```sql
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;
-- Note: functions live in `net` schema, NOT `extensions`
```

### Correct function signature (THIS IS THE TRAP)
The docs show `net.http_post(url, body, params, headers, timeout)`. Headers is `jsonb`, NOT an array of `http_header` structs.

**WRONG** (will error: "function does not exist"):
```sql
PERFORM extensions.http_post(
    url := _url,
    body := _payload::text,
    headers := ARRAY[
        extensions.http_header('Content-Type', 'application/json'),
        extensions.http_header('Authorization', 'Bearer ' || _secret)
    ]
);
```

**CORRECT**:
```sql
PERFORM net.http_post(
    url     := _url,
    body    := _payload,          -- jsonb, not text
    headers := _headers            -- jsonb, not array
);
```

### Full trigger function
```sql
CREATE OR REPLACE FUNCTION public.on_knowledge_insert()
RETURNS TRIGGER AS $$
DECLARE
  _url    constant text := 'https://hermes.example.com/api/hooks/knowledge';  -- (real URL: memory_search "webhook endpoint")
  _secret constant text := '<64-char hex secret>';
  _payload jsonb;
  _headers jsonb;
BEGIN
  _payload := jsonb_build_object(
    'id',       NEW.id,
    'text',     left(NEW.text, 200),
    'tags',     to_jsonb(NEW.tags),
    'source',   NEW.source,
    'priority', NEW.priority
  );

  _headers := jsonb_build_object(
    'Content-Type', 'application/json',
    'Authorization', 'Bearer ' || _secret
  );

  PERFORM net.http_post(
    url     := _url,
    body    := _payload,
    headers := _headers
  );

  RETURN NEW;
EXCEPTION WHEN OTHERS THEN
  RAISE LOG 'knowledge insert webhook failed (row %): %', NEW.id, SQLERRM;
  RETURN NEW;  -- never block the insert
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS knowledge_insert_webhook ON public.knowledge;
CREATE TRIGGER knowledge_insert_webhook
  AFTER INSERT ON public.knowledge
  FOR EACH ROW EXECUTE FUNCTION public.on_knowledge_insert();
```

### Debugging pg_net
```sql
-- Check if requests are being queued
SELECT * FROM net.http_request_queue;

-- Check responses (including errors)
SELECT * FROM net._http_response ORDER BY created DESC LIMIT 10;

-- Check postgres logs for trigger errors
-- (via Supabase dashboard: Logs → Postgres)
```

## Hermes Side: FastAPI Webhook Endpoint

### Route file: `routes/hooks.py`
Key design decisions:
- **Bearer auth** using constant-time comparison (`hmac.compare_digest`)
- **Deduplication:** checks if pointer already exists (prefix match) before appending
- **Search term derivation:** first 5 words of text + first tag, capped at 80 chars
- **Pointer format:** `knowledge.py search "term".  [auto]` — `[auto]` marks auto-generated vs human-written
- **Always returns 200** even on logic errors — never 5xx so Supabase doesn't retry-spam

### Auth middleware exemption
The webhook endpoint MUST be exempt from the session-cookie auth gate:
```python
_AUTH_EXEMPT = {
    "/api/auth/login", "/api/auth/logout", "/api/auth/check",
    "/api/health", "/api/hooks/knowledge",  # ← add this
    "/", "/index.html", "/favicon.ico"
}
```

### Systemd `.env` loading
The systemd unit needs `EnvironmentFile` to read the webhook secret:
```ini
# /etc/systemd/system/hermes-dashboard.service.d/dispatcher-override.conf
[Service]
Environment=HERMES_HOME=%h/.hermes
EnvironmentFile=%h/hermes-dispatcher/.env
Restart=always
```

### `.env` format
```
WEBHOOK_SECRET=<64-char hex>
```
Generated via `python3 -c "import secrets; print(secrets.token_hex(32))"`.

## Cloudflare Bot Fight Mode Gotcha

**Symptom:** pg_net requests return HTTP 403 with Cloudflare error code 1010.

**Root cause:** Supabase's pg_net worker makes outbound HTTP requests from datacenter IPs. Cloudflare's Bot Fight Mode blocks these.

**Fix in Cloudflare dashboard:**
1. Security → WAF → Custom Rules
2. Create rule: `(http.request.uri.path eq "/api/hooks/knowledge")`
3. Action: **Skip** → WAF components to skip: **Bot Fight Mode**

This lets Supabase through while keeping bot protection on everything else.

## Secret Rotation

When the webhook secret changes:
1. Update `~/hermes-dispatcher/.env`
2. Update the trigger function in Supabase (replace the hardcoded secret)
3. `systemctl restart hermes-dashboard`

The `ALTER DATABASE SET` approach was attempted but requires superuser — not available via the Supabase migration API. Hardcoding in the `SECURITY DEFINER` function body is the pragmatic approach (function bodies are only readable by superusers).

## End-to-End Test

```bash
# Direct HTTP test (bypasses Supabase)
curl -s -X POST http://localhost:8787/api/hooks/knowledge \
  -H "Authorization: Bearer $WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"id":999,"text":"Test webhook append","tags":["test"],"source":"test","priority":"1"}'

# Check MEMORY.md
tail -3 ~/.hermes/memories/MEMORY.md
# Should show: knowledge.py search "Test webhook append".  [auto]
```

## Files Involved

| File | Purpose |
|---|---|
| `~/hermes-dispatcher/routes/hooks.py` | Webhook receiver endpoint |
| `~/hermes-dispatcher/server.py` | Route registration + auth exempt |
| `~/hermes-dispatcher/.env` | `WEBHOOK_SECRET` |
| `/etc/systemd/system/hermes-dashboard.service.d/dispatcher-override.conf` | Env loading |
| Supabase `public.on_knowledge_insert()` | Trigger function |
| Supabase `knowledge_insert_webhook` trigger | AFTER INSERT on `public.knowledge` |
