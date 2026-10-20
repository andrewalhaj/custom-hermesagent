"""Durable-fact extraction from session text: extract_facts() -> [(fact, tag)].

Used by session_heartbeat, which does the dedup and the store. Every fact must match a category
below AND pass junk_filter._is_noise(); a missed fact is cheaper than a polluted store.
"""

import re

from hermes_env import env
from junk_filter import _is_noise

# ── Durable-fact category matchers ─────────────────────────────────────
# Each operates on a single clean sentence. Sub-span matchers (config, infra)
# construct a fact string; whole-sentence matchers accept the sentence as-is.

# CONFIG: key = value / key → value / key set to value, where key is a real
# identifier (dotted or underscored preferred) and value is a literal token.
CONFIG_PATTERN = re.compile(
    r"\b([A-Za-z_][\w.]{2,}?)\s*(?:=|→|->|\bset\s+to\b)\s*([\w\-./:@]{2,})\b"
)

# INFRA identity/assignment. No IGNORECASE — the LHS must be an actual host
# token or an ALL-CAPS identifier, never a lowercase prose word (that was the
# bug that turned "conf" / "js" into "facts"). Only '=' , not ':'.
INFRA_ASSIGNMENT = re.compile(
    r"\b(" + (env("KB_HOST_NAME_PATTERN") or r"[A-Z]{2,4}-\d+") + r"|Mac\s+(?:Mini|Studio)|[A-Z][A-Z0-9_]{2,})\s*=\s*"
    r"([^\s=`][^\n`]{4,80})"
)

# Bare IPv4 (used to promote a clean sentence that mentions an IP → infra).
_IP_ADDRESS = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

# HOST/PATH mapping: a clean sentence tying a host/service to a filesystem path
# or deploy location.
HOSTPATH_FACTS = re.compile(
    r"(?i)\b(?:deployed?\s+(?:to|at|from)|lives?\s+(?:in|at)|located\s+(?:in|at)|"
    r"path\s+is|installed\s+(?:in|at|to)|mounted\s+at|served\s+from)\b"
)

# DIAGNOSTIC: root-cause / calibration statements.
DIAGNOSTIC_FACTS = re.compile(
    r"(?i)\b(?:root\s+cause|the\s+(?:issue|problem|bug)\s+(?:is|was)|"
    r"broken\s+because|caused\s+by|scores?\s+max\s+at|floor\s+is|"
    r"threshold\s+is|hardcoded|miscalibrated|regression\s+(?:from|in))\b"
)

# ARCHITECTURE: hosting / deployment / port topology.
ARCHITECTURE_FACTS = re.compile(
    r"(?i)\b(?:runs?\s+on|hosted\s+on|deployed\s+at|served\s+from|"
    r"listens?\s+on\s+port|exposed\s+at|proxied\s+(?:by|through)|"
    r"backed\s+by|reads?\s+from|writes?\s+to)\b"
)

# DECISION: an explicit, settled choice (not "I'll..." narration — that's
# stripped by _is_noise).
DECISION_FACTS = re.compile(
    r"(?i)^(?:Decision|Decided|Chose|Selected|Opted|Went\s+with|Picked|"
    r"Settled\s+on|Verdict|Ruled\s+out|Rejected)\b"
)

# CREDENTIAL events: rotation / revocation of secrets (durable security facts).
CREDENTIAL_FACTS = re.compile(
    r"(?i)\b(?:rotated|regenerated|revoked|reissued|reset|invalidated)\s+"
    r"(?:the\s+|a\s+|an\s+)?(?:\w+\s+){0,2}?"
    r"(?:token|key|secret|credential|password|api\s+key|cert(?:ificate)?)s?\b"
)

# PREFERENCE: stable user/system preferences.
PREFERENCE_FACTS = re.compile(
    r"(?i)^(?:Prefers?|Expects?|Wants?|Always|Never|User\s+prefers?|"
    r"User\s+wants?|User\s+expects?)\b"
)

def _segment(text):
    """Split raw multi-message text into clean candidate sentences.

    Split on newlines FIRST (kills cross-line table/narration merges and the
    filename→next-line gluing that produced fragments), drop obvious markdown
    scaffolding lines, then split each line into sentences.
    """
    out = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        # drop markdown scaffolding lines wholesale
        if line[0] in "#>-*|" or line.startswith("```") or line.startswith("| "):
            continue
        for sent in re.split(r"(?<=[.!?])\s+", line):
            sent = sent.strip()
            if sent:
                out.append(sent)
    return out


def extract_facts(text):
    """Run all heuristics and return deduplicated (fact, tag) factoids.

    Contract preserved: returns a list of (fact:str, tag:str) tuples, each
    fact >= MIN_FACT_LENGTH characters and passing the _is_noise() gate.
    """
    facts = []
    seen = set()

    def add(fact, tag):
        fact = (fact or "").strip()
        key = fact.lower()
        if key in seen:
            return
        if _is_noise(fact):
            return
        seen.add(key)
        facts.append((fact, tag))

    for s in _segment(text):
        # Skip anything the gate rejects before doing category work — this also
        # means whole-sentence categories only ever see clean sentences.
        clean = not _is_noise(s)

        # CONFIG (sub-span) — construct "Config: key = val". Run even on a
        # sentence that itself is clean; guard the key so we don't emit prose.
        if clean:
            for m in CONFIG_PATTERN.finditer(s):
                key, val = m.group(1).strip(), m.group(2).strip()
                # require a config-shaped key: dotted, underscored, or ALLCAPS
                if ("." in key or "_" in key or key.isupper()) and len(val) >= 2:
                    add(f"Config: {key} = {val}", "config")

            # INFRA identity assignment (sub-span)
            for m in INFRA_ASSIGNMENT.finditer(s):
                add(m.group(0).strip(), "infra")

            # Whole-sentence promotions (sentence already clean). Architecture
            # is checked before the host/path infra promotion because "runs on"
            # / "served from" are more specifically architectural than a bare
            # host/path mapping; first tag wins on dedup.
            if ARCHITECTURE_FACTS.search(s):
                add(s, "architecture")
            if _IP_ADDRESS.search(s):
                add(s, "infra")
            if HOSTPATH_FACTS.search(s):
                add(s, "infra")
            if DIAGNOSTIC_FACTS.search(s):
                add(s, "diagnostic")
            if DECISION_FACTS.match(s):
                add(s, "decision")
            if CREDENTIAL_FACTS.search(s):
                add(s, "credential")
            if PREFERENCE_FACTS.match(s):
                add(s, "preference")

    return facts
