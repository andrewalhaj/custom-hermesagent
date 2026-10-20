import pytest
import importlib, os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
embed_mod = importlib.import_module("knowledge.embed")  # module, not the shadowing function


def test_remote_failure_raises_and_never_uses_local_model(monkeypatch):
    monkeypatch.setattr(embed_mod, "EMBEDDING_API_URL", "http://x")
    monkeypatch.setattr(embed_mod, "_embed_remote", lambda t: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(embed_mod, "get_model", lambda: pytest.fail("local fallback used"))
    embed_mod._EMBED_CACHE.clear()
    with pytest.raises(RuntimeError, match="remote embed failed"):
        embed_mod.embed(["unique-text-for-no-fallback-test"])
    assert not embed_mod._EMBED_CACHE
