#!/usr/bin/env python3
"""Cron entry point (the script field takes no args): embed new session turns into the session store.
Runs knowledge.session_embed with --apply --since-last; the job itself lives in scripts/knowledge/session_embed.py."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from knowledge.session_embed import main

if __name__ == "__main__":
    sys.exit(main(["--apply", "--since-last"]) or 0)
