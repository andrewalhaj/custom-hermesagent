"""Learned store gate (shadow mode): p_drop per fact string from a ModernBERT classifier.

Callers must catch exceptions — a gate failure must never break the caller.
"""
import os

THRESHOLD = 0.802  # calibrated on the sealed test; do not tune
_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
MODEL_DIR = os.environ.get("STORE_GATE_MODEL_DIR", os.path.join(_HOME, "models", "store-gate-modernbert-seed0"))

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


def score(texts: list[str]) -> list[float]:
    """p_drop for each text, one forward pass."""
    if not texts:
        return []
    import torch
    tok, model = _load()
    enc = tok(texts, truncation=True, max_length=512, padding=True, return_tensors="pt")
    with torch.no_grad():
        return torch.softmax(model(**enc).logits, dim=-1)[:, 1].tolist()
