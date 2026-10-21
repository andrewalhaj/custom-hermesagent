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
- Connect Computer has two independent checkboxes and only one matters to TIA: **Always display in navigation window** keeps the server in the tree (cosmetic; unticked = gone next time ALM opens, which reads as "I have to reconnect every time"), **Add to search list** (also File → Settings → License keys tab → search list) is what TIA/WinCC consult when they look for a key. A laptop can show the server's keys in the tree and still report "no license" until the server is in the search list. The search list lives in ALM's settings, so it is the item to seed in the per-laptop onboarding script; users never open ALM day to day (print-spooler analogy) and only see a dialog when the server is unreachable or the pool is empty.
- Gameplan doc: ~/reports/dtg-siemens-license-server-plan.md (VM = DTG-LS01; Phase 0 inventory + Floating/Single decision gate before any build; its "test with a Trial key" line is wrong — see the canary rule below).
- New purchases arrive via Online Software Delivery: ALM → Online Software node → ticket number → download directly on the target machine.
- **Verified live on DTG-LS01 (ALM V6.2 SP5, 2026-10-20):** Settings dialog is File → Settings → Connection tab: Port 4410, "Allow remote connections", tick "Immediate restart of service" (free while no keys are loaded). Installer: tick the WebView2 Runtime — the `Online Software` node is a web view and is blank without it. Server ALM version must be ≥ the laptops' (Help → About).
- In the `Online Software` delivery-note row, `Download` = the product ISOs (many GB of TIA installers), **`Transfer license(s)`** = the key. Clicking Download to get a key is the common mistake.
- `Transfer license(s)` → *"TransferOfLicenses: Error unknown – Forbidden"* is a portal 403: the Siemens ID in use lacks OSD transfer rights for that customer number, nothing on the server is involved. Fix: sign in as whoever did the earlier transfers (green checks), or get the right granted by the company's Industry Mall administrator (SiePortal → user icon → mySiePortal → authorizations); fallback SiePortal → Support → Create service request, topic Online Software Delivery, quoting customer no. + delivery-note SIRS number.
- **`Online Software` is not a trial-key source.** It lists delivery notes for the customer number; a green check on `Transfer license(s)` = key already left Siemens (lives on some laptop/stick → must be *moved* via Connect Computer); no check = still on Siemens → download straight onto the server. Read the CoL (`Certificate` button) for Floating vs Single before downloading. The exclusion canary is therefore a real Floating key moved from an idle engineer's laptop and left overnight, not a Trial.
- Connect Computer "not accessible" triage, in order, from the laptop: `Resolve-DnsName <server>` (a post-join rename leaves a stale record — retry with the IP to confirm), `Test-NetConnection <ip> -Port 4410` (False → the inbound rule is not enabled for the **Domain** profile; the join switches the profile), then on the **server** `netstat -ano | findstr :4410` must show `0.0.0.0:4410 LISTENING` — `127.0.0.1` only means Allow remote connections never applied. A laptop's own `127.0.0.1:4410` listener is normal, don't read it as the server's. If TCP connects and it still fails, check the dialog: the Computer name field takes the bare hostname, the port goes in its own box — `host:4410` in the name field is resolved as a hostname.
- Key version is part of the match: a V15 Floating key licenses WinCC/TIA V15 and older only; a laptop on V17/V19 needs that version's key (the newer delivery notes). "No license found" with a key visibly sitting on the server is almost always version mismatch — compare TIA version to key version before touching the network.
- Laptop gotchas seen in rollout: ALM service set to Manual → "Service has not been started" and the laptop loses its key every reboot (set Automatic; add to the per-laptop checklist). ALM won't launch / not in Settings → Apps: don't hunt for the uninstaller — run the same 6.2 SP5 setup and take **Repair** (keys in `AX NF ZZ` survive both repair and uninstall). The registry/appwiz entry is named `SIMATIC Automation License Manager…`, never "ALM". Two laptops with a dead `almservice` = EDR/AV policy quarantining `almsrv.exe`, not coincidence — check quarantine, exclude the ALM program folder in the central AV policy before Phase 5. A laptop on guest Wi-Fi cannot reach the server at all; employee SSID (802.1X machine cert, see skill `windows-server-administration`) is a precondition of the search-list step. USB transfer fails on BitLocker-To-Go-encrypted or exFAT/NTFS sticks (ALM wants FAT32) or with TIA open — use the network path instead.
- Server hardening commands: `Add-MpPreference -ExclusionPath "C:\AX NF ZZ"` (Intune/GPO AV policy overwrites local exclusions — re-check after the join, put it in the policy); `Disable-ScheduledTask -TaskPath "\Microsoft\Windows\Defrag\" -TaskName "ScheduledDefrag"`; firewall rule TCP 4410 inbound, Domain+Private profiles.
- Server operations: a reboot drops every reservation and TIA errors on every laptop → patch in a window with no open sessions. Storage vMotion, clone, snapshot-revert, P2V = disk-level work until proven otherwise: keys out to a stick first, back after. Canary before real keys: one Floating key moved from an idle engineer's laptop, left through a backup/AV cycle, confirmed still valid (Trial keys don't transfer, so they can't play this role).
