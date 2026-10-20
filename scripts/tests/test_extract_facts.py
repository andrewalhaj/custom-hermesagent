"""extract_facts(): real transcript noise yields nothing; durable facts come out with the right tag.

Run: python -m pytest scripts/tests/test_extract_facts.py -v
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

from junk_filter import _is_noise  # noqa: E402
from session_capture import extract_facts  # noqa: E402

# Real noise the old extractor emitted as "facts" (state.db, 2026-07-08).
REJECT = [
    'USER: Status on mealio kanban card?',
    'now:\n\n| Layer | Mechanism | Verified |',
    ". A stray file got swept in — 'Hermes Task Dispatcher.dc.htm",
    'conf:\nNow edit index.html - change HA_URL to empty string',
    'js:\nNow edit patch_conf.py - replace IPs and deploy path:',
    'py:\nNow replace the IP reference in patch_index_pwa.py',
    'push:\nNow let me get the diff and commit SHA for the report:',
    'state:\nNow let me re-run the verifications:',
    '| File | Changes |',
    '### Files Modified (15)',
    '> Note: uncomment and replace with your Glances host',
    'Let me run one final comprehensive verification:',
    'Now edit mobile/mobile.js - change HA_URL to empty string',
    "I'll start by pulling the latest changes and reading auth.ts",
    'Let me just run git rm govee_config.json and git add.',
    "Here's the complete summary:",
    "We're told Do NOT git commit, so I'll stage the deletion.",
    'Should I key the rate limiter on IP or user_id?',
    'What is the current deploy path for the dashboard?',
    '. I need to git rm it from the index too.',
    "secret = 'mealio-dev-secret-change-me'",
    "digest('hex') } ```",
    'Build passes.',
]

ACCEPT = [
    ('DISPATCH_HOST = the Mac Mini running the dispatcher on port 8080.', 'infra'),
    ('The dashboard is deployed to 203.0.113.10 behind a Caddy proxy.', 'infra'),
    ('Root cause was a module-level IIFE that threw at import time.', 'diagnostic'),
    ('The knowledge store runs on Supabase and is served from port 5432.', 'architecture'),
    ('Decision: use vLLM for throughput over SGLang for this workload.', 'decision'),
    ('Rotated the GitHub deploy token after it leaked in the README.', 'credential'),
    ('User prefers concise terminal-friendly output without markdown.', 'preference'),
]


@pytest.mark.parametrize("sample", REJECT)
def test_noise_yields_no_facts(sample):
    assert extract_facts(sample) == []


@pytest.mark.parametrize("sample,tag", ACCEPT)
def test_durable_fact_is_extracted_with_its_tag(sample, tag):
    facts = extract_facts(sample)
    assert tag in {t for _, t in facts}, facts
    assert not any(_is_noise(f) for f, _ in facts)


def test_extractor_does_not_need_the_knowledge_package():
    # Heartbeat dedups/stores itself; a broken knowledge import must not take extraction down too.
    import subprocess
    code = "import sys; sys.modules['knowledge'] = None; import session_capture"
    scripts = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    assert subprocess.run([sys.executable, '-c', code], cwd=scripts).returncode == 0
