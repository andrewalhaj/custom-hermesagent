"""Environment/config lookup shared by the Hermes scripts.

Scripts read host addresses, ports and filesystem paths from the process
environment or from ``~/.hermes/.env`` (``$HERMES_HOME/.env``) instead of
hardcoding them, so this repo can be public. The real environment wins; the
.env file is a fallback; an optional default comes last.
"""
import os
from pathlib import Path

HERMES_HOME: Path = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()

_cache: dict[Path, dict[str, str]] = {}


def _parse(file: Path) -> dict[str, str]:
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if not key or " " in key:
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        out[key] = val  # later duplicates win
    return out


def env(key: str, default: str | None = None) -> str | None:
    val = os.environ.get(key)
    if val:
        return val
    file = HERMES_HOME / ".env"
    if file not in _cache:
        _cache[file] = _parse(file)
    return _cache[file].get(key) or default


def require(key: str) -> str:
    val = env(key)
    if not val:
        raise SystemExit(f"{key} is not set: export it or add {key}=... to {HERMES_HOME / '.env'}")
    return val


def path(key: str, default: str) -> Path:
    return Path(env(key, default)).expanduser()
