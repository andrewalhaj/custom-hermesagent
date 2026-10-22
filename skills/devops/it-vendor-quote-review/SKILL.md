---
name: it-vendor-quote-review
description: "Use when reviewing vendor/MSP quotes: decode, redline"
version: 1.0.0
author: Hermes (curator)
license: MIT
metadata:
  hermes:
    tags: [vendor, msp, quotes, licensing, procurement, windows-server, azure-hybrid]
    category: devops
    related_skills: [infra-incident-triage]
---

# IT Vendor / MSP Quote Review

## When to Use

- Andrew shares a vendor/MSP proposal (hardware, licensing, services SOW) and asks for review, "what do you think," flags, or a redline
- Questions on OEM vs Software Assurance, Azure Hybrid Benefit, or Windows Server licensing economics (see references/windows-server-licensing.md)
- Drafting or verifying his upward message (to the CTO) about a quote

Typically MSP quotes, but the method generalizes. Output: verified flags ranked by severity, a redline, and talking points for his CTO. First proven on a real MSP hardware-refresh proposal (Oct 2026).

Vendor facts (corporate structure and rebrand, leadership, staff size, certifications, the customer's per-user/per-server billing breakdown) are not in this file: `memory_search "MSP vendor facts rebrand MSA billing"`. The vendor's public MSA PDF may be the only contract text available beforehand; the signed Quote may reference it or attach a different revision — check which.

## Method (order matters)

1. **Read the actual PDF line items** (read_file extracts PDF text), never the summary someone gave of it. The summary of the reference quote said "hardware refresh"; the line items revealed a larger total and a hidden platform migration.
2. **Price-check suspicious lines against market before judging.** Use exa search (primary web_search backend intermittently returns empty results; exa `web_search_exa` is the reliable fallback) against CDW/Newegg/vendor listings. Separating "fair price, wrong part" from "gouging" changes the entire negotiation tone — the reference quote's consumer SSDs were at street price, so the flag was engineering, not margin.
3. **Decode what the parts list implies but never states.** Checklist of tells:
   - Windows Server Datacenter "FI" = Dell factory-install = **OEM licensing** (see references/windows-server-licensing.md for the full OEM vs SA economics — verified against Microsoft docs 2026-10-13).
   - Datacenter + "No RAID" config + many loose SATA/NVMe drives + aftermarket caddies = software-defined storage (S2D-shaped) and drives destined for server bays, not a NAS. Aftermarket PowerEdge caddies specifically = dodging Dell's drive channel = Dell ProSupport will disown the drives.
   - Zero line items for the incumbent hypervisor (no vSphere/vCenter renewal) + "build new DCs and migrate / lift and shift" SOW verbs = platform migration (VMware→Hyper-V), not a refresh. State confidence honestly (~85%, make the vendor name it).
   - Core-count math: license packs vs actual CPU cores (the reference quote had a 16-core base on 24-core EPYCs = under-licensed; orphan Standard packs with no named machine).
4. **Stress the design against its own purpose.** Two HA hosts: can ONE carry the full VM load (RAM!)? Mixed CPU vendors: live migration breaks across Intel↔AMD. Storage: PLP/certification for the stated workload. The question is always "does this survive the failure it exists for."
5. **Contract shape:** labor "estimate, actual billed at completion" = demand not-to-exceed; require acceptance criteria (timed tested restore + live failover demo before final payment, not racked hardware); cap tariff/price pass-throughs; check expiry date and addressee (expired/misaddressed = free renegotiation leverage).
6. **Surface the strategy inputs the quote silently answers.** OEM vs SA licensing is a hybrid-cloud bet someone must make explicitly. Storage sizing depends on the same answer. These are CTO decisions hiding in checkboxes — extract them as questions, never let a parts list decide them.
7. **Check the quote against newly-discovered assets** (recovered hardware, existing cloud footprint). A quote written before the environment was mapped prices the wrong environment.
8. **Pull the governing contract before judging an MSP objection or a renewal.** Signed Quote first (it overrides the MSA), then the vendor's public MSA PDF — `pdftotext` and grep it yourself; quote clauses verbatim. Map the objection to a clause (Modifications = notice + you-own-the-fallout; Co-Management = in-house IT's determination wins, MSP yields) before replying "allowed/not allowed". Clause map, objection triage, notice-vs-meeting: `references/msp-contract-clauses.md`.
9. **Get the load before the re-quote.** With vCenter access: VMs tab with Memory Size/CPUs/Provisioned columns (sum of RAM allocations + ~10% = what one surviving host must carry), cluster and host Summary + Performance → past year peaks, datastore provisioned totals (minimum usable for the new pool). That page, not an argument, goes back with the change request.

## Framing rules (Andrew's conventions)

- **Severity-ranked flags, hard-stop first.** Lead with the most checkable technical fact (e.g. "consumer drives, uncertified"), not the philosophical ones.
- **Credit before flags**: state what the vendor got right and that prices were market-checked fair — keeps it fixable-with-same-vendor, matters for ongoing MSP relationships.
- **Blame-free upward framing**: errors in the boss's own docs are "the verification system working as intended," never someone's mistake.
- **Decisions table with owners** (decision / owner / what it blocks) — he reuses it verbatim in meetings.
- When he drafts his own message upward, verify his claims against primary sources (vendor/MS docs) before he sends; fix wording precision ("the *license* is factory-installed, not the hardware") and flag when his draft silently retreats from his own strongest findings ("quote seems pretty solid" after finding a dealbreaker) — make the omission deliberate or restore it.
- Watch for conclusion-inversion in drafts: "migration explains the labor costs" vs the actual finding "migration makes 100 hours optimistic."
- "How is the MSP not aware" / "why would they sell this": answer with mechanisms (price-fitting, assembled-not-engineered, load never measured, storage tech undecided, nobody checking) and then say none of them needs winning — a neutral change request makes the why moot. Keep the vendor's competence credited where it is real.

## References

- `references/windows-server-licensing.md` — OEM/FI vs volume+SA economics, Azure Hybrid Benefit mechanics, dual-use rights, verified figures + MS citations (2026-10).
- `references/msp-contract-clauses.md` — MSA clause map (modifications, co-management, advice, proprietary configs, transition, pass-through, non-solicit, exit), translating "bandwidth" objections into numbers, vendor-homework checklist, notice-vs-meeting, why quotes come under-specced.
- `references/storage-evidence-pack.md` — cited sources for the consumer-SSD/S2D/Dell-ProSupport flag (Microsoft PLP requirement + write-through mechanism, Dell Service Description exclusions, WD SA500 no-PLP) and the two sentences to demand in a re-quote.

## Pitfalls

- Don't call uncertified-for-S2D parts "overkill" — the fix costs MORE, not less. Overkill and wrong-part are opposite flags.
- "It all touches one quote" ≠ one finding: the reference quote bundled three projects (hardware refresh + platform migration + storage re-architecture) — each needs its own plan, acceptance test, and labor line. Name the bundling.
- Don't size replacement hardware before the replaced hardware is identified and measured (the reference quote recycled servers nobody had named — vCenter utilization data is the prerequisite for RAM/storage sizing).
- Asking for a different licence channel (volume + SA instead of OEM/FI) or split sourcing violates nothing in a typical MSA — check for `exclusiv`/`procure`/`sole` and say "none found"; expect "FI is simpler" (margin), answer that it is the one line uncorrectable after signature.
- Vendor-authored validation ("40% better") is a prior, not a result; when no independent report exists, the customer's own eval set is the independent report (see retrieval-benchmarking `references/eval-set-from-real-questions.md`).
- A repurposed old server CAN be a backup/copy target (never the only copy → age acceptable) but NOT a production cluster node (vendor-mixed live migration breaks, cert requirements, weakest-survivor problem). Same box, opposite verdicts by role.
