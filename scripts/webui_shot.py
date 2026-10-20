#!/usr/bin/env python3
"""Screenshot a staging WebUI panel via CDP. Usage:
  shot.py <url> <out.png> [js_to_eval]
Static staging server (:8788) needs no auth. Live (:8787) needs cookie (not handled here)."""
import os, sys, json, time, base64, urllib.request, websocket

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788/index.html"
out = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/shot.png")
js  = sys.argv[3] if len(sys.argv) > 3 else ""

tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json/list"))
page = next((t for t in tabs if t.get("type") == "page"), tabs[0])
ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20,
                                 origin="http://127.0.0.1:9222")
_id = 0
def send(method, params=None):
    global _id; _id += 1
    ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == _id: return r

send("Page.enable")
send("Runtime.enable")
send("Page.navigate", {"url": url})
time.sleep(3.5)
if js:
    send("Runtime.evaluate", {"expression": js})
    time.sleep(1.5)
res = send("Page.captureScreenshot", {"format": "png"})
data = res.get("result", {}).get("data", "")
if data:
    with open(out, "wb") as f:
        f.write(base64.b64decode(data))
    print(f"OK {out} ({len(data)} b64 chars)")
else:
    print("FAIL", json.dumps(res)[:300])
ws.close()
