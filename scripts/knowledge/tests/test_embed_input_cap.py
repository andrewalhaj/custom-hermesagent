import importlib, io, json, os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
embed_mod = importlib.import_module("knowledge.embed")


def test_remote_embed_caps_input_length(monkeypatch):
    sent = []

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        body = json.loads(req.data)
        sent.append(body["input"])
        return _Resp(json.dumps({"embeddings": [[0.0] for _ in body["input"]]}).encode())

    monkeypatch.setattr(embed_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(embed_mod, "EMBEDDING_API_URL", "http://x")
    out = embed_mod._embed_remote(["short", "x" * 50_000])
    assert len(out) == 2
    assert sent[0][0] == "short"
    assert len(sent[0][1]) == embed_mod.MAX_EMBED_CHARS
