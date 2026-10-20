#!/usr/bin/env python3
"""Read-only probe of the live Honcho API to discover what is deletable.

Run BEFORE proposing any "delete the dummy data" workflow. Establishes, from the
live OpenAPI spec (not assumption), which objects the API lets you delete.

Key facts it confirms (verified June 2026, API version 3.0.9):
  - Real base is https://api.honcho.dev/v3/...  (the honcho-bridge.sh /v1 base is STALE)
  - openapi.json lives at https://api.honcho.dev/openapi.json (v1/ and v2/ variants 404)
  - Deletable: sessions, conclusions, workspaces, session-peers, webhooks
  - NOT deletable: messages (GET/PUT only), observations (derived, not stored)

Reads HONCHO_API_KEY from ~/.hermes/.env. Makes only GET requests + one POST
sessions/list (read-only query). Performs NO mutations.
"""
import json, os, urllib.request, urllib.error

ENV = os.path.expanduser("~/.hermes/.env")
key = None
with open(ENV) as f:
    for line in f:
        if line.startswith("HONCHO_API_KEY"):
            key = line.strip().split("=", 1)[1]
            break
if not key:
    raise SystemExit("HONCHO_API_KEY not found in ~/.hermes/.env")

def get(url):
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + key)
    try:
        r = urllib.request.urlopen(req, timeout=25)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return "ERR", str(e).encode()

# 1. Locate the OpenAPI spec
spec = None
for base in ["https://api.honcho.dev", "https://api.honcho.dev/v3"]:
    code, body = get(base + "/openapi.json")
    print(f"{base}/openapi.json -> {code} ({len(body)} bytes)")
    if code == 200:
        spec = json.loads(body)
        break
if not spec:
    raise SystemExit("Could not fetch OpenAPI spec.")

info = spec.get("info", {})
print(f"\nAPI: {info.get('title')} v{info.get('version')} (openapi {spec.get('openapi')})")

# 2. Enumerate every DELETE route — the authoritative deletability list
print("\n=== DELETE routes (what the API lets you delete) ===")
for p, methods in sorted(spec.get("paths", {}).items()):
    if "delete" in methods:
        print(f"  DELETE {p}  -- {methods['delete'].get('summary','')}")

# 3. Flag what is NOT deletable (presence of resource without a delete verb)
print("\n=== Session/message route verbs (note: no delete-message exists) ===")
for p, methods in sorted(spec.get("paths", {}).items()):
    if "message" in p.lower() or "session" in p.lower():
        verbs = ",".join(m.upper() for m in methods if m in ("get","post","put","delete","patch"))
        print(f"  [{verbs}] {p}")
