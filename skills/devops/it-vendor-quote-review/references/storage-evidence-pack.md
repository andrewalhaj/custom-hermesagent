# Storage-line evidence pack — consumer SSDs in Dell servers under S2D

Cite these; don't assert "Dell disowns them" without the Service Description wording. Verified 2026-10-22.

## Microsoft (S2D)
- *Storage Spaces Direct hardware requirements* (learn.microsoft.com/en-us/windows-server/storage/storage-spaces/storage-spaces-direct-hardware-requirements): every component certified in the Windows Server Catalog; **"Solid-state drives must provide power-loss protection"**; same number and type of drives in every server; the fully configured cluster must pass `Test-Cluster` validation; RAID controllers without pass-through NOT SUPPORTED (so a RAID card's battery-backed cache cannot hide a drive's missing PLP the way it does on a file server).
- *"Don't do it: consumer-grade SSDs in Storage Spaces Direct"* (techcommunity.microsoft.com, Dan Lovinger, Principal SE): S2D issues write-through; without a capacitor-backed buffer the drive commits every 4K write to NAND — order-of-magnitude latency on day one ("not much of a cache anymore"); and "after Storage Spaces Direct acknowledges any write, it must be stable" — a power event leaves mirrored nodes holding different versions of a block.
- Consequence order to state: performance collapse first (silent), data loss on the first power event, no support from any vendor when either happens. It will not refuse to run.

## Drive
- WD Red SA500: no PLP (ServeTheHome teardown; WD product brief lists endurance/MTTF/power, never PLP). Consumer 5-yr RMA = a replacement drive by mail, nothing for data or downtime. Quoted at ~$1,118/4 TB in 10010 — above street; check the unit price too.

## Dell
- *ProSupport for Infrastructure — Service Description* (i.dell.com/sites/csdocuments/Legal_Docs/en/us/dell-prosupport-for-infrastructure-sd-en.pdf): Excluded Services include **"Warranty service or support for non-Dell systems, software, or additional components"** and services required "due to unauthorized attempts by third-party personnel to install, repair, maintain, or modify hardware"; warranty covers "standard Dell components in a standard configuration"; for non-standard components "the Customer will also work with the manufacturer directly"; Collaborative Assistance (Dell phones the vendor) is what remains.
- Mechanism: Dell-sold drives carry Dell firmware; PERC/iDRAC flag anything else "not supplied by an authorized hardware provider" / non-certified, permanently (Dell community threads, staff answers; iDRAC9 release notes log PR36 for non-certified drives). You cannot flash Dell firmware onto a non-Dell drive.
- Calibrated claim: Dell supports the server; the drives are outside coverage and Dell may decline storage work it attributes to them. Aftermarket PowerEdge caddies on a quote exist only to put non-Dell drives in Dell bays.

## MSP side
- Typical MSA sells hardware "as is … no liability whatsoever for the quality, functionality, or operability of any Third Party Products"; liability cap = fees paid excluding hardware/licences; consequential loss excluded. The customer holds the outage alone — which is why the drive line is the one to refuse outright.

## The two sentences to demand in a re-quote
1. The quoted drives are Dell-certified for the chassis and covered under the quoted ProSupport term.
2. The storage configuration is supported by Microsoft for the stated architecture (name it: S2D / SAN / other).
Either sentence they cannot write is the line to change. If the architecture is unnamed, ask "where do the drives physically live and what runs on them" first.
