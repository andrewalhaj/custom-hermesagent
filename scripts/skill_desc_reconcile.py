#!/usr/bin/env python3
"""
skill_desc_reconcile.py — durable fix for the 60-char skill-description cliff.

Why: agent/skill_utils.py:extract_skill_description() truncates any skill
description >60 chars to desc[:57]+"..." in the system-prompt <available_skills>
index. Truncated descriptions bury their trigger keyword and fire unreliably.
Upstream rejected lifting the cap (issue #13944 / PR #24294 — system-prompt
bloat). So we author WITHIN the constraint and re-apply on session start.

This script is idempotent: it only rewrites descriptions currently >60 chars,
and leaves <=60 ones untouched. Safe to run repeatedly (the on_session_start
hook heal-skill-descriptions.sh does). LOCAL skills only: rewriting core skills
made `hermes update` autostash conflict and left .bak files in the core tree.

Rewrite strategy, in order:
  1. CURATED override map (hand-authored, trigger-first) — highest quality.
  2. Deterministic word-boundary compaction — cut at the last word boundary
     <=60, strip trailing punctuation. Never cuts mid-word, never emits an
     ellipsis into the prompt. Preserves the leading (usually highest-signal)
     content. A safe default; curate later for high-value skills.

Usage:
  skill_desc_reconcile.py --dry-run        # show every change, write nothing
  skill_desc_reconcile.py --apply          # rewrite (git is the backup)
  skill_desc_reconcile.py --check          # cron watchdog: list offenders, silent when clean
  skill_desc_reconcile.py --dry-run --json # machine-readable plan
  skill_desc_reconcile.py --quiet-exit-code # exit 1 if any truncated remain (guard mode)
"""
from __future__ import annotations
import argparse, glob, json, os, re, shutil, sys, time

LIMIT = 60  # must match agent/skill_utils.py

ROOTS = [os.path.expanduser("~/.hermes/skills")]
# Never touch these path fragments (archives, backups, vendored junk).
EXCLUDE = (".bak", "_archive", "/.archive/", "/.git/", "/node_modules/",
           "/venv/", "/site-packages/", "/tests/", "/website/")

# ── Curated trigger-first overrides (extend over time) ──────────────────────
# name -> description (<=60). Hand-authored where the trigger keyword would
# otherwise be lost by naive leading-truncation.
OVERRIDES: dict[str, str] = {
    "kanban-video-orchestrator": "Kanban video: orchestrate multi-agent video production.",
    "concept-diagrams": "Concept diagrams: explain ideas as clean visual schematics.",
    "page-agent": "Page-agent: build a single web page end-to-end.",
    "one-three-one-rule": "1-3-1 rule: structure decisions as problem/options/pick.",
    "hyperframes": "Hyperframes: multi-panel narrative image sequences.",
}


def extract_desc_span(text: str):
    """Return (start_line, end_line, raw_desc) for the description field, or None."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    fm_end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        return None
    ds = None
    for i in range(1, fm_end):
        if re.match(r"^description:\s*", lines[i]):
            ds = i
            break
    if ds is None:
        return None
    # description may be inline or a folded/literal block (>- | etc.)
    m = re.match(r"^description:\s*(.*)$", lines[ds])
    first = m.group(1).strip()
    de = ds + 1
    if first in (">", "|", ">-", "|-", ">+", "|+", ""):
        # folded/literal block: gather indented continuation lines
        buf = []
        while de < fm_end and (lines[de].startswith((" ", "\t")) or lines[de].strip() == ""):
            buf.append(lines[de].strip())
            de += 1
        raw = " ".join(x for x in buf if x).strip()
    else:
        # inline; YAML inline can't span lines for our purposes
        raw = first
        # tolerate accidental wrapped continuation (rare)
        while de < fm_end and lines[de].startswith((" ", "\t")) and not re.match(r"^\s*[A-Za-z_][\w-]*:\s", lines[de]):
            raw += " " + lines[de].strip()
            de += 1
    raw = raw.strip().strip("'\"")
    return ds, de, raw


def compact(desc: str, name: str) -> str:
    """Produce a <=60 char description: override if present, else word-boundary cut."""
    if name in OVERRIDES:
        return OVERRIDES[name]
    if len(desc) <= LIMIT:
        return desc
    # word-boundary truncation at <=60, strip trailing punctuation/space
    cut = desc[:LIMIT]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    cut = cut.rstrip(" ,;:.-—–")
    return cut


def find_skill_files():
    seen = set()
    for root in ROOTS:
        for f in glob.glob(root + "/**/SKILL.md", recursive=True):
            if any(x in f for x in EXCLUDE):
                continue
            if f in seen:
                continue
            seen.add(f)
            yield f


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reconcile skill descriptions to <=60 chars.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    g.add_argument("--apply", action="store_true", help="rewrite truncated descriptions")
    g.add_argument("--check", action="store_true", help="list offenders; print nothing when clean")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--quiet-exit-code", action="store_true",
                    help="guard mode: print nothing, exit 1 if any truncated remain")
    args = ap.parse_args(argv)

    ts = time.strftime("%Y%m%d-%H%M%S")
    plan = []
    for f in find_skill_files():
        try:
            text = open(f, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        span = extract_desc_span(text)
        if not span:
            continue
        ds, de, raw = span
        if len(raw) <= LIMIT:
            continue
        nm = None
        m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        nm = (m.group(1).strip().strip("'\"") if m else os.path.basename(os.path.dirname(f)))
        newdesc = compact(raw, nm)
        plan.append({"name": nm, "path": f, "old_len": len(raw),
                     "new_len": len(newdesc), "old": raw, "new": newdesc,
                     "ds": ds, "de": de})

    # guard mode: just report whether anything is truncated
    if args.quiet_exit_code:
        sys.exit(1 if plan else 0)

    if args.check:
        for p in plan:
            print(f"⚠️ skill description over {LIMIT}c (truncated in the prompt): "
                  f"{p['name']} ({p['old_len']}c) {p['path']}")
        if plan:
            print("FIX: skill_desc_reconcile.py --apply, or hand-write a trigger-first description.")
        return

    if args.json and not args.apply:
        print(json.dumps({"count": len(plan), "plan": plan}, ensure_ascii=False, indent=1))
        return

    if not args.apply:  # dry-run (default if neither flag)
        print(f"TRUNCATED skills to reconcile: {len(plan)}\n")
        for p in plan:
            print(f"[{p['old_len']:>4}c -> {p['new_len']:>2}c] {p['name']}")
            print(f"    OLD(seen): {p['old'][:57]}...")
            print(f"    NEW      : {p['new']}")
        print(f"\n(dry-run — nothing written. {len(plan)} files would change.)")
        return

    # --apply
    written = 0
    for p in plan:
        f = p["path"]
        text = open(f, encoding="utf-8", errors="replace").read()
        lines = text.split("\n")
        # a JSON string is a valid YAML double-quoted scalar: quotes and backslashes stay escaped
        lines[p["ds"]:p["de"]] = [f'description: {json.dumps(p["new"], ensure_ascii=False)}']
        tmp = f + ".tmp"  # tmp + replace: a kill mid-write must not truncate the SKILL.md
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        shutil.copymode(f, tmp)
        os.replace(tmp, f)
        written += 1
    out = {"applied": written, "ts": ts}
    print(json.dumps(out) if args.json else f"APPLIED: {written} files rewritten")


if __name__ == "__main__":
    main()
