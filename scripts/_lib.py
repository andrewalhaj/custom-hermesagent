"""Small helpers shared by the top-level scripts (cron jobs, CLIs).

Stdlib only, so any script can import it before its own dependencies load.
"""
import os

ENV_FILE = os.path.expanduser("~/.hermes/.env")


def load_env(path=ENV_FILE):
    """Parse KEY=VALUE lines into a dict (quotes stripped, comments skipped); {} if unreadable."""
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env
