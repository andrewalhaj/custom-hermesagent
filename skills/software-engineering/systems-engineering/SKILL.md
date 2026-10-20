---
name: systems-engineering
description: "Use when applying SE process: V&V, reviews, trade studies"
version: 0.1.0
author: Hermes
license: MIT
metadata:
  hermes:
    tags: [Systems-Engineering, Requirements, Verification, Validation, Technical-Reviews, Risk-Management, DoD, NASA]
    sources:
      - "OUSD(R&E), Systems Engineering Guidebook, February 2022 (240 pp; DoDI 5000.88 / DoDI 5000.02 basis)"
      - "NASA Systems Engineering Handbook, SEH 2.0 Fundamentals (NPR 7123.1), https://www.nasa.gov/reference/2-0-fundamentals-of-systems-engineering/"
---

# Systems Engineering

Knowledge base for doing systems engineering the way the two reference frameworks do it: the
DoD Systems Engineering Guidebook (16 processes, SEP, eight technical reviews, 24 design
considerations) and NASA's SEH Chapter 2 (the 17-process SE engine, V&V distinction,
the cost-effectiveness dilemma). Use it to structure, review, or audit any design, architecture,
migration, or acquisition effort. Reference material, no dependencies. It does NOT cover the
pathway-specific DoD Engineering of Defense Systems Guidebook or NASA SEH chapters 3+.

## When to Use

- "Design this system / architecture / migration properly", "what are we missing?"
- Writing or reviewing requirements, a ConOps, a SEP, an architecture doc, a test plan.
- "Entry/exit criteria for SRR / PDR / CDR", "which baseline does SFR set"
- "It passed the tests but users hate it" — verification vs validation.
- Framing a cost / schedule / performance / risk trade honestly.
- Risk management, TPMs, configuration management, interface control, design considerations (R&M, safety, security, HSI, DMSMS, COTS, MOSA).
- Any defense / aerospace / large-system conversation using DoD or NASA SE vocabulary.

## Prerequisites

None. DoD source PDF: locate a cached copy with `search_files` (pattern `SE-Guidebook*.pdf`). NASA source: re-fetch with `web_extract` on the URL above.

## How to Run

1. This file is always loaded — mental models and index below.
2. Load one reference at a time: `skill_view(name="systems-engineering", file_path="references/<file>")`.
3. Write the resulting ConOps / SEP / V&V matrix / review checklist / trade table to a `.md` with `write_file`.

## Quick Reference — shared mental models

**System** = elements + enabling elements (people, procedures, facilities, and what develops, tests, deploys, sustains it). Value comes from the interconnections. Both frameworks say so.

**The V (DoD Fig 1-2 / NASA SE engine):** design top-down — stakeholder requirements → requirements analysis → architecture / logical decomposition → design solution → stop when each leaf is buildable, buyable, or reusable. Realize bottom-up — implement → integrate → verify → validate → transition. Technical management (planning, decision analysis, assessment/TPMs, requirements mgmt, risk, CM, data, interfaces) runs the whole life cycle.

**Iterative** = re-apply to the same product to fix a discrepancy. **Recursive** = apply to the next layer down (design) or up (realization), or the next phase.

**Verification** proves each "shall" (test / analysis / inspection / demonstration) against the requirements baseline. **Validation** proves the product does its job in its environment for real users, traced to the ConOps / operational need. Validate every phase on phase products, not only at delivery.

**Reviews are event-driven.** Entry/exit criteria, not calendar dates. A schedule-driven PDR produces a false baseline.

**The Systems Engineer's Dilemma (NASA §2.5):** at any cost-effective point — cut cost at constant risk or cut risk at constant cost → performance drops; cut cost at constant performance → more risk; cut risk at constant performance → more cost. Schedule behaves like cost. Say which variable is held constant in every trade.

**Cost lock-in:** design spends ~15% of life-cycle cost and commits ~75% (DAU curve). Early validation is where corrections are still cheap.

**Tailoring is required, not optional** (DoDI 5000.88) — scale processes to maturity, complexity, size, pathway, phase.

**SEP essentials (DoD §1.5):** required for MDAPs / ACAT II-III; Lead SE writes, PM directs; updated before every review; ≥ 15 TPMMs each with threshold / margin / contingency; RIOs collected monthly, reported quarterly; attached (scrubbed) to the RFP; contractor SEMP must align.

**Cyber / resilience** engineering starts at concept, every pathway.

## Reference Index — load on demand

- `references/nasa-seh-fundamentals.md` — NASA SEH 2.0: 17-process engine, Pre-Phase A–F cycling, V&V, Dilemma, cost curve, competency ladder. Load for NASA vocabulary (KDP, NPR 7123.1) or a concise framing of the whole discipline.
- `references/ch1-introduction-and-sep.md` — DoD: purpose, 16 processes, policy table, SEP content/approval. Load when writing or reviewing a SEP.
- `references/ch2-1-systems-of-systems.md` — DoD: SoS types and how SE differs for them.
- `references/ch2-tools-techniques-contracting.md` — DoD: SE tools, digital engineering / MBSE, DevSecOps, MOSA + data rights, agile, engineering resources, certifications, SE in RFPs and contracts.
- `references/ch3-technical-reviews-and-audits.md` — DoD: ASR, SRR, SFR, PDR, CDR, SVR/FCA, PRR, PCA — purpose, baseline set, products, exit criteria.
- `references/ch4-1-technical-management-processes.md` — DoD: IMP/IMS, SRA, TPMs + leading indicators, risk/issue/opportunity, CM baselines, data management, interface control.
- `references/ch4-2-technical-processes.md` — DoD: the eight technical processes, ISO/IEC/IEEE 15288 mapping.
- `references/ch5a-design-considerations-1-13.md` — DoD: Section 508, affordability trades, anti-counterfeiting, COTS, corrosion, CSI, demil, DMSMS, HSI, insensitive munitions, intel/LMDP, interoperability, IUID.
- `references/ch5b-design-considerations-14-17.md` — DoD: manufacturing & quality, modular design, operational energy, PHS&T.
- `references/ch5c-design-considerations-18-24.md` — DoD: R&M, spectrum, standardization, supportability, survivability, system safety, system security engineering / program protection.

## Procedure

1. **Place the effort.** Phase, pathway/ACAT (DoD) or Pre-Phase A–F (NASA), next review; this fixes which processes dominate and what is due.
2. **Bound the system** incl. enabling elements, humans, SoS context (`ch2-1-systems-of-systems.md`).
3. **ConOps before detailed requirements.** Cover nominal, off-nominal, degraded modes. It is the check for missing/conflicting requirements.
4. **Requirements → architecture top-down** to the buildable/buyable/reusable leaf (`ch4-2-technical-processes.md`); MOSA and digital-engineering choices from `ch2-tools-techniques-contracting.md`.
5. **Instrument technical management:** IMP/IMS, TPMs with threshold/margin/contingency, RIO register, CM baselines, ICDs (`ch4-1-technical-management-processes.md`). Stand up the SEP from `ch1-introduction-and-sep.md`.
6. **Two-column V&V matrix:** every requirement → verification method; every ConOps scenario → validation method. Never let one column stand in for the other.
7. **Walk the design considerations** (`ch5a`, `ch5b`, `ch5c`); record each as addressed / tailored / N-A.
8. **Gate on reviews** using `ch3-technical-reviews-and-audits.md` exit criteria; slip the date, not the criteria.
9. **Frame every trade with the Dilemma**; look for win-win first.
10. **Realize bottom-up** and keep technical management running through operations and disposal; upgrades re-enter the V.

## Pitfalls

- Validation treated as a delivery event; verifying requirements nobody validated against a ConOps.
- Decomposing or prototyping deeper than feasibility needs.
- Iterative/recursive used as synonyms.
- TPMs without margin and contingency, or too few of them.
- Unscrubbed SEP in the RFP; SEMP that doesn't align to the SEP.
- Mixing vocabularies: KDP/NPR 7123.1 (NASA) vs milestone/DoDI 5000.88 (DoD) — load the matching reference.
- DoD figures/tables are images in the PDF; references reconstruct them from surrounding text — cite the figure number and re-read the PDF with `read_file` when precision matters.

## Verification

A deliverable produced with this skill has: a ConOps with ≥ 1 off-nominal scenario; every requirement traced to a verification method and every scenario to a validation method; every trade stating the held-constant variable; the next review named with its exit criteria; a disposition for each applicable design consideration. Check with `search_files` for "shall", "validation", "holds constant", "exit criteria" in the written `.md`.
