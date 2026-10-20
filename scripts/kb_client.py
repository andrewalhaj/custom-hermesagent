#!/usr/bin/env python3
"""Thin client for the KB warm-search daemon (kb_daemon.py).

`daemon_search(...)` returns the hits list, or raises DaemonUnavailable on ANY
failure so the caller can fall back to in-process search. The daemon is pure
acceleration — never a hard dependency.
"""
import socket
import json
import os
import sys

SOCKET_PATH = os.path.expanduser('~/.hermes/run/kb.sock')


class DaemonUnavailable(Exception):
    """Raised when the daemon can't be reached or returns an error."""
    pass


def daemon_search(query, top_k=8, tag_filter=None, min_priority=None, timeout=3.0):
    req = json.dumps({
        'query': query,
        'top_k': top_k,
        'tag_filter': tag_filter,
        'min_priority': min_priority,
    }) + '\n'
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(SOCKET_PATH)
            sock.sendall(req.encode('utf-8'))
            chunks = []
            while True:
                b = sock.recv(65536)
                if not b:
                    break
                chunks.append(b)
                if b.endswith(b'\n'):
                    break
        raw = b''.join(chunks).decode('utf-8').strip()
    except (OSError, socket.timeout) as e:
        raise DaemonUnavailable(f"connect/io failed: {e}") from None

    if not raw:
        raise DaemonUnavailable("empty response")
    try:
        resp = json.loads(raw)
    except json.JSONDecodeError as e:
        raise DaemonUnavailable(f"malformed response: {e}") from None

    if not resp.get('ok'):
        raise DaemonUnavailable(f"daemon error: {resp.get('error', 'unknown')}")
    return resp.get('hits', [])


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("usage: python3 kb_client.py 'your query'", file=sys.stderr)
        sys.exit(2)
    try:
        for hit in daemon_search(sys.argv[1]):
            score = hit.get('score', 0)
            text = str(hit.get('text', ''))[:100].replace('\n', ' ')
            print(f"[{score:.3f}] {text}  id={hit.get('id')}")
    except DaemonUnavailable as e:
        print(f"DAEMON UNAVAILABLE: {e}", file=sys.stderr)
        sys.exit(1)
