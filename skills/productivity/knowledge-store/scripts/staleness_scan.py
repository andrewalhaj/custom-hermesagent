#!/usr/bin/env python3
"""Staleness audit scan — deterministic dead-term flagging over the LanceDB cold
store + ~/.hermes/references/ docs. READ-ONLY: prints a flag report, mutates nothing.

This is STEP 1 of the staleness-audit methodology (see SKILL.md). Output is a
FLAG list, NOT a kill list — every hit must still be classified by hand into
CORRECT / KEEP-historical / PROTECT-meta / REINGEST, and the dead-term premise
must be verified against the LIVE filesystem before acting (steps 2-4 in SKILL.md).

USAGE:
    1. Edit the DEAD list below for the CURRENT verified-dead facts (this is the
       one part you must keep current — it encodes "what is no longer true").
    2. python3 scripts/staleness_scan.py
    3. Manually verify each dead term is ACTUALLY dead on the live host (docker ps,
       port probe, ls) before trusting the flags.
    4. Classify flags into the four buckets; write the ledger to
       references/staleness-audit-<date>.md.

Suppresses the HF/torch load-weights noise so the report is clean.
"""
import json, os, re, glob, sys

HERMES = os.path.expanduser("~/.hermes")

# ── EDIT THIS: verified-dead literal strings. (pattern, why_dead) ──────────
# Keep this list current. Each entry must be a fact you have VERIFIED is no
# longer true against the live system — not a guess. A regex can't confabulate,
# but a wrong entry here flags healthy data, so the list is the careful part.
DEAD = [
    (r"\bManifest\b",        "Manifest retired — direct provider routing now"),
    (r"localhost:2099",      "Manifest port — retired"),
    (r"ha-fusion",           "ha-fusion decommissioned — wall-dash replaced it"),
    (r":5050\b",             "ha-fusion port :5050 dead — wall-dash serves :5051"),
    (r"\bRailway\b",         "Railway no longer relevant"),
    (r"\bNeon\b",            "Neon DB not in current topology"),
    (r"\bQdrant\b",          "Qdrant never installed (verified absent)"),
    (r"\bChroma\b",          "Chroma never installed (verified absent)"),
    (r"2[,.]?200",           "old memory cap 2200 — raised to 3000"),
]
# Context-sensitive: 'backup' near a host id is dead framing, but 'backup' alone
# is a common word — only flag when it sits next to the host that is NOT a backup.
BACKUP_CTX = re.compile(
    r"(backup|BACKUP)[^\n]{0,40}(VPS-A|HA host)|(VPS-A)[^\n]{0,40}(backup|BACKUP)")

# Docs that MUST contain dead terms — pre-mark as PROTECT so they don't read as kills.
# (The blocklist names what's false; audit logs document staleness; .bak files are frozen.)
META_HINT = ("blocklist", "staleness-audit", ".bak", "confabulation")


def scan(text):
    hits = []
    for pat, why in DEAD:
        if re.search(pat, text, re.IGNORECASE):
            hits.append(why)
    if BACKUP_CTX.search(text):
        hits.append("VPS-A is the PROD host, NOT a backup")
    return sorted(set(hits))


def main():
    # ── LanceDB rows ──
    try:
        import lancedb
        db = lancedb.connect(f"{HERMES}/knowledge_db")
        df = db.open_table("knowledge").to_pandas()
        rows = [{"id": str(r["id"]), "text": str(r["text"])} for _, r in df.iterrows()]
    except Exception as e:
        rows = []
        print(f"[warn] LanceDB unreadable: {e}", file=sys.stderr)

    row_flags = [(r["id"], scan(r["text"]), r["text"][:90].replace("\n", " "))
                 for r in rows if scan(r["text"])]

    # ── reference docs ──
    docs = sorted(glob.glob(f"{HERMES}/references/**/*.md", recursive=True))
    doc_flags = []
    for d in docs:
        try:
            txt = open(d, encoding="utf-8").read()
        except Exception:
            continue
        h = scan(txt)
        if h:
            name = d.replace(HERMES + "/", "")
            meta = any(m in name.lower() for m in META_HINT)
            doc_flags.append((name, h, meta))

    # ── report ──
    print("=" * 64)
    print("STALENESS SCAN — FLAGS (not a kill list; classify each by hand)")
    print("=" * 64)
    print(f"LanceDB rows : {len(row_flags)}/{len(rows)} flagged")
    print(f"Reference docs: {len(doc_flags)}/{len(docs)} flagged\n")

    print("--- DOCS ---")
    for name, hits, meta in sorted(doc_flags, key=lambda x: (-len(x[1]))):
        tag = "  [META? verify PROTECT]" if meta else ""
        print(f"  [{len(hits)}] {name}{tag}")
        for h in hits:
            print(f"        · {h}")

    print("\n--- ROWS (first 30) ---")
    for rid, hits, prev in row_flags[:30]:
        print(f"  {rid[:8]} | {'; '.join(hits)}")
        print(f"           \"{prev}\"")
    if len(row_flags) > 30:
        print(f"  ... +{len(row_flags) - 30} more rows")

    print("\nNEXT: verify each dead term is ACTUALLY dead on the live host,")
    print("then classify into CORRECT / KEEP-historical / PROTECT-meta / REINGEST.")


if __name__ == "__main__":
    main()
