"""Parametrized pytest suite for is_junk_fact() — must-reject and must-pass corpora."""
import os
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import pytest
from junk_filter import is_junk_fact  # noqa: E402

# ── Must-REJECT corpus ────────────────────────────────────────────────────────
# All leaked into the store; every one must return True from is_junk_fact().
MUST_REJECT = [
    "The swarm lane is 100% dead — zero successful swarm runs on this board.",
    "Never mirrored, say the word if you want me to dig harder.",
    "That tells you whether the gateway is dropping/blocking the forward or the problem is downstream.",
    "So a clone won't run out of the box, and the auto v1/v2 switching lives in the launcher you'd have to compile.",
    "Expect the same iterate-loop as AeroMLM: run → paste error/weirdness → I patch → repeat.",
    "I pulled the SRL-T source: `IsLoggedIn` is literally one pixel — it reads the color at the **music tab corner** (width-4, height-21) and matches it.",
    "If the file *is* exactly at that path and it still errors, then Simba's include path is pointed somewhere else — in Simba go to **Tools/Settings**.",
    "Live for a flip-through: **http://203.0.113.10:8903/servicenow-loa-presentation-vintage.html** (F = fullscreen, S = speaker view).",
    "LINEAR_WEBHOOK_SECRET=lin_wh...7c19",
    "Config: LINEAR_WEBHOOK_SECRET = lin_wh...7c19",
    # ── Step 1.4: secret names the old regex missed (*_SERVICE_KEY, *_PASS, *_PAT) ──
    "Set SUPABASE_SERVICE_KEY=" + ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "yb2xlIjoic2VydmljZSJ9", "c2lnbmF0dXJl"]) + " in the dispatcher .env file.",
    "The postgres login for the homelab database is DB_PASS=hunter2hunter2 on the Mac Mini.",
    "The CI runner authenticates to GitHub with GITHUB_PAT=" + "ghp_" + "A1b2C3d4" * 5 + " since 2026-10-01.",
    "The fixes are live in the running process (dispatcher was rebooted) but not committed.",
    'OOTB = continuity via delegation, not suspension; no native "hold until return"',
    # ── New: bare shell/code fragments leaked by session-heartbeat pipeline ──
    "INSTALLER_BASENAME=$(basename $PACKAGEPATH)   # ← reads the pkg's filename",
    "EXE_PATH=$(command -v mytool)   # locate the binary",
    "APP_BUILD_ID=$(git rev-parse HEAD)  # current HEAD commit",
    "OUTPUT_DIR=$(mktemp -d /tmp/build.XXXX)   # scratch workspace",
    # ── New (card t_cfb35dd1, 2026-08-13): 3rd-pass leak classes ──
    # Actual leaked rows (full text from sector-boost-removals-20260813.json):
    # 88dc0573 — stale diagnostic narration (opener + markdown bold, no date)
    "Root cause fully diagnosed with evidence: swarm-worker profiles are pinned to the Mac Studio **llama.cpp :8080 endpoint serving 8K context**, but the worker system prompt is ~26K tokens.",
    # 6dbcd24b — deictic verb-initial fragment (no subject)
    "Pulled (via Ollama's own API — no SSH write to the Studio needed).",
    # 350a3c10 — conditional speculation ("If ... would ...", no dateline)
    "If travel@ lives in a separate tenant, mgraszak's access would be a guest/cross-tenant arrangement and `Remove-MailboxPermission` there needs her identity as it exists in *that* tenant (and you'd connect to that tenant's Exchange Online).",
    # b4836c75 — ephemeral working note ("one-line edit + re-render")
    "Source HTML lives at `~/resume-deai/pretty/resume.html` — any tweak (color, spacing, font) is a one-line edit + re-render.",
    # 2026-07-23 flagged classes: sequential-instruction / imperative-deictic openers
    "Then restart the dispatcher and re-run the failing swarm task to confirm the fix.",
    "Expect this ticket shape: a Sentry issue id, a Linear key, and a one-line remediation summary.",
    # ── New (card t_85377168, 2026-08-14): heartbeat leak classes ──
    # Actual leaked rows from session_heartbeat's first full post-fix run:
    # 1 — deictic narration about a live conversation (session deixis)
    "The decommission decision lives in a session that closed before either cron processed it.",
    # 2 — diagnostic narration with **bold**, "Reasoning:" opener, no date
    "Reasoning: the root cause wasn't missing discipline, it was a **stale fact sitting unchallenged for two months**.",
    # 3 — recall-gap narration
    "Honest recall gap: session search finds **no prior Notion webhook session** in either this profile or default'",
    # 4 — transient-state truncated clause (cut mid-sentence, dangling tail)
    "MAX_FACTS_PER_RUN=20 caps each run, so the full backlog of 130 active sessions clears over the next",
    # ── Older corpus (was scripts/test_junk_filter.py) ──
    'These are read-only commands plus a write to /tmp (non-config, ungated), so no greenlight needed.',
    'The fixes are live in the running process (dispatcher was restarted) but not committed.',
    'It lives in /srv/dispatcher/.env — and it already has a value ending ...c19.',
    'The pre-flight check says 0.0.0.0:8787 is already in use but ss / fuser show nothing.',
    'Config: OOTB = continuity',
    'OOTB = continuity via delegation, not suspension; no native hold until return',
    'The Playwright browser runs on the Mac, so it needs a network-reachable URL.',
    'Port is 8080',
    'Config: debug = true',
    'Currently running the test suite, will report back when done.',
    "For now, we're using the fallback config until the real one is ready.",
    'The config already has a value ending in ...c19.',
    'I opened a terminal and ran the test suite.',
    'I checked the config file and it looks good.',
    'Let me verify that the service is running.',
    "I'll update the config after the test passes.",
    'The api_key ending ...x19 is used for authentication.',
]

# ── Must-PASS corpus ──────────────────────────────────────────────────────────
# Real durable facts in the store; every one must return False from is_junk_fact().
MUST_PASS = [
    "The Neo4j MCP server must use mcp-neo4j-cypher from neo4j-contrib, NOT the neo4j-mcp-server Go binary, which has a bug with Neo4j Aura Free instances.",
    "Next.js pitfall (hit 2026-07-08 in mealio): module-level environment secret checks (e.g. SESSION_SECRET) crash `next build` because build-time import evaluates them.",
    "Homelab host naming convention (adopted 2026-07-08 for public repos): the Intel Mac Mini is referred to as MM01 and the Mac Studio as MS01.",
    "session_heartbeat.py silent failure 2026-07-02 to 07-08: the knowledge.py->package refactor changed store() signature, heartbeat kept passing context_prefix= kwarg, TypeError was swallowed by a bare except counting it as 'skipped'.",
    "Knowledge store embedding model (knowledge/embed.py): Qwen3-Embedding-4B via Ollama on Mac Studio, native 2560-dim vectors.",
    "unified_search._search_skills() had a glob leak: skills/**/SKILL.md matched .curator_backups dirs so deleted skills stayed searchable.",
    "Mealio forced password reset design (shipped 2026-07-08): session token extended to 4-part userId.ts.flag.hmac with legacy 3-part tokens still accepted.",
    "infra_watchdog cron (2026-07-01 cleanup): removed zombie Honcho API probe and orphan-ratio/pointer-decay checks that caused false P1 alerts.",
    # ── New: prose sentences with inline code/shell fragments must still pass ──
    "The deployed shell script uses BASENAME=$(command -v basename) to find the basename tool at runtime, falling back to a hardcoded path on legacy systems.",
    "The provisioning playbook sets CONFIG_FILE=/etc/app/config.ini via a template variable and then sources it in the init script.",
    # ── New (card t_cfb35dd1): must-pass guards proving the 3rd-pass gates stay narrow ──
    # Dated diagnostic with a "Root cause" opener + markdown bold survives
    # because the date anchor is present (only opener+bold+NO-date is junk).
    "Root cause (found 2026-07-08): the **knowledge.py package refactor** changed store()'s signature, so session_heartbeat kept passing a stale context_prefix kwarg and silently counted every write as skipped.",
    # Dated conditional survives — speculation gate only fires with no dateline.
    "If the Aura Free instance is paused (observed 2026-06-26), the Neo4j driver would raise Unauthorized rather than a connection timeout, which is the tell for a paused vs. dead instance.",
    # Real host/path fact that mentions an edit but is not an ephemeral edit-note.
    "The dispatcher config lives at ~/.hermes/config.yaml and is read fresh per cron tick, so edits take effect without a service restart.",
    # "The problem" as a subject in a durable, dated fact (not narration).
    "Homelab DNS resolution was fixed on 2026-07-01 by pointing the Mac Mini resolver at 203.0.113.53 instead of the ISP default.",
    # ── New (card t_85377168): must-pass guards proving the heartbeat gates stay narrow ──
    # Durable infra fact, no bold — "The X can't ..." is not session deixis.
    "The gateway can't self-restart — restart from outside: systemctl restart hermes-gateway from any other shell on the Mac Mini.",
    # Has an ID anchor; ends on an identifier token, not a dangling function word.
    "The swarm workers' system prompt is ~26k tokens — root cause t_f85d352d",
    # Dated "Root cause" diagnostic survives the _HAS_DATE gate.
    "Root cause of the 2026-07-09 outage: the **dispatcher lock file** was never released after the OOM kill, so every subsequent claim timed out.",
    # Config pattern ending on a value token (no terminal punctuation) must pass.
    "Config: EMBEDDING_API_URL = http://203.0.113.10:8000/embed",
    # Real host/path fact — "lives at <path>", not "lives in a session".
    "The dispatcher config lives at ~/.hermes/config.yaml, and the kanban DB lives at ~/.hermes/kanban.db on the same host.",
    # ── Older corpus (was scripts/test_junk_filter.py) ──
    "session_heartbeat.py silent failure 2026-07-02: the knowledge.py refactor changed store() signature to require a 'source' parameter.",
    'The Hermes dispatcher runs on port 8787 and is configured via /srv/dispatcher/.env with SUPABASE_URL and SUPABASE_SERVICE_KEY.',
    'Playwright browser tests require a network-reachable URL; localhost does not work when the browser runs on a separate Mac.',
    'The cold-store guard in knowledge/storage.py validates every write against _coldstore_validate() before committing to Supabase pgvector.',
    'Port 8787 conflict resolved: the old dispatcher process was killed and restarted on the correct port.',
    'Supabase memory provider stores facts via knowledge.storage.store() which runs _coldstore_validate(), _knn_gate(), and embed().',
]


@pytest.mark.parametrize("fact", MUST_REJECT)
def test_is_junk_fact_rejects(fact):
    assert is_junk_fact(fact) is True, f"Expected junk but passed: {fact!r}"


@pytest.mark.parametrize("fact", MUST_PASS)
def test_is_junk_fact_passes(fact):
    assert is_junk_fact(fact) is False, f"Expected durable fact but was rejected: {fact!r}"


def test_filter_survives_a_broken_knowledge_package():
    # The gateway plugin imports this; a knowledge import error must not switch the filter off.
    probe = "import sys; sys.modules['knowledge'] = None; import junk_filter; print(junk_filter.is_junk_fact('Let me check.'))"
    r = subprocess.run([sys.executable, "-c", probe], cwd=SCRIPTS, capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == "True", r.stderr[-2000:]
