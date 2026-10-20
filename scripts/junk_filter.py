"""Junk-fact filter: decides whether a candidate string is a durable, self-contained fact.

No knowledge-package import on purpose: plugins/supabase loads this inside the gateway, and a
broken knowledge import must not silently switch the filter off. Used by session_capture and
plugins/supabase._is_junk.
"""
import re

from secret_mask import mask_secrets

MIN_FACT_LENGTH = 50         # raised from 25: true durable facts are ≥50 chars;
                              # fragments like "Config: OOTB = continuity" (28 chars)
                              # and deictic references are shorter.
MAX_FACT_LENGTH = 400        # anything longer is a pasted blob, not a self-contained fact

# ── Noise gate ─────────────────────────────────────────────────────────
# A candidate string is REJECTED if any of these fire. Applied to every fact
# before it is emitted, and to every sentence before category matching.

# First/second-person conversational narration and filler. These are the turns
# that fragmented into noise before: "Let me...", "Now edit...", "I'll...".
_CONVERSATIONAL = re.compile(
    r"(?i)(?:^|\b)(?:"
    r"let me|let'?s|i'?ll|i will|i'?ve|i'?m|i need|i think|i'd|i can|i should|"
    r"i opened|i checked|i tried|i ran|i did|i saw|i wrote|i made|i decided|"
    r"i pulled|i patch|"
    r"we'?ll|we're|we've|we should|we need|we're told|"
    r"you can|you should|you'?ll|you'?re|you'?ve|your\b|here'?s|here is|"
    r"you want|say the word|if you want me to|expect the same|"
    r"now (?:edit|let|replace|add|run|the|i|get|do|check|verify)|"
    r"next (?:is|up|step)|hmm|okay|ok,|good,|great,|sure,|actually|wait,|"
    r"let me just|first,? let|then let"
    r")\b"
)

# Interrogative openers (questions are never durable facts).
_QUESTION_OPEN = re.compile(
    r"(?i)^(?:what|why|how|when|where|who|which|should|could|would|can|"
    r"do|does|did|is|are|was|were|will|shall|may)\b"
)

# Filename-split artifact: a bare lowercase token immediately followed by ':'
# at the very start (e.g. "conf:", "js:", "py:", "push:", "state:").
_FRAGMENT_COLON = re.compile(r"^[a-z0-9_./+-]{1,24}:")

# Transcript role tags leaking into content.
_ROLE_TAG = re.compile(r"(?i)\b(?:user|assistant|system)\s*:")


# ── Shared junk-fact filter (used by BOTH extract_facts() and the supabase plugin) ──
# This is the comprehensive, conservative filter that catches conversation fragments,
# deictic references, hedged/transient state, secrets-adjacent snippets, etc.
# False negatives (junk slips through) are better than false positives (real facts blocked).

# Deictic/anaphoric openers — "These are", "This is", "That is", "It lives/in/",
# "The fixes", "The check", "The pre-flight", "The Playwright", "Config:" standalone.
# These refer back to something said earlier and are meaningless in isolation.
_DEICTIC_OPENER = re.compile(
    r"(?i)^(?:"
    r"These\s+are|This\s+is|That\s+(?:is|tells)|Those\s+are|"
    r"It\s+(?:lives?|is|was|will|runs|works|sounds|looks|seems|has|does)|"
    r"The\s+(?:fix(?:es)?\s+(?:are|were|live)|check\s+(?:says|shows|is)|"
    r"pre-?flight|Playwright|"
    r"result\s+(?:is|was|shows)|answer\s+(?:is|was)|"
    r"output\s+(?:is|was|shows)|error\s+(?:is|was)|issue\s+(?:is|was))"
    r")\b"
)
# Truncated/clipped: starts mid-thought, no capitalized subject.
# E.g. "OOTB = continuity via delegation..." — no named subject.
_MID_THOUGHT_OPENER = re.compile(
    r"(?i)^(?:"
    r"OOTB|Config:\s+OOTB|So\s+(?:the|it|this|that|an?)\b|"
    r"And\s+(?:then|so|the|it)|But\s+(?:the|it|this)|"
    r"Or\s+(?:the|it)|"
    r"Via\s+|Through\s+|With\s+the|Just\s+"
    r")\b"
)
# Transient/hedged state markers — these describe temporary conditions,
# not durable facts worth storing.
_TRANSIENT_STATE = re.compile(
    r"(?i)"
    r"(?:"
    r"currently|for now|for the moment|for the time being|"
    r"as of now|as yet|so far|thus far|up to now|"
    r"temporarily|in the meantime|meanwhile|"
    r"already\s+has\s+a\s+value|"
    r"ending\s+\.\.\.|"
    r"still\s+(?:need|needs|waiting|pending|unresolved|broken)|"
    r"not\s+(?:yet\s+)?(?:sure|certain|confirmed|finalized)|"
    r"\d+%\s*dead"
    r")"
)
# Secrets-adjacent snippets: mention of secret values being shown, truncated,
# or assigned — NOT just the variable name (which is legitimate knowledge).
_SECRETS_ADJACENT = re.compile(
    r"(?i)"
    r"(?:"
    r"\b(?:password|api[_-]?key|secret[_-]?key|access[_-]?token|"
    r"auth[_-]?token|bearer|private[_-]?key)"
    r"\s*[=:]\s*\S{4,}|"  # actual assignment with value (e.g. "password = abc123")
    r"\b(?:supabase[_-]?key|supabase[_-]?url|service[_-]?role[_-]?key)"
    r"\s*[=:]\s*\S{4,}|"  # same for Supabase-specific secrets with assignment
    r"(?:ending|is|:=)\s+\.\.\.\w+|"  # truncated token: "ending ...c19"
    r"\b\w*(?:SECRET|TOKEN|PASSWD|PASSWORD|API_?KEY|PRIVATE_?KEY)\w*\s*=\s*(?!\*{3,}|<)\S{4,}"  # generic env-var secret
    r")"
)
# Pattern for truncated tokens: "ending ...c19" or similar
_TRUNCATED_SECRET = re.compile(r"ending\s+\.\.\.\w+")
# Generic "value ending ..." pattern
_VALUE_ELLIPSIS = re.compile(r"\bvalue\s+ending\b.*\.\.\.")
# Conditional user-guidance: "If the/that/it ... then ..." mid-conversation instructions
_CONDITIONAL_GUIDANCE = re.compile(r"(?i)^If\s+(?:the|that|it)\b.*\bthen\b")
# Ephemeral URL announcement: "Live for/at ... http(s)://..."
_LIVE_URL = re.compile(r"(?i)^Live\s+(?:at|for)\b.*https?://")
# Bare shell/code fragment with no declarative prose sentence structure.
# Catches leaked shell variable assignments / command substitution
# (VAR=$(...)) and bare code-snippet lines that the session-heartbeat
# pipeline writes as "facts".  CRITICAL: must NOT fire on legitimate
# facts that embed shell commands, paths or env-var names inside a
# full declarative sentence (those have different opening structure).
_CODE_SNIPPET = re.compile(
    r"(?i)"
    r"^[A-Z_][A-Z0-9_]*\s*=\s*"         # starts with VARNAME =   (shell assignment)
    r"(?!'[^']*$)"                        # not a single-quoted string value
    r"\$?\("                              # followed by $( or just (
)

# ── 3rd-pass leak classes (added 2026-08-13, card t_cfb35dd1) ──────────────
# Four junk classes leaked is_junk_fact()=False across three sector-boost
# passes. Each pattern below is deliberately NARROW and, where a durable
# variant exists ("The"-openers, dated diagnostics, real host/path facts),
# gated on additional signals so real store facts still pass.

# Date anchor: durable diagnostic/history facts cite when something happened;
# transient mid-debug narration does not. Used to gate the diagnostic-narration
# and conditional-speculation rejectors so genuinely dated facts survive.
_HAS_DATE = re.compile(
    r"\b(?:19|20)\d{2}\b"                                # a year (1999, 2026)
    r"|\b\d{4}-\d{2}-\d{2}\b"                            # ISO date
    r"|\b\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?\b"           # 07-23, 8/13, 07/23/26
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d"
)
# Markdown emphasis (**bold** / __bold__). Durable store facts are plain prose;
# emphasis is a tell for copied mid-conversation narration. Used only in
# combination with a diagnostic-narration opener + missing date (never alone).
_MARKDOWN_BOLD = re.compile(r"\*\*[^*]+\*\*|__[^_]+__")
# Present-tense diagnostic NARRATION openers. Real diagnostic facts carry a
# date anchor; the transient mid-debug variety opens "Root cause ..." and leans
# on markdown emphasis to point at a component. Combined: opener + bold + no date.
_DIAG_NARRATION_OPENER = re.compile(
    r"(?i)^(?:Root\s+cause\b|Diagnosis\b|Turns\s+out\b|Reasoning\b|"
    r"The\s+(?:real\s+)?(?:culprit|problem|issue|bug)\s+(?:is|turns)\b)"
)
# Sentence-initial hypothetical speculation: "If <clause> ... would/could/might".
# Broader than _CONDITIONAL_GUIDANCE (which needs the/that/it + then); catches
# "If travel@ lives ... would be ...". Gated on no-dateline to stay narrow.
_IF_SPECULATION = re.compile(
    r"(?i)^If\b[^.?!]*\b(?:would|could|might|wouldn'?t|couldn'?t|'d)\b"
)
# Verb-initial past-participle fragment immediately followed by a parenthetical
# and no subject: "Pulled (via ...)", "Fetched (from ...)". A durable fact is
# Subject-Verb; a Verb-first "-ed (" opener is a deictic clip.
_VERB_INITIAL_FRAGMENT = re.compile(r"^[A-Z][a-z]{2,}ed\s+\(")
# Ephemeral editability working-note: "... any tweak (...) is a one-line edit
# + re-render", "one-line edit". Describes how trivial an edit is — a transient
# working note, not durable knowledge.
_EPHEMERAL_EDIT_NOTE = re.compile(
    r"(?i)\bone-line\s+edit\b"
    r"|\bany\s+(?:tweak|change|edit|adjustment)\b[^.?!]*\b(?:one-line|quick|simple|trivial)\b"
)
# Sequential-instruction and imperative-deictic openers flagged 2026-07-23:
# "Then <verb> ...", "Expect this ...", second-person "You don't ...".
_INSTRUCTION_OPENER = re.compile(
    r"(?i)^(?:Then\s+[a-z]+|Expect\s+this\b|You\s+do\s?n'?t\b|You\s+do\s+not\b)"
)

# ── Heartbeat leak classes (added 2026-08-14, card t_85377168) ─────────────
# Four classes leaked on session_heartbeat's first full post-fix run.

# 1. Deictic narration about a live conversation: "The X lives/sits/is in
#    a/the/this/that session ...". Present-tense claim about ephemeral session
#    state — meaningless once the session is gone. NARROW: requires the literal
#    word "session" as the location, so real host/path facts ("The dispatcher
#    config lives at ~/...") are untouched.
_SESSION_DEIXIS = re.compile(
    r"(?i)^The\s+\w[\w\s'-]{0,60}?\b(?:lives?|sits?|is)\s+in\s+"
    r"(?:a|an|the|this|that)\s+session\b"
)
# 3. Recall-gap narration: "Honest recall gap: ..." / "Recall gap: ..." —
#    transient mid-conversation self-assessment, never a durable fact.
_RECALL_GAP_OPENER = re.compile(r"(?i)^(?:Honest\s+)?recall\s+gap\b")
# 4. Truncated clause: cut mid-sentence, ending on a bare connective /
#    determiner / preposition ("...clears over the next"). A blanket
#    no-terminal-punctuation check is TOO BROAD — real config facts end on a
#    value token ("Config: EMBEDDING_API_URL = http://host:8000/embed") — so
#    this fires only when the final word is a dangling function word that no
#    complete sentence ends on. _is_noise() covers ellipses and a few hanging
#    connectors, but is_junk_fact() is called standalone by the supabase
#    plugin, so the gate must live here too.
_TERMINAL_CHARS = ".!?'\")]`"
_DANGLING_TAIL = re.compile(
    r"(?i)\b(?:the|a|an|and|or|but|so|to|of|for|with|over|into|onto|from|"
    r"by|on|at|in|next|than|is|are|was|were|be|will|would|could|that|this|"
    r"its|their|as|per|via|after|before|during|until|while|when|where|"
    r"which|who|whose)$"
)


def is_junk_fact(fact):
    """Return True if 'fact' is junk (conversational fragment, deictic reference,
    transient state, secrets-adjacent, or too short).

    Designed to be conservative: false negatives (junk passes) are preferred
    over false positives (real facts blocked). Shared by:
      - session_capture.extract_facts() → _is_noise()
      - plugins/supabase._is_junk()
    """
    f = (fact or "").strip()
    if not f:
        return True
    if len(f) < MIN_FACT_LENGTH:
        return True
    # Deictic/anaphoric openers — meaningless without prior context
    if _DEICTIC_OPENER.match(f):
        return True
    # Mid-thought openers — no named subject, references earlier narration
    if _MID_THOUGHT_OPENER.match(f):
        return True
    # Transient/hedged state
    if _TRANSIENT_STATE.search(f):
        return True
    # Secrets-adjacent — risk of leaking credentials
    if _SECRETS_ADJACENT.search(f):
        return True
    # Any credential value storage would mask (e.g. *_SERVICE_KEY=, *_PASS=, *_PAT=, raw JWT)
    if mask_secrets(f) != f:
        return True
    # Truncated secret token patterns
    if _TRUNCATED_SECRET.search(f):
        return True
    if _VALUE_ELLIPSIS.search(f):
        return True
    # First/second-person conversational narration or offer-to-user phrasing
    if _CONVERSATIONAL.search(f):
        return True
    # Conditional user-guidance: "If the/that/it ... then ..."
    if _CONDITIONAL_GUIDANCE.match(f):
        return True
    # Ephemeral URL announcement: "Live for/at ... http(s)://"
    if _LIVE_URL.match(f):
        return True
    # Bare shell/code fragments — no declarative prose
    if _CODE_SNIPPET.match(f):
        return True
    # ── 3rd-pass leak classes (card t_cfb35dd1) ──────────────────────────
    # Diagnostic NARRATION (transient mid-debug) vs. dated diagnostic fact:
    # opener + markdown bold + NO date anchor. All three required — a dated
    # "Root cause of the 2026-.. outage:" fact keeps its date and survives.
    if (_DIAG_NARRATION_OPENER.match(f)
            and _MARKDOWN_BOLD.search(f)
            and not _HAS_DATE.search(f)):
        return True
    # Sentence-initial "If ... would/could/might" speculation with no dateline
    if _IF_SPECULATION.match(f) and not _HAS_DATE.search(f):
        return True
    # Verb-initial past-participle fragment: "Pulled (via ...)" — no subject
    if _VERB_INITIAL_FRAGMENT.match(f):
        return True
    # Ephemeral editability working note: "... one-line edit + re-render"
    if _EPHEMERAL_EDIT_NOTE.search(f):
        return True
    # Sequential-instruction / imperative-deictic openers: "Then ...", "Expect
    # this ...", second-person "You don't ..."
    if _INSTRUCTION_OPENER.match(f):
        return True
    # ── Heartbeat leak classes (card t_85377168) ─────────────────────────
    # Deictic session narration: "The X lives/sits/is in a session ..."
    if _SESSION_DEIXIS.match(f):
        return True
    # Recall-gap self-assessment: "Honest recall gap: ..." / "Recall gap: ..."
    if _RECALL_GAP_OPENER.match(f):
        return True
    # Truncated clause: no terminal punctuation AND ends on a dangling
    # function word no complete sentence ends on ("...over the next").
    if f[-1] not in _TERMINAL_CHARS and _DANGLING_TAIL.search(f):
        return True
    return False


def _is_noise(s):
    """Return True if the candidate is NOT a durable, self-contained fact."""
    s = (s or "").strip()
    if len(s) < MIN_FACT_LENGTH or len(s) > MAX_FACT_LENGTH:
        return True
    # markdown scaffolding
    if "|" in s:                       # table row/header
        return True
    if "```" in s or s.startswith("#") or s.startswith(">"):
        return True
    if "\n" in s:                      # multi-line blob that escaped segmentation
        return True
    # transcript role tags / questions
    if _ROLE_TAG.search(s):
        return True
    if s.endswith("?") or _QUESTION_OPEN.match(s):
        return True
    # mid-sentence fragment: a real fact opens with a capital, digit, quote,
    # or backtick-identifier — not a lowercase clip or stray punctuation.
    first = s[0]
    if not (first.isupper() or first.isdigit() or first in "`\"'"):
        return True
    # truncated / dangling clip: a durable fact ends on terminal punctuation or
    # a closing token, never on an ellipsis or a hanging connector.
    if s.endswith("...") or s.endswith("…"):
        return True
    if s[-1] in ",;:—-" or s.endswith("so") or s.endswith("and") or s.endswith("but"):
        return True
    # filename-split artifact ("conf:", "py:", ...)
    if _FRAGMENT_COLON.match(s):
        return True
    # conversational / narration turns
    if _CONVERSATIONAL.search(s):
        return True
    # Also check the shared junk-fact gate (catches deictic, transient, etc.)
    if is_junk_fact(s):
        return True
    return False
