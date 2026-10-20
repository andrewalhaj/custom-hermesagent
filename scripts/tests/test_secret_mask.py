"""Step 1.4: credential values are masked before anything reaches the knowledge store.

Run: .venv/bin/python -m pytest scripts/tests/test_secret_mask.py -v
"""
import os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

from secret_mask import mask_secrets

# Fake values, built by concatenation so no scanner mistakes this file for a leak.
JWT = 'eyJ' + 'hbGciOiJIUzI1NiJ9' + '.' + 'eyJyb2xlIjoic2VydmljZSJ9' + '.' + 'c2lnbmF0dXJlMTIz'
LEAKS = [
    ('Set SUPABASE_SERVICE_KEY=' + JWT + ' in .env', JWT),
    ('DB_PASS=hunter2hunter2 is the postgres login', 'hunter2hunter2'),
    ('GITHUB_PAT: ' + 'ghp_' + 'A1b2C3d4' * 5 + ' was added to the runner', 'ghp_'),
    ('SENTRY_DSN="https://abc123@o1.ingest.sentry.io/42"', 'abc123@'),
    ('ADMIN_PWD=Tr0ub4dor&3 for the router', 'Tr0ub4dor'),
    ('the api_key: ' + 'sk-ant-' + 'a1B2c3D4e5' * 3 + ' belongs to the bot', 'sk-ant-'),
    ('curl -H "Authorization: Bearer ' + 'abcDEF123456' * 2 + '" localhost', 'abcDEF123456'),
    ('raw token in prose ' + JWT + ' pasted by mistake', JWT),
    ('TELEGRAM_BOT_TOKEN=' + '1234567890:' + 'AAbbCCddEE' * 3, '1234567890:'),
    ('LINEAR_WEBHOOK_SECRET=' + 'lin_wh_' + 'q1w2e3r4t5' * 2, 'lin_wh_'),
    ('pass the api-key: ' + 'Zq9xW2' + 'vB7nM4 to the client', 'Zq9xW2'),
    ('DATABASE_URL is postgres://admin' + ':s3cr3tpw' + '@db:5432/x', 's3cr3tpw'),
    ('curl -H "Authorization: Basic ' + 'dXNlcjpw' + 'YXNzd29yZA==' + '" localhost', 'dXNlcjpw'),
    ('Authorization: Basic ' + 'dXNlcjpw' + 'YXNz', 'dXNlcjpw'),
    ('API_KEY=/aB3+xY9zQ7w==', '/aB3+xY9zQ7w'),  # base64 can start with /; only a pure path is skipped
    ('REDIS_URL=redis://' + ':p4ssw0rd' + '@redis:6379', 'p4ssw0rd'),
    ('key:\n-----BEGIN ' + 'RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAfake\n+/ab12==\n-----END ' + 'RSA PRIVATE KEY-----\nok', 'MIIEowIBAAKCAQEAfake'),
    ('-----BEGIN ' + 'PRIVATE KEY-----\nMIIEvQIBADANfaketruncated', 'MIIEvQIBADANfake'),
    ('supabase cli login with ' + 'sbp_' + '0123456789abcdef' * 2 + ' today', 'sbp_0123'),
    ('new key ' + 'sb_secret_' + 'AbCdEf0123456789' * 2 + ' issued', 'sb_secret_AbC'),
    ('tailscale up --authkey ' + 'tskey-auth-' + 'kABCD1CNTRL-' + 'x1y2z3w4v5u6', 'tskey-auth'),
    ('aws id ' + 'AKIA' + 'FAKEFAKEFAKE1234' + ' in the log', 'AKIAFAKE'),
    ('hf login ' + 'hf_' + 'AbCdEfGhIjKlMnOpQrSt' + '12', 'hf_AbCd'),
    ('stripe ' + 'sk_live_' + '4eC39HqLyjWDarjtT1zdp7dc', 'sk_live_4eC'),
]
CLEAN = [
    'The dispatcher reads SUPABASE_URL and SUPABASE_SERVICE_KEY from /srv/dispatcher/.env.',
    'PATH=/usr/local/bin:/usr/bin is set by the systemd unit.',
    'GITHUB_TOKEN: rotated 2026-10-01 after the leak audit.',
    'SUPABASE_SERVICE_KEY=*** (redacted) and API_KEY=<from vault> are placeholders.',
    'EMBEDDING_API_URL = http://10.0.0.5:8000/embed',
    'Set ANTHROPIC_API_KEY=$ANTHROPIC_KEY in the unit file.',
    # Live-store false positives found 2026-10-16: a variable name as the value; a trailing comma.
    'Sentry webhook uses HMAC-SHA256. Secret = SENTRY_CLIENT_SECRET from the .env file.',
    'The dashboard stores .dashboard_passwd_hash: hashes, never plaintext.',
    # Review 1.4: prose words before a colon, and the shell's PWD.
    'The sessions table primary key: session_id (text).',
    'foreign key: user_id references users(id)',
    'All tests pass: 147/147 after the refactor.',
    'Sort key: stored_at.desc,id',
    'Ran from PWD=/srv/app/scripts',
    'Token: 12345 used of the 200k budget.',
    'Nginx uses HTTP Basic authentication for /admin.',
    # Review 1.4 #3: a path is a location, not a credential.
    'SSH_KEY_PATH=/home/ops/.ssh/id_ed25519 is the deploy key.',
    'KEY_FILE=~/certs/k.pem',
    # New token shapes must not eat prose or identifiers.
    'MAKIAVELLI and BANKIAAAAAAAAAAAAAAAAAA are words; hf_hub_download and sk_live_mode are names.',
    'Docs say a PEM file starts with a BEGIN PRIVATE KEY line.',
]


@pytest.mark.parametrize('text,value', LEAKS)
def test_masks_secret_value(text, value):
    out = mask_secrets(text)
    assert value not in out, out
    assert '***' in out


@pytest.mark.parametrize('text', CLEAN)
def test_leaves_names_and_placeholders(text):
    assert mask_secrets(text) == text


def test_keeps_the_variable_name():
    assert mask_secrets('DB_PASS=hunter2hunter2 here') == 'DB_PASS=*** here'


def test_store_writes_masked_text(monkeypatch):
    # test_junk_filter swaps `knowledge` for a stub module; load the real package for this test.
    if not hasattr(sys.modules.get('knowledge'), '__path__'):
        monkeypatch.delitem(sys.modules, 'knowledge', raising=False)
    from knowledge import storage
    rows, seen = [], []
    monkeypatch.setattr(storage, 'embed', lambda texts: seen.extend(texts) or [[0.0]] * len(texts))
    monkeypatch.setattr(storage, '_knn_gate', lambda text, vector: seen.append(text) or (None, None))
    monkeypatch.setattr(storage, '_supa_insert', lambda row: rows.append(row))
    monkeypatch.setattr(storage, '_classify_sector', lambda text: None)
    monkeypatch.setattr(storage, '_coldstore_log', lambda *a, **k: None)
    leak = 'Digest: set SUPABASE_SERVICE_KEY=' + JWT + ' and restarted the dispatcher.'
    storage.store(leak, source='session-distill')
    assert len(rows) == 1
    for text in [r['text'] for r in rows] + seen:
        assert JWT not in text and 'SUPABASE_SERVICE_KEY=***' in text


def test_linear_on_long_underscore_runs():
    import time
    t = time.time()
    mask_secrets('KEY_' * 5000)
    assert time.time() - t < 0.2
