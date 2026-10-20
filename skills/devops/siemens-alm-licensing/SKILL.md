---
name: siemens-alm-licensing
description: Use when moving or serving Siemens ALM license keys.
---

# Siemens Automation License Manager (ALM) — key handling (TIA/STEP 7/WinCC)

From-memory knowledge of ALM 5/6; web search was down when written — verify port/menu wording against the installed version before putting in a runbook.

## Mechanism
- A key is one movable file in a hidden `AX NF ZZ` folder, bound to its physical disk location. ALM **moves** it; there is never a second copy.
- Copying via Explorer, disk imaging/cloning (Veeam, Macrium, Clonezilla, dd) produces a **defective** key → Recover request on Siemens Industry Online Support (needs the Certificate of License number; takes days).
- Exclude the key folder from AV, backup, defrag.

## Rules
- **License leaves the machine before the machine is touched**: ALM → drag key to USB stick → image/wipe/rebuild → drag back. Backups of a disk with the key are useless for the key — a plain backup doesn't require moving the key out first (the live key stays valid), but the USB stick + CoL are the only real backup of the license: label, log the numbers, lock up.
- Transfer types: Floating and Single move freely; **Trial and Rental don't transfer**. Trial = license number all zeros, 21 days — not worth saving.
- Upgrade keys need the prior-version key present; move them together.
- Dead laptop: pull the SSD, USB enclosure on a working PC with ALM, unlock BitLocker first, drag key off. Dead disk → Recover.
- Uninstalling TIA does NOT remove the key.

## Licensing model (answer this before any sizing question)
- Per **installation / concurrent use**, never per named user — ALM has no user concept, only storages and hostnames. Contrast for Andrew: M365 = per named user, Windows Server = per core + CALs, Siemens = a dongle that happens to be a file (FlexLM-style concurrent use).
- Single = one machine, any number of people at it. Floating = one concurrent session from any machine → buy to **peak concurrency** (watch the server's "in use" view for a few weeks), not headcount. Don't map keys to engineers in a spreadsheet; it drifts and means nothing.
- Compliance exposure = installations running without a key (an expired Trial in ALM is the tell), not headcount.
- Edition hierarchy drains the pool upward: Basic users consume Professional keys when Basic ones are gone — account for it when sizing.
- Reading an ALM screenshot: check License type (Trial/Single/Floating), license number (all zeros = trial), validity, red status icon. If the only key is an expired Trial, the real key is elsewhere (original stick, another PC, the CoL) — say so before advising any transfer.

## License server (fleet fix)
- **Only Floating keys serve remotely.** Type is baked in at purchase; Single can't be converted (ask distributor about crossgrade).
- Server: Windows VM running ALM (not an SMB share), File → Settings → Allow remote connections, inbound TCP **4410**.
- Client: ALM → Edit → Connect Computer → server name. TIA uses local keys first, then remote; nothing to set in TIA.
- Matching: product family (higher edition covers lower), version ≥ required, cheapest fitting key; first-come-first-served, no per-user reservation. Key is reserved on server, not moved; released on exit or heartbeat timeout.
- Offline field work: user drags a Floating key to the laptop (check-out) and back.
- **Reservation vs transfer** (Andrew's "unassign" question): reservation is automatic — TIA open = in use, TIA closed = back in pool, heartbeat frees crashed clients; force-release only for stuck sessions. Transfer is a deliberate drag that removes the key from the pool until dragged back. Analogy: DHCP lease vs moving the cable.
- Connect Computer is **persistent** — the ALM Windows service re-attaches at boot; users never open ALM day to day (print-spooler analogy). They only see a dialog when the server is unreachable or the pool is empty.
- Gameplan doc: ~/reports/dtg-siemens-license-server-plan.md (VM = DTG-LS01; Phase 0 inventory + Floating/Single decision gate before any build; test exclusions with a Trial key first).
- New purchases arrive via Online Software Delivery: ALM → Online Software node → ticket number → download directly on the target machine.
- Server operations: a reboot drops every reservation and TIA errors on every laptop → patch in a window with no open sessions. Storage vMotion, clone, snapshot-revert, P2V = disk-level work until proven otherwise with a Trial key: keys out to a stick first, back after. Load one Trial key, run the backup/AV cycle, confirm it is still valid, and only then load real keys.
