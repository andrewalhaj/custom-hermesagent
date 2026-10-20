"""Knowledge package settings: retrieval feature flags and env lookups.

Flags are read at call time so tests and cron jobs (session_state) can toggle them mid-process.
env() falls back to $HERMES_HOME/.env because not every process starts with .env loaded.
"""
import os

import hermes_env

DB_DIR = os.path.expanduser('~/.hermes/knowledge_db')  # benchmark files + classifier cache, not vectors


def flag(name):
    """Retrieval flags are off unless set to exactly 'true'."""
    return os.environ.get(name, "") == "true"


def env(key, default=""):
    return hermes_env.env(key) or default
