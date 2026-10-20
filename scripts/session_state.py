"""state.db and watermark helpers shared by the session cron jobs (distill, heartbeat).

Read-only connect so a cron bug can never write the agent's live state.db.
"""
import json
import os
import sqlite3
from pathlib import Path

# Dedup/store only need raw vector similarity; the layered pipeline costs 12-34s per
# query and blows the cron timeout. knowledge.config.flag reads os.environ at call time.
SLOW_RETRIEVAL_FLAGS = ("RERANK", "QUERY_EXPAND")


def connect_ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def save_json(path, data):
    """tmp + replace: a kill mid-write must not wipe the saved progress."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def retrieval_flags_off():
    for flag in SLOW_RETRIEVAL_FLAGS:
        os.environ[flag] = "false"
