"""Store gate (shadow mode): p_drop per fact string.

Backends (STORE_GATE_BACKEND): "local" = the ModernBERT classifier (default); "jev" = TypeSafe Jev zero-shot
(noul store_gate question, p_drop = 1 - noul; model pinned in knowledge/jev.py). Callers must catch exceptions — a gate failure must never break the caller.
"""
import json
import os
import subprocess
import sys


def _backend() -> str:
    try:
        from knowledge.config import env  # os.environ first, then .env (cron has no EnvironmentFile)
        b = env("STORE_GATE_BACKEND", "local")
    except ImportError:  # the scorer child runs this file as a script with only scripts/knowledge on sys.path
        b = os.environ.get("STORE_GATE_BACKEND", "local")
    return b if b in ("local", "jev") else "local"


BACKEND = _backend()
# local: calibrated on the sealed test; jev: knowledge/jev.py STORE_GATE_THRESHOLD (0.70). Do not tune.
THRESHOLD = {"local": 0.802, "jev": 0.70}[BACKEND]
_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
MODEL_DIR = os.environ.get("STORE_GATE_MODEL_DIR", os.path.join(_HOME, "models", "store-gate-modernbert-seed0"))

# the caller (cron engine runtime) has no torch; the model runs in a child under this interpreter
SCORER_PYTHON = os.environ.get("STORE_GATE_PYTHON", os.path.join(_HOME, "cache", "laya-exp", "venv", "bin", "python"))
TIMEOUT_S = 60

_loaded = None


def _load():
    global _loaded
    if _loaded is None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(4)  # leave the Mini's other cores alone
        tok = AutoTokenizer.from_pretrained(MODEL_DIR)
        model = AutoModelForSequenceClassification.from_pretrained(MODEL_DIR).eval()
        _loaded = (tok, model)
    return _loaded


def _score_local(texts: list[str]) -> list[float]:
    import torch
    tok, model = _load()
    enc = tok(texts, truncation=True, max_length=512, padding=True, return_tensors="pt")
    with torch.no_grad():
        return torch.softmax(model(**enc).logits, dim=-1)[:, 1].tolist()


def _score_jev(texts: list[str]) -> list[float]:
    out = []
    for text in texts:  # shortcut: one request per fact at 1-5 facts per heartbeat; batch via fan-out if volume grows
        from knowledge import jev
        if jev.withheld(text):
            out.append(0.0)  # never sent to the vendor; no verdict = keep
            continue
        out.append(1.0 - float(jev.ask(text, jev.STORE_GATE_QUESTIONS, timeout=TIMEOUT_S)["store_gate"]["noul"]))
    return out


def score(texts: list[str]) -> list[float]:
    """p_drop for each text; local = one forward pass in a child process that has torch."""
    if not texts:
        return []
    if BACKEND == "jev":
        return _score_jev(texts)
    # shortcut: one child (and ~5 s model load) per call, fine at 1-5 facts per heartbeat; use a persistent scorer when volume grows
    proc = subprocess.run([SCORER_PYTHON, os.path.abspath(__file__)], input="\n".join(json.dumps(t) for t in texts),
                          capture_output=True, text=True, timeout=TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"store gate scorer exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    try:
        out = json.loads(proc.stdout)
        if len(out) == len(texts):
            return [float(x) for x in out]
    except (ValueError, TypeError):
        pass
    raise RuntimeError(f"store gate scorer returned bad output: {proc.stdout[:200]!r}")


if __name__ == "__main__":
    sys.path.pop(0)  # script dir shadows stdlib/site names (search.py, classify.py ...)
    print(json.dumps(_score_local([json.loads(l) for l in sys.stdin.read().splitlines() if l.strip()])))
