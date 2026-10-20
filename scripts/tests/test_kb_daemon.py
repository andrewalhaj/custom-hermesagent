"""kb_daemon: bounded request line, clamped top_k."""
import json
import os
import socket
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import kb_daemon


def _roundtrip(payload):
    a, b = socket.socketpair()
    a.sendall(payload)
    kb_daemon.handle_connection(b)
    a.settimeout(2)
    return json.loads(a.recv(65536))


def test_oversized_line_rejected():
    resp = _roundtrip(b'x' * (kb_daemon.MAX_LINE + 10))
    assert resp['ok'] is False and 'too long' in resp['error']


def test_top_k_clamped(monkeypatch):
    seen = []
    class E:
        search = staticmethod(lambda **kw: seen.append(kw['top_k']) or [])
    monkeypatch.setattr(kb_daemon, '_engine', E)
    for k, want in ((10**9, 50), (-5, 1), (0, 1)):
        assert _roundtrip(json.dumps({'query': 'q', 'top_k': k}).encode() + b'\n')['ok']
        assert seen[-1] == want


def test_recv_times_out(monkeypatch):
    monkeypatch.setattr(kb_daemon, 'RECV_TIMEOUT', 0.2)
    a, b = socket.socketpair()
    kb_daemon.handle_connection(b)   # peer never sends; must return, not hang
    a.settimeout(2)
    assert json.loads(a.recv(65536))['ok'] is False
