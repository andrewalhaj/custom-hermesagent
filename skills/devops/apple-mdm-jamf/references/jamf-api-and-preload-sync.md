# Jamf API auth + SharePoint→Preload sync (built 2026-07-22)

Context: The previous employer tracks IT purchases in a **SharePoint List** ("IT Purchasing" — Monday-lookalike UI, easy to misidentify). Goal: serial + asset tag entered at receiving → auto-created Jamf Inventory Preload record → Mac enrolls pre-tagged.

## Jamf API client (one-time)

1. Settings → **API Roles and Clients**
2. API Role `inventory-preload-writer`: privileges `Read/Create/Update Inventory Preload Records`
3. API Client `sharepoint-preload-sync` → assign role → **ENABLE the client** (new clients start disabled — the #1 cause of 401s) → generate secret (shown once)

## Token flow (client credentials, ~20-min tokens)

```
POST https://<org>.jamfcloud.com/api/oauth/token
Content-Type: application/x-www-form-urlencoded
grant_type=client_credentials&client_id=<ID>&client_secret=<SECRET>
```
Response: `{"access_token":"...","expires_in":1199}`. Use as `Authorization: Bearer <token>` (note the space). Request fresh per flow run — don't cache.

Preload write:
```
POST https://<org>.jamfcloud.com/api/v2/inventory-preload/records
{"serialNumber":"<trimmed>","deviceType":"Computer","assetTag":"<trimmed>"}
```
- 201 created · 401 bad/malformed bearer or missing privilege · 409 record exists (make idempotent: GET `?filter=serialNumber=="<sn>"` then PUT `/records/{id}`) · 400 usually whitespace in serial — always `trim()`.

## Power Automate flow spec

Trigger: SharePoint *When an item is created or modified* on the list.
Condition (ALL): Serial Number ≠ blank, Asset Tag ≠ blank, **Jamf Sync Status ≠ "Synced"** — the third guard prevents the write-back from re-triggering an infinite loop. If-no → Terminate (Succeeded).
Then: HTTP token call → Parse JSON (`{"type":"object","properties":{"access_token":{"type":"string"}}}`) → HTTP preload POST → Update item (Jamf Sync Status = Synced). Parallel *Mark Error* Update-item with run-after = "has failed" on the POST, so failures surface in the list not just run history.

Gotchas:
- Plain **HTTP action is Premium** — one Premium license on the flow owner (or 90-day trial to build). SharePoint/Excel connectors are standard.
- SharePoint **internal column names** differ from display names (`Serial_x0020_Number`) — pick fields from the dynamic-content panel, never hand-type; verify via List settings → column URL `&Field=`.
- Choice columns appear in dynamic content as `<name> Value` — that's the one to use.
- **Code view is read-only in Power Automate** (unlike Logic Apps) — flows are built in the designer; JSON can only enter via clipboard-paste of individual actions, and connector actions (SharePoint) lose connection references on paste. Build connector steps by hand.
- New-designer condition: blank comparison = leave right box empty (or `null` expression if validation objects).

## Smoke-test pattern (do this BEFORE wiring the real trigger)

Instant flow, manual trigger:
1. Token HTTP call alone → run → Body contains `access_token`? Isolates creds/enable-toggle problems (where most first-run failures live).
2. Add preload POST with **hardcoded** test body (`C02TEST12345` / `TEST-001`) → 201 + record visible in Settings → Global → Inventory Preload.
3. Delete test record (API/CSV — UI has no row delete), keep the flow renamed "Jamf API smoke test" as a permanent health probe (useful after secret rotations).

Also valid: curl / `Invoke-RestMethod` from any machine to pre-verify creds outside Power Automate.

## Backfill + master-CSV workflow (no-code operating model)

- Jamf export → preload CSV transform: keep `Serial Number`, add `Device Type=Computer`, `Asset Tag`. Their computer-name convention embeds serial + username (`nprimo-G2RPH6GM0X`), so usernames are parseable from names when the export lacks them — but per the stale-user hazard, **ship preload tags-only**.
- Master CSV lives in SharePoint; edits = fill cells + re-upload (idempotent). Flow takes over writes for new purchases; the 164-Mac baseline (2026-07) was uploaded tags-blank purely to seed serials.
- File delivery to Andrew: litterbox upload + verify with download-back `diff` before sending the link.

## Related integration options (discussed, not built)

- Reverse sync: Jamf enrollment webhook → Power Automate "When an HTTP request is received" → set an "Enrolled ✓" column on the purchase row.
- Entra offboarding trigger → blank the leaver's preload user fields (only needed if user fields ever get preloaded).
- Open-source middleware alternatives (n8n, Activepieces, Snipe-IT for asset truth) are the fallback when no M365 Power Platform licensing exists — with Power Automate available, it wins on governance/zero-hosting.
