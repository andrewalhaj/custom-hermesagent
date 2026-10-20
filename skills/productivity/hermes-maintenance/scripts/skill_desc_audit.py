#!/usr/bin/env python3
"""skill_desc_audit.py — replicate the system-prompt skill-description truncation
so authors SEE exactly what the runtime agent sees in <available_skills>.

WHY THIS EXISTS
  agent/skill_utils.py:extract_skill_description() truncates every skill
  description shown in the system prompt:
      desc = str(raw).strip().strip("'\\"")
      if len(desc) > 60: return desc[:57] + "..."
      return desc
  i.e. a CLIFF at 60: <=60 shown whole; >=61 -> first 57 chars + "...", tail LOST.
  Upstream rejected removing the cap (system-prompt bloat); the sanctioned answer
  is author-feedback. This tool IS that feedback loop, local to our box.

  IMPORTANT: this tool does NOT change runtime behavior. It does not make skills
  fire. It tells you whether a skill's TRIGGER KEYWORD survives into the visible
  57-char window so that a human can FIX the description (the thing that does).

USAGE
  python3 skill_desc_audit.py                 # audit local profile skills
  python3 skill_desc_audit.py --all           # also include core builtin skills
  python3 skill_desc_audit.py --check NAME     # preview one skill (system_prompt_preview)
  python3 skill_desc_audit.py --truncated-only # list only the offenders
  python3 skill_desc_audit.py --json           # machine-readable
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

LIMIT = 60          # the cliff
KEEP = 57           # chars kept when truncated
HOME = Path(os.path.expanduser("~/.hermes/skills"))
CORE = Path("/usr/local/lib/hermes-agent")  # builtin skills live under here

try:
    import yaml  # PyYAML handles folded scalars (>-, |) correctly
except Exception:
    yaml = None

EXCLUDED = {".git", "node_modules", "__pycache__", ".venv", "venv",
            "site-packages", "_archive", "_decommissioned",
            ".archive", ".decommissioned"}


def parse_frontmatter(text: str) -> dict:
    """Extract the YAML frontmatter block between the first two '---' lines."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    body = []
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        body.append(ln)
    raw = "\n".join(body)
    if yaml:
        try:
            data = yaml.safe_load(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    # crude fallback: single-line description only
    out = {}
    for ln in body:
        if ln.startswith("description:"):
            out["description"] = ln.split(":", 1)[1].strip().strip("'\"")
        if ln.startswith("name:"):
            out["name"] = ln.split(":", 1)[1].strip().strip("'\"")
    return out


def render(raw_desc: str) -> str:
    """EXACT replica of agent/skill_utils.extract_skill_description()."""
    if not raw_desc:
        return ""
    desc = str(raw_desc).strip().strip("'\"")
    if len(desc) > LIMIT:
        return desc[:KEEP] + "..."
    return desc


def collect(roots: list[Path]) -> list[dict]:
    rows = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED]
            if "SKILL.md" not in filenames:
                continue
            p = Path(dirpath) / "SKILL.md"
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            fm = parse_frontmatter(txt)
            name = (fm.get("name") or Path(dirpath).name).strip().strip("'\"")
            if name in seen:
                continue
            seen.add(name)
            raw = str(fm.get("description", "") or "").strip().strip("'\"")
            shown = render(raw)
            truncated = len(raw) > LIMIT
            rows.append({
                "name": name,
                "len": len(raw),
                "truncated": truncated,
                "shown": shown,
                "lost_tail": raw[KEEP:] if truncated else "",
                "path": str(p),
            })
    rows.sort(key=lambda r: (-r["truncated"], -r["len"], r["name"]))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Audit skill-description truncation.")
    ap.add_argument("--all", action="store_true", help="include core builtin skills")
    ap.add_argument("--check", metavar="NAME", help="preview a single skill")
    ap.add_argument("--truncated-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    roots = [HOME] + ([CORE] if args.all else [])
    rows = collect(roots)

    if args.check:
        hit = next((r for r in rows if r["name"] == args.check), None)
        if not hit:
            print(f"skill not found: {args.check}", file=sys.stderr)
            sys.exit(2)
        print(f"name:  {hit['name']}")
        print(f"len:   {hit['len']} chars (limit {LIMIT})")
        print(f"status:{'TRUNCATED — tail lost' if hit['truncated'] else 'INTACT — shown whole'}")
        print(f"\nWHAT THE AGENT SEES IN THE SYSTEM PROMPT:")
        print(f"    - {hit['name']}: {hit['shown']}")
        if hit["truncated"]:
            print(f"\nLOST TAIL (never reaches the prompt):\n    {hit['lost_tail']}")
            print("\nFIX: move the highest-signal TRIGGER keyword into the first ~50 chars.")
        return

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    over = [r for r in rows if r["truncated"]]
    ok = [r for r in rows if not r["truncated"]]
    pct = 100 * len(over) // max(len(rows), 1)
    print(f"Skills scanned: {len(rows)}  |  TRUNCATED: {len(over)} ({pct}%)  |  intact: {len(ok)}")
    print("=" * 72)
    show = over if args.truncated_only else rows
    for r in show:
        flag = "✂ TRUNC" if r["truncated"] else "  ok   "
        print(f"[{flag}] {r['len']:>3}c  {r['name']}")
        print(f"          SEES: {r['shown']}")
        if r["truncated"]:
            print(f"          LOST: {r['lost_tail'][:80]}{'…' if len(r['lost_tail'])>80 else ''}")
    if over:
        print("=" * 72)
        print(f"FIX {len(over)} offenders: trigger keyword into first ~50 chars; "
              f"rich when-to-use goes in load_when: + body (not truncated).")


if __name__ == "__main__":
    main()
