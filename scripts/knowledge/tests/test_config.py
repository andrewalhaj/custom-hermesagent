"""knowledge.config: os.environ wins, ~/.hermes/.env is the fallback (the gateway may start
without .env loaded), and flags are on only for the exact string 'true'.

Run:  python3 -m pytest scripts/knowledge/tests/test_config.py -v
"""
import os
import sys

_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from knowledge import config


def test_env_prefers_process_env_then_dotenv_then_default(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# comment\nK_BOTH="from-file"\nK_FILE=\'file-only\'\n')
    monkeypatch.setattr(config, "ENV_FILE", str(f))
    monkeypatch.setenv("K_BOTH", "from-env")
    monkeypatch.delenv("K_FILE", raising=False)
    monkeypatch.delenv("K_NONE", raising=False)
    assert config.env("K_BOTH") == "from-env"
    assert config.env("K_FILE") == "file-only"
    assert config.env("K_NONE", "dflt") == "dflt"


def test_flag_is_exact_true_and_read_at_call_time(monkeypatch):
    monkeypatch.setenv("RERANK", "true")
    assert config.flag("RERANK")
    monkeypatch.setenv("RERANK", "1")
    assert not config.flag("RERANK")
    monkeypatch.delenv("RERANK")
    assert not config.flag("RERANK")
