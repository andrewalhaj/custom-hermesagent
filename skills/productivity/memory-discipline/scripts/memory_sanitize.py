#!/usr/bin/env python3
"""
memory_sanitize.py — Strip corruption artifacts from MEMORY.md and USER.md.

Deployed live at ~/.hermes/scripts/memory_sanitize.py and wired as a no_agent
cron every 30 min (*/30 * * * *, deliver=local). This copy is the canonical
source kept with the memory-discipline skill so it can be re-deployed after a
host migration / fresh install.

Corruption types it fixes:
  1. read_file line-number prefixes:  "4|some content" -> "some content"
     (root cause: a memory-editing cron read MEMORY.md with read_file, which
     emits an "N|" gutter, then wrote it back — persisting the gutter.)
  2. Stale [HONCHO_DUP: YYYY-MM-DD] tags where date >= GRACE_DAYS old
     (the Memory Honcho Dedup cron flags dups then deletes after a grace
     window; this clears tags it left behind past the window.)

Usage:
  python3 memory_sanitize.py              # silent if clean, reports if fixed
  python3 memory_sanitize.py --verbose    # always print results
  python3 memory_sanitize.py --check      # exit 1 if corruption found, no write

Safe to run anytime. Only writes if it finds something to fix.
Creates .bak-sanitize-<ts> before any write.
"""
import os, re, shutil, sys
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
FILES = [
    HERMES_HOME / "memories" / "MEMORY.md",
    HERMES_HOME / "memories" / "USER.md",
]
GRACE_DAYS = 3

# N| prefix inserted by read_file tool (e.g. "42|some content")
LINE_NUM_RE = re.compile(r"^(\d+)\|", re.MULTILINE)

# [HONCHO_DUP: YYYY-MM-DD] tag inserted by Memory Honcho Dedup cron
HONCHO_DUP_RE = re.compile(r"\[HONCHO_DUP:\s*(\d{4}-\d{2}-\d{2})\]\s*")


def strip_line_numbers(text: str) -> tuple[str, int]:
    """Remove N| prefixes. Returns (cleaned_text, count_fixed)."""
    matches = LINE_NUM_RE.findall(text)
    if not matches:
        return text, 0
    cleaned = LINE_NUM_RE.sub("", text)
    return cleaned, len(matches)


def strip_stale_honcho_dups(text: str) -> tuple[str, int]:
    """Remove [HONCHO_DUP: YYYY-MM-DD] tags >= GRACE_DAYS old."""
    now = datetime.now(timezone.utc)
    count = [0]

    def maybe_strip(m: re.Match) -> str:
        try:
            tag_date = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if (now - tag_date).days >= GRACE_DAYS:
                count[0] += 1
                return ""
        except ValueError:
            pass
        return m.group(0)

    return HONCHO_DUP_RE.sub(maybe_strip, text), count[0]


def sanitize_file(path: Path, check_only: bool = False) -> dict:
    if not path.exists():
        return {"path": str(path), "skipped": "not found"}

    original = path.read_text()
    text, ln_count = strip_line_numbers(original)
    text, dup_count = strip_stale_honcho_dups(text)

    total = ln_count + dup_count
    if total == 0:
        return {"path": str(path), "status": "clean"}

    if check_only:
        return {
            "path": str(path),
            "status": "corrupted",
            "line_number_prefixes": ln_count,
            "stale_honcho_dup_tags": dup_count,
        }

    ts = int(datetime.now().timestamp())
    bak = path.parent / f"{path.name}.bak-sanitize-{ts}"
    shutil.copy2(path, bak)
    path.write_text(text)

    return {
        "path": str(path),
        "status": "fixed",
        "line_number_prefixes_removed": ln_count,
        "stale_honcho_dup_tags_removed": dup_count,
        "backup": str(bak),
    }


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    check_only = "--check" in sys.argv

    results = [sanitize_file(f, check_only=check_only) for f in FILES]
    any_issue = any(r.get("status") in ("fixed", "corrupted") for r in results)

    if not verbose and not any_issue:
        sys.exit(0)  # Silent if clean

    for r in results:
        s = r.get("status", "?")
        p = r["path"]
        if s == "clean":
            if verbose:
                print(f"[CLEAN]  {p}")
        elif s == "fixed":
            print(f"[FIXED]  {p}")
            print(f"         line-number prefixes removed : {r['line_number_prefixes_removed']}")
            print(f"         stale HONCHO_DUP tags removed: {r['stale_honcho_dup_tags_removed']}")
            print(f"         backup                       : {r['backup']}")
        elif s == "corrupted":
            print(f"[CORRUPT] {p}")
            print(f"          line-number prefixes : {r['line_number_prefixes']}")
            print(f"          stale HONCHO_DUP tags: {r['stale_honcho_dup_tags']}")
        else:
            print(f"[SKIP]   {p} — {r.get('skipped', '?')}")

    if check_only and any_issue:
        sys.exit(1)


if __name__ == "__main__":
    main()
