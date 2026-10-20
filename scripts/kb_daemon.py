#!/usr/bin/env python3
"""KB warm-search daemon.

Loads the sentence-transformers model + Supabase client ONCE and serves
hybrid searches over a Unix-domain socket. Turns the cold-start
(torch import + model load + initial Supabase connect) into ~100ms per query
for all CLI / cron callers. The in-gateway B-full path already keeps its own
warm engine; this daemon is the equivalent for out-of-process callers.

Protocol: newline-delimited JSON, one request + one response per connection.
  request:  {"query": "...", "top_k": 5, "tag_filter": null, "min_priority": null}\n
  response: {"ok": true, "hits": [...]}\n   or   {"ok": false, "error": "..."}\n

Robustness: a single bad request never crashes the daemon. SIGTERM/SIGINT
unlink the socket and exit cleanly. If a live daemon already owns the socket,
this process exits 0 ("already running"); a stale socket is reclaimed.
"""
import socket
import os
import sys
import json
import signal
import threading
import time

SOCKET_PATH = os.path.expanduser('~/.hermes/run/kb.sock')
SCRIPTS_DIR = os.path.expanduser('~/.hermes/scripts')
RUN_DIR = os.path.dirname(SOCKET_PATH)
MAX_LINE = 64 * 1024     # request line cap, bytes
RECV_TIMEOUT = 5.0       # total seconds a client gets to send its request line
MAX_TOP_K = 50
_slots = threading.BoundedSemaphore(16)   # concurrent handler threads

# Loaded once in warm_up(), read by every connection handler.
_engine = None
_server_socket = None


def _eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def warm_up():
    """Import the knowledge package and force model + DB load via a throwaway search.

    The July 9 restructure split the monolithic knowledge.py (LanceDB) into the
    knowledge/ package (Supabase pgvector). Load the package BY NAME so we serve
    the authoritative in-process path — loading the legacy monolith by file path
    now dies on `import lancedb` (uninstalled), which is exactly what left this
    daemon serving an empty index for 48 days. WorkingDirectory is ~/.hermes,
    not scripts/, so put scripts/ on sys.path before importing.
    """
    global _engine
    t0 = time.time()
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    import knowledge as mod
    mod.get_model()                 # force SentenceTransformer load
    mod.search('warmup', top_k=1)   # warm the Supabase match_knowledge RPC path + one query
    _engine = mod
    _eprint(f"[kb_daemon] ready — loaded in {time.time() - t0:.2f}s, listening on {SOCKET_PATH}")


def _recv_line(conn):
    """Read until newline (or EOF). Returns the decoded line without the newline.

    Raises ValueError past MAX_LINE bytes, socket.timeout past RECV_TIMEOUT in total.
    """
    deadline = time.monotonic() + RECV_TIMEOUT
    chunks, size = [], 0
    while True:
        conn.settimeout(max(deadline - time.monotonic(), 0.001))
        b = conn.recv(4096)
        if not b:
            break
        chunks.append(b)
        size += len(b)
        if size > MAX_LINE:
            raise ValueError('request too long')
        if b.endswith(b'\n'):
            break
    return b''.join(chunks).decode('utf-8').strip()


def handle_connection(conn):
    try:
        raw = _recv_line(conn)
        if not raw:
            return
        params = json.loads(raw)
        # Whitelist the accepted kwargs so a malformed request can't reach
        # search() with junk keys.
        kwargs = {
            'query': params['query'],
            'top_k': max(1, min(int(params.get('top_k', 5)), MAX_TOP_K)),
            'tag_filter': params.get('tag_filter'),
            'min_priority': params.get('min_priority'),
        }
        hits = _engine.search(**kwargs)
        resp = {'ok': True, 'hits': hits}
    except Exception as e:  # one bad request must never take down the daemon
        resp = {'ok': False, 'error': f"{type(e).__name__}: {e}"}
    try:
        conn.sendall((json.dumps(resp) + '\n').encode('utf-8'))
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _serve(conn):
    try:
        handle_connection(conn)
    finally:
        _slots.release()


def _cleanup(*_):
    try:
        if _server_socket is not None:
            _server_socket.close()
    except Exception:
        pass
    try:
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
    except Exception:
        pass
    _eprint("[kb_daemon] shut down")
    sys.exit(0)


def _already_running():
    """True if a live daemon answers on the socket. Reclaims a stale socket."""
    if not os.path.exists(SOCKET_PATH):
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(2.0)
    try:
        probe.connect(SOCKET_PATH)
        return True            # someone is listening
    except (ConnectionRefusedError, socket.timeout, OSError):
        try:
            os.unlink(SOCKET_PATH)   # stale — reclaim it
        except OSError:
            pass
        return False
    finally:
        probe.close()


def main():
    global _server_socket
    os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)

    if _already_running():
        _eprint("[kb_daemon] already running — exiting")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    _server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    _server_socket.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o600)
    _server_socket.listen(8)

    warm_up()   # pay the cold-start once, before serving

    while True:
        try:
            conn, _ = _server_socket.accept()
        except OSError:
            break   # socket closed during shutdown
        _slots.acquire()   # at the cap, new clients wait in the listen backlog
        threading.Thread(target=_serve, args=(conn,), daemon=True).start()


if __name__ == '__main__':
    main()
