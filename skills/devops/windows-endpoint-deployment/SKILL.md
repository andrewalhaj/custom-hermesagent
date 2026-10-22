---
name: windows-endpoint-deployment
description: "Use when automating Windows laptop software installs."
version: 1.1.0
metadata:
  hermes:
    tags: [windows, deployment, ppkg, dism, sysprep, ninjarmm, rockwell, siemens, ot-discovery]
    category: devops
---

# Windows endpoint deployment (fleet laptop onboarding, OT/controls software)

Class: "put the same software on N Windows laptops" — standard office apps up to 100+ GB Rockwell/Siemens engineering stacks. Context: on-prem AD `<ad-domain>`, NinjaRMM on every laptop, Intune/Entra P1 available.

## Procedure
1. **Get the real product list.** Parse the installed-programs export (fixed-width: name `[:114]`, version `[114:134]`, vendor `[134:165]`, date `[165:]`), then split into *products a tech installs* vs *dependencies that ride along* (Rockwell Add-On Profiles, VC++/.NET/SQL/VS shells, OPC redists, CodeMeter). 60–70 % of rows are dependencies — plan from the product list only. Flag EOL items (Flash, .NET 1.1, MSXML 4) and server roles that don't belong on a laptop (TIA Multiuser **Server**).
2. **Decide need from the field, not the list.** Rockwell Logix Designer major version must equal controller firmware major rev (KB 22724); View Designer major = PanelView 5000 firmware major; TIA project extension = version (`.ap13/.ap15/.ap15_1`, upgrade is one-way). So the version list == set of firmware revs in service. Engineers rarely hold this — discover it: CIP ListIdentity on UDP 44818 (what RSWho sends) + TCP 102 connect for S7, then scan the project share for extensions/ACD headers. Scripts: `references/ot-discovery.md`.
3. **Pick the mechanism per product** using the silent-install table in `references/installer-switches.md`:
   - no silent installer → **golden image** (sysprep → WinPE → `dism /Capture-Image`);
   - silent-capable → **RMM-run resumable script** from a LAN share;
   - PPKG only for join + a few MSIs at OOBE; Intune Win32 only if <30 GB and internet pull is acceptable; winget only for commodity apps.
4. **Write the plan .md first** (architecture with one Mermaid flow, click-by-click image build, script internals, RMM setup, licensing, verification, rollback), then the script. Delegate the script; keep `# VERIFY` on every switch not confirmed by vendor docs — never assert a switch you haven't seen documented.
5. **Test on one laptop**: `-DryRun`, then live, then confirm each VERIFY switch. Only then fleet.

## Always-on rules
- Image carries software, never identity: strip node-locked licences (FactoryTalk Activation rehost, ALM key to USB) and the RMM agent (per-install device ID) before `sysprep /generalize`; never boot back into Windows after sysprep.
- Resumable install scripts: startup **scheduled task** for resume (RunOnce needs a logon — a laptop parked at the login screen never resumes); `shutdown /r /t 30` not `Restart-Computer` so the script can still return 3010 to the RMM; a `-Detach` launcher so RMM job timeouts can't kill a 45-min installer; version-pinned detection (a golden image already holds older same-name components).
- PPKG domain join: creds live in the file → encrypt; join account = plain domain user in the service-accounts OU, delegated Create/Delete Computer objects on the target OU; `redircmp` so joins land in an OU — containers (`CN=Computers`) have no `gPLink`, GPO can't attach.
- OT scans: single targeted query per port (UDP 44818 ListIdentity, TCP 102 connect); never broad nmap/`-A` on PLC VLANs (old PLCs fault). Tell the firewall owner before a burst to 2k addresses.
- Scripts delivered over Telegram land in `Downloads`: give `cd $env:USERPROFILE\Downloads` before `.\script.ps1`, one short command per code block. Andrew runs them on the work laptop; this box has no Windows — say "parsed, not executed" plainly.

## Artifacts
the controls-laptop software-standard and deployment-plan reports, the layer-2 install script, and `controls-inventory/` (all under `~/reports/`) (inventory xlsx, `scan-projects.ps1`, `discover-plcs.ps1`).
