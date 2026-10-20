# Idempotent Standalone Scripts (Port-Bind Check)

When writing standalone Python scripts that bind a port (webhook listeners, push
servers, mini-HTTP endpoints), always check port availability before binding.
If the port is already occupied, exit silently. This prevents noisy background
process failure notifications in Hermes chat when a second instance is spawned.

## Pattern

```python
if __name__ == "__main__":
    import socket, sys
    LISTEN = ("127.0.0.1", 5052)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(LISTEN)
        s.close()
    except OSError:
        s.close()
        sys.exit(0)  # already running, silent exit

    print(f"listening on {LISTEN[0]}:{LISTEN[1]}")
    srv = HTTPServer(LISTEN, Handler)
    srv.serve_forever()
```

Key points:
- Bind a throwaway socket to check port availability, close it, then bind the real server
- `sys.exit(0)` — silent, no error. The existing instance is handling traffic
- Do this BEFORE the `print("listening...")` line so nothing is emitted when skipping
- Works for any Python HTTP server (http.server, Flask, FastAPI with uvicorn in __main__)

## Real example from this session

Two scripts — `gcal_push.py` and `gcal_webhook.py` — both listen on port 5052.
HAJarvis spawned background copies that collided with the running instance,
producing `OSError: Address already in use` tracebacks and noisy Telegram notifications.
Patched both with the pattern above. Now they exit silently if port 5052 is occupied.

## Related

- Also ran into `delegation-checkpoint ModuleNotFoundError` noise on these scripts —
  the sitecustomize hook only works inside the Hermes venv. For standalone system-Python
  scripts, the hook fires but can't find its modules. The noise is benign and the
  scripts still run. If it becomes a real problem, export `HERMES_DELEG_CHECKPOINT=off`.
