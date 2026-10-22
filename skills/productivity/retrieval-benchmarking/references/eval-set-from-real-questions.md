# Building a gold eval set from real user questions

Use when a new corpus (company docs, SOPs, a product manual set) needs a decision-grade
retrieval benchmark and no frozen set exists yet. Complements `ragas-reference-testset.md`
(synthetic generation from the documents) — synthetic questions sound like the documents;
real askers do not, and that gap is what the system has to close. Start with real questions;
use Ragas only to pad coverage.

## Procedure

1. **Collect from where questions already exist**: helpdesk tickets, chat threads to the
   senior people, "ask Dave" questions. Keep the asker's wording — misspellings,
   part-number-only queries, vague phrasing. Hand out the collection sheet
   (`templates/eval-question-handout.json` → `docx_create.py`): 5–10 questions per person,
   where the answer lives (or "don't know"), the answer if known, frequency.
2. **Target mix** (balance after collection, not per person): ~40% procedure ("how do I…"),
   ~30% lookup (torque value, part number, fault code), ~20% multi-document, ~10%
   not-documented. The not-documented group is the abstention test — the system must
   answer "no reviewed page covers this", never guess.
3. **Consolidate** into one master sheet, one row per question, verbatim text kept and a
   cleaned copy in its own column. De-duplicate exact repeats only; two people asking the
   same thing differently is two rows.
4. **Gold-label with a subject-matter owner**: document name, section/heading/page, answer
   text (1–3 lines), `confirmed_by`. This is the only step that needs a human and the one
   that costs the most — budget a few hours of a senior person's time. "Don't know" rows go
   to the owner for a 5-minute lookup before being tagged not-documented.
5. **Freeze** at ~100 rows (50 is usable; CI ≈ ±5 points at 100). Version the file; new
   questions go into v2, never edited into v1 after a decision depends on it.

Master-sheet columns: `id, question, kind (procedure|lookup|multi-doc|not-documented),
gold_document, gold_section, gold_answer, frequency (daily|weekly|rare), source (role),
confirmed_by`.

## Scoring (same four numbers every time)

- Gold doc in top 5 (Success@5) and its rank (MRR) — the index.
- Returned text contains the answer (hosted judge, `jev.recall_judge_question`) — the
  delivery. Score it at the exact budget production hands the model (caps, slots,
  formatter, or the managed service's `maxOutputSizeInTokens`), not on raw hits.
- Not-documented questions correctly come back empty.

A configuration ships only when it beats the incumbent outside the bootstrap CI on these
numbers; the set is never edited to flatter a result.

## Handout rules that matter

- No customer-confidential content in the question text; reference documents by name.
- Tell askers not to look anything up to improve the question — first version is right.
- Keep the reviewer page separate from the technician page; askers skip it.
