"""TypeSafe Jev client + every question and threshold the write-path judges use (store gate, supersede judge,
distill selector). Questions ARE the policy — review them here, not in the callers.

Callers must catch exceptions and fail open — a judge failure must never block a write.
WITHHOLD: text matching the work-tenant patterns is never sent to the vendor; callers treat it as "no verdict".
"""
import json
import os
import re
import urllib.request

MODEL = os.environ.get("JEV_MODEL", "jev-1.14.0")  # pinned: jev-latest moves between releases
URL = "https://api.typesafe.ai/v1/systemone"
# Privacy boundary with the vendor. JEV_WITHHOLD_PATTERN is a case-insensitive regex; any text matching it is never sent and the
# judge returns "no verdict" (caller keeps its local behaviour). Empty pattern or JEV_WITHHOLD=off = send everything.
# Typical contents: a private subnet prefix, employer/customer names, hostnames. Secrets are already masked before storage (secret_mask.py).
WITHHOLD = None  # tests may monkeypatch a compiled regex here; otherwise read from config at call time


def _withhold_regex():
    if WITHHOLD is not None:
        return WITHHOLD
    from knowledge.config import env  # os.environ first, then the Hermes dotenv (cron/kb-daemon have no EnvironmentFile)
    pat = env("JEV_WITHHOLD_PATTERN")
    return re.compile(pat, re.I) if pat else None


def withheld(*texts: str) -> bool:
    """True = do not send to the vendor."""
    from knowledge.config import env
    rx = _withhold_regex()
    if rx is None or env("JEV_WITHHOLD", "on") == "off":
        return False
    return any(rx.search(t or "") for t in texts)


def noul(instructions, true: str, false: str) -> dict:
    return {"type": "noul", "instructions": instructions, "criteria": {"true": true, "false": false}}


def ask(state, questions: dict, timeout: float = 20.0) -> dict:
    """One System One request; returns the `answers` dict keyed by question id."""
    from knowledge.config import env  # os.environ first, then the Hermes dotenv (kb-daemon has no EnvironmentFile)
    key = env("TYPESAFE_API_KEY")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY not set")
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["answers"]


# ── Store gate (heartbeat): state = the fact text. p_drop = 1 - noul.
# Threshold 0.70 = P>=0.95 operating point on the 352-row live-verified pool (2026-10-21: P 0.947, R 0.45). Do not tune on that pool.
STORE_GATE_THRESHOLD = 0.70
STORE_GATE_QUESTIONS = {"store_gate": noul(
    "Is this text a durable, specific, reusable fact about the operator's systems, tools, decisions, or preferences (yes) "
    "— or is it transient noise, raw CLI output, a session-only observation, or superseded information (no)?",
    "Durable and reusable: a specific fact about the operator's systems, tools, configuration, decisions, or preferences that is worth remembering.",
    "Transient or noise: CLI output, session chatter, ephemeral status, or information that is already superseded or will not be reused.")}

# ── Supersede judge (storage): state = {old_fact, new_fact}. Acts only when BOTH hold (same subject AND supersedes).
# Band and thresholds fixed before live data (pilot 2026-10-21: 6 pos median 0.81 / 6 random neg max 0.05).
SUPERSEDE_MIN_COS = float(os.environ.get("SUPERSEDE_JUDGE_MIN_COS", "0.80"))  # below this the rows are not neighbours
SUPERSEDE_THRESHOLD = float(os.environ.get("SUPERSEDE_JUDGE_THRESHOLD", "0.75"))
SUPERSEDE_QUESTIONS = {
    "same_subject": noul("Are `old_fact` and `new_fact` about the same subject?",
                         "Same system, setting, person, decision or thing.", "Different subjects, or only shared vocabulary."),
    "supersedes": noul(
        "Does `new_fact` supersede `old_fact`: same subject, and `new_fact` gives a newer or corrected state such that `old_fact` is now stale and should be deleted?",
        "Same subject; new_fact updates, corrects, or replaces what old_fact states.",
        "Different subject, or both can be true at once, or old_fact adds information new_fact lacks."),
}

# ── Distill selector (session_distill): state = {session_title}; the item sits in the instructions.
# Measured 2026-10-21 on 10 probe items: this framing separates junk (<=0.15) from records (>=0.59). Items in state referenced by
# `entries[i]` paths did NOT (FINISHED 0.78, a real decision 0.11); "from this session" wording scored everything < 0.25.
DISTILL_THRESHOLD = float(os.environ.get("DISTILL_JUDGE_THRESHOLD", "0.5"))
DISTILL_TRUE = "A specific statement: a reader learns what was decided, produced, ruled out or asked without seeing the rest of the conversation."
DISTILL_FALSE = ("Narration of what the assistant is about to do, a bare status word, a fragment, generic how-to advice, "
                 "or text that needs the surrounding reply to mean anything.")


def distill_question(item: str) -> dict:
    return noul({"text": item, "question": "Is this text a specific, self-contained record of a decision, outcome, skipped item, or instruction?"},
                DISTILL_TRUE, DISTILL_FALSE)


# ── Reranker (search._jev_rerank, RERANK_BACKEND=jev): state = {query}; one noul per candidate, passage in the instructions.
# Benchmarked 2026-10-21 vs bge-reranker-v2-m3 on the 62-query KB set: MRR 0.944 vs 0.904 (7 wins / 1 loss / 50 ties), S@8 1.0,
# abstention 4/4 via rerank_raw = logit(noul), rerank step p50 0.25 s vs 0.49 s. Lookups touching work-tenant text stay on bge.
RERANK_TIMEOUT_S = 5  # same budget as the bge call; a slow vendor degrades to bge, not to a stalled turn


def rerank_question(passage: str) -> dict:
    return noul({"question": "Does the passage contain the information the query is asking for?", "passage": passage},
                "The passage directly states or answers what the query asks.",
                "The passage is about something else, or only shares vocabulary with the query.")


# ── Retrieval-quality judge (eval_recall --judge): state = {question}; the recalled block sits in the instructions.
# Offline eval only, never on the serving path. Independent of the production reranker (bge) by design.
def recall_judge_question(block: str) -> dict:
    return noul({"recalled_notes": block[:6000], "question": "Do the recalled notes contain the information needed to answer the question?"},
                "At least one note directly states the fact, decision or value the question asks for.",
                "The notes are about something else, only share vocabulary with the question, or are empty.")


# ── Sector labeler: walk knowledge/taxonomy.json one level per choice call; stop at "other" or low confidence.
# Partial depth is a valid label (e.g. ["infrastructure"]). Replaces the centroid classifier (65% abstention).
SECTOR_MIN_CONF = 0.60
_TAXONOMY = None


def _taxonomy():
    global _TAXONOMY
    if _TAXONOMY is None:
        import os as _os
        with open(_os.path.join(_os.path.dirname(__file__), "taxonomy.json")) as f:
            _TAXONOMY = json.load(f)["nodes"]
    return _TAXONOMY


def _children(path):
    return [n for n in _taxonomy() if n["path"][:-1] == path and len(n["path"]) == len(path) + 1]


def classify_sector(text: str, min_conf: float = SECTOR_MIN_CONF):
    """Return (sector_path list, confidence of the last accepted level). ([], 0.0) = abstain. Raises on vendor error."""
    path, conf = [], 0.0
    while True:
        kids = _children(path)
        if not kids:
            return path, conf
        criteria = {n["path"][-1]: n["description"][:300] for n in kids}
        criteria["other"] = "None of the above fits, or the text is too vague to tell"
        where = f" within {'/'.join(path)}" if path else ""
        a = ask(text[:1500], {"sector": {"type": "choice", "criteria": criteria,
                                          "instructions": f"Which topic area{where} does this fact belong to?"}})["sector"]
        if a["choice"] == "other" or a["confidence"] < min_conf:
            return path, conf
        path, conf = path + [a["choice"]], a["confidence"]
