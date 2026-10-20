"""Mask credential values so they never reach the knowledge store (pgvector) or the prefix LLM.

Stdlib only: imported by knowledge/storage.py (every write) and junk_filter.is_junk_fact().
Keeps the variable name, so "X_KEY=abc123" still says which key was set, just not its value.
"""
import re

# A secret-named variable (any *_KEY, *_PASS, *_PAT, *_DSN, ... segment) or "Bearer", then its value.
# The name must look like a variable: joined to another segment by _ or - (DB_PASS, api_key, api-key,
# PASSWORD_FILE) or a bare UPPER-case word (TOKEN=). A bare prose word ("primary key:", "pass:") is not
# a name, and bare PWD is the shell's working directory.
# The match starts at the secret segment (prefix stays outside it); bounded suffix keeps this linear.
# The value must hold a non-letter or be 16+ letters, so prose like "GITHUB_TOKEN: rotated" stays;
# an UPPER_SNAKE value is another variable's name and a /path or ~/path is a location, not a credential.
# shortcut: an all-letter password under 16 chars, or one after a bare lower/Capitalised "password:", passes;
# tighten if one ever shows up in the store.
_WORD = r'(?:SECRET|TOKEN|PASS|PASSWORD|PASSWD|PASSPHRASE|PWD|KEY|APIKEY|PAT|DSN|CREDENTIALS?)'
_NAMED = re.compile(
    r'((?:(?:(?<=[_-])' + _WORD + r'(?:_[A-Za-z0-9]+){0,8}'
    r'|(?<![\w-])' + _WORD + r'(?:_[A-Za-z0-9]+){1,8}'
    r'|(?<![\w-])(?-i:(?!PWD\b)' + _WORD + r'))'
    r'''\s*[=:]\s*["']?|\bBearer\s+))'''
    r'''(?!\*|<|\$|~?/[\w./-]*(?![^\s"'<>,;)])|\.\.\.|(?-i:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b)'''
    r'''(?:(?=[^\s"'<>,;)]*[^A-Za-z\s"'<>,;)])[^\s"'<>,;)]{6,}|[A-Za-z]{16,})''',
    re.IGNORECASE,
)
# Token shapes that are secrets with no name next to them: JWT, Anthropic/OpenAI, GitHub, Slack,
# Linear, Telegram bot, PEM private key, Supabase, Tailscale, AWS, HuggingFace, Stripe, the user:pass of a URL, HTTP Basic auth (needs a digit/+/=/ or a capital past
# the first letter, so "Basic authentication" stays).
_TOKEN = re.compile(
    r'(?<=://)[^\s:/@]*:[^\s@/]+(?=@)'
    r'|\bBasic\s+(?=[A-Za-z0-9+/]*[0-9+/=]|[A-Za-z][a-z]*[A-Z])[A-Za-z0-9+/]{12,}={0,2}'
    r'|\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}'
    r'|\bsk-[\w-]{20,}'
    r'|\b(?:gh[pousr]_|github_pat_)\w{20,}'
    r'|\bxox[abprs]-[\w-]{10,}'
    r'|\blin_(?:api|wh)_\w{16,}'
    r'|\b\d{8,10}:AA[\w-]{30,}'
    r'|-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----[A-Za-z0-9+/=\s]*(?:-----END [A-Z ]{0,20}PRIVATE KEY-----)?'
    r'|\bsbp_[0-9a-f]{20,}|\bsb_secret_[\w-]{20,}'
    r'|\btskey-[\w-]{10,}'
    r'|\bAKIA[0-9A-Z]{16}\b'
    r'|\bhf_[A-Za-z0-9]{20,}'
    r'|\b[sr]k_live_[A-Za-z0-9]{16,}'
)


def mask_secrets(text):
    """Return text with every credential value replaced by ***."""
    return _TOKEN.sub('***', _NAMED.sub(r'\1***', text))
