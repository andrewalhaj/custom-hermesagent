# Windows Server Licensing — OEM vs Volume+SA, AHB mechanics

Condensed from Microsoft primary sources, verified 2026-10-13:
learn.microsoft.com/en-us/windows-server/get-started/azure-hybrid-benefit,
microsoft.com/licensing/faqs, microsoft.com/en-us/licensing/product-licensing/windows-server.
Use for quote reviews and hybrid-cloud licensing questions.

## The two channels

| | OEM / Dell "FI" (factory install) | Volume (CSP/Open Value) + Software Assurance |
|---|---|---|
| Win Server current version | Own forever, tied to chassis | Own forever, portable |
| Future versions (2028 etc.) | Repurchase in full (~$16-20K per 48 Datacenter cores) | Included while SA term active |
| Azure VMs (Windows meter) | Rent Windows by the hour inside VM price (~$400/core/yr), no off switch | Azure Hybrid Benefit: VM bills at Linux rate (~35-45% off total; MS quotes "up to 80%" with reserved instances) |
| DR/passive server | Needs own license | Covered (SA passive-replica right) |
| Convertible later? | NO — no OEM→SA path, ever | n/a |
| SA nature | — | Ongoing term ~25%/yr of license price, must stay ACTIVE (lapse = AHB must be removed, version escalator stops; MS explicit) |

## Key verified mechanics

- **AHB eligibility (MS verbatim):** "on-premises core licenses ... with active Software Assurance or qualifying subscription licenses." Standard and Datacenter both qualify. OEM categorically cannot.
- **Datacenter dual-use rights (MS verbatim):** "With Datacenter Edition licensing, you get these lower-cost instances in Azure while maintaining existing on-premises deployments." Standard edition does NOT get dual-use (180-day migration grace only). → Datacenter+SA covers on-prem host AND Azure VMs simultaneously during migration; no double-pay window, no rollback deadline.
- **AHB allocation math:** first 16 core licenses → up to 16 vCPUs across ≤2 Azure VMs; each further 8 licenses → 8 vCPUs on one VM.
- **Core minimums:** 16-core base per server, but license ALL physical cores (24-core EPYC 9254 needs 16+8). Quotes with 16-core line items on >16-core CPUs are under-licensed.
- **License Mobility ≠ AHB:** Windows Server is NOT eligible for classic License Mobility; AHB is its separate Azure-specific mechanism. Don't conflate.
- Windows Server by-VM licensing (post-Oct 2022) and Flexible Virtualization Benefit also require SA/subscription.

## Ballpark economics (2026 figures, sanity-check before quoting)

- Datacenter volume list ≈ $6,771 per 16-core pack (~$423/core); OEM runs ~15-25% under.
- 48 cores: OEM ~$16-18K vs license+3yr SA ~$33-36K list → realistic delta **$12-15K/3yr after reseller discount**.
- Offsets that each roughly cover the delta once: 3 mid-size VMs (≈12 vCPU) in Azure ≈ $4.8K/yr of avoided Windows meter; DR-server license waiver ($4-8K); one skipped version repurchase ($16-20K).
- Decision framing that works: "OEM bets 'never cloud,' SA bets 'eventually' — it must be the CTO's explicit bet, not a factory checkbox, because it's the ONLY quote line that can't be corrected later."

## Explaining to a licensing newcomer (sequence that landed)

1. OEM = own the present, rebuy the future, rent the cloud. SA = own the present, future included, cloud included.
2. "Buying Windows twice" is wrong framing — it's buy once + RENT forever (Azure meters Windows hourly; there's no second purchase to finish).
3. SA is not a license; it's a maintenance contract ON a license (membership/perks analogy). AHB is one perk inside it.
4. Common confusion to pre-empt: OEM does NOT block hybrid features/migration — it makes them cost more (the stranded-license + meter problem).
