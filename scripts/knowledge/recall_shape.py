"""Single source for "is this user message recall-shaped?" (recall_gate guard, supabase prefetch widening)."""
import re

# Conservative on purpose: past-tense / shared-history cues that mean "recall the past", never a plain "what is X" explain question.
_RECALL = re.compile(
    # status of the past: changes, leftovers, open items
    r"what\s+changed|what(?:(?:'|’)?s|\s+is)\s+(?:left|outstanding)\b(?!\s+(?:padding|margin|align|join))"
    r"|anything\s+(?:else\s+)?(?:left|from|we|stale|uncommitted|outstanding)\b|\bstill\s+(?:stale|open|uncommitted|left)\b"
    # past events and outcomes
    r"|\bwhat\s+(?:did|was|were|happened)\b|\b(?:did|have|had)\s+we\b|\bdid\s+(?:the|it)\b"
    r"|\bwhy\s+(?:did|was|wasn't|wasn’t|can't|can’t)\b"
    # decisions and conclusions
    r"|\bverdict\b|\bconclu(?:de|ded|sion)\b|\bland(?:ed)?\s+on\b|\bdecided\b|\bcome\s+out\s+to\b"
    # where we stopped, what is planned
    r"|where\s+(?:were\s+we|did\s+we\s+leave)|\b(?:leave|left)\s+off\b|\bnow\s+that\b|\bplan\s+for\b|what\s+are\s+we\s+doing\s+with"
    # time markers
    r"|\blast\s+(?:night|week|time|session)\b|\bwhen\s+we\s+checked\b"
    # explicit recall requests
    r"|remind\s+me(?!\s+to\b)|catch\s+me\s+up|\brecap\b|what\s+about\s+the\b.*\bfrom\b"
    # vague back-references to a past run ("how come X didn't…", "did you spot…", "that long run")
    r"|\bhow\s+come\b|\bdid\s+you\s+(?:spot|find|notice|see|hit|check)\b|\bthat\s+(?:long\s+)?(?:run|session|turn|night)\b"
    # "how much ... actually" (asking for a previously established fact)
    r"|how\s+much\b.*\bactually\b",
    re.IGNORECASE)
_OVERRIDE = re.compile(r"NO SESSION SEARCHES|no lookup", re.IGNORECASE)  # Andrew's explicit skip


def is_recall(text: str) -> bool:
    return bool(_RECALL.search(text)) and not _OVERRIDE.search(text)
