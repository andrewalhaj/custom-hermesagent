# Cold-Store & Reference-Doc Staleness Audit (proven 2026-06-08)

How to find and remediate stale facts in the Supabase knowledge store and `~/.hermes/references/`
docs WITHOUT deleting real data. The danger: a "cleanup" that nukes historical records or
meta-docs, or that trusts stale stored notes over the live world. This method gates every
destructive step behind a reversible backup and a live-system check.

## Why this is needed
Compaction is a zero-sum char shuffle inside the HOT tier — it relieves nothing. Real headroom
comes from the cold tier (Supabase) + docs tier. But those rot silently: this session the cold
store still served `Manifest`/`ha-fusion`/`:5050`/"2,200 cap" long after those were retired, and
the previously-rewritten `infrastructure-summary.md` still had 11 STALE CHUNKS in Supabase because
the doc rewrite never re-indexed. **Offloading fresh facts into a dirty store is harmful** —
future semantic recall surfaces both the fresh fact AND the stale contradicting one. De-stale
BEFORE you offload.

## The method (read-only first, mutate last)

### 1. Export everything reversibly FIRST
- Supabase: `SELECT * FROM knowledge` → dump every row+column to
  `~/.hermes/references/_archive/supabase-full-<ts>.json`. Any later `DELETE FROM knowledge WHERE id=...` is
  then one re-insert away from undo. (The `knowledge.py` CLI has a `recent` command for quick inspection;
  use SQL for full dump.)
- Docs: `tar czf references/_archive/refs-preB-<ts>.tar.gz -C references <files>`.

### 2. Deterministic dead-term scan (ZERO hallucination)
Build a DEAD-term set from VERIFIED-live ground truth — `(pattern, why_dead, correct_fact)`
tuples — then pure-substring/regex scan every row + doc. Do NOT ask an LLM (or a swarm worker)
to "judge staleness" from its own knowledge — that launders stale priors into the kill list.
The scan is mechanical and auditable.

### 3. Classify — flagged ≠ kill. Bucket into:
- **KILL** — stale CURRENT-STATE claim with no salvage (old profile tables, retired routing).
- **CORRECT** — valuable core + one stale fact (fix the fact, keep the lesson). E.g. a delegation
  root-cause row where "Manifest" is just historical context.
- **REINGEST** — chunk copies of a doc that was rewritten; delete stale chunks, re-index the doc.
- **KEEP-historical** — session transcripts / changelogs are TRUE records of past events. Killing
  them erases history.
- **KEEP-example** — dead term used as an *illustration* in a pattern doc, not a state claim.
- **PROTECT (meta-docs)** — the confabulation-blocklist, a staleness-audit doc, a `.bak` file,
  and a doc's own "the prior version described X / there is no X" correction preamble are
  SUPPOSED to contain dead terms. Auto-classifiers flag them; never kill them. A blocklist ROW
  that itself lists a now-real thing as "confab" (e.g. "LanceDB usage" — LanceDB IS real now)
  needs CORRECTING, not deleting.

### 4. Verify the load-bearing premise against the LIVE system before any kill
The whole kill list rests on "X is dead." Prove it from the filesystem/hosts, not from notes —
notes are exactly what's stale. This session confirmed `docker ps`/`curl` on the host that
ha-fusion was gone and wall-dash live BEFORE archiving 9 docs about ha-fusion. The world wins
over any stored note, every time.

### 5. Respect domain ownership
If a row/doc is in another agent's domain (HA/dashboard → ha-bot), do NOT correct it yourself —
flag it and route it to that profile (see `kanban-swarm-dispatch` cross-profile dispatch).

### 6. Gate the destructive batch
KILL+REINGEST deletions are the irreversible-feeling part (though backed up). Present the
classified ledger and get greenlight before mutating. Docs corrections: `.bak-<ts>` each first.

## Reusable scaffold
A pass = export+backup → build DEAD set from live truth → scan → classify → verify premise live →
present ledger → (greenlit) delete/correct/reingest → re-verify. The per-session scripts live in
`/tmp` during the run; the durable artifact is the `_archive/` backup + a
`references/staleness-audit-<date>.md` ledger so the next pass starts from known state.
