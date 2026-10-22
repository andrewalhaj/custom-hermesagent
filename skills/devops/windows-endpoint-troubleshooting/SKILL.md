---
name: windows-endpoint-troubleshooting
description: "Windows fleet triage: domain/hybrid join, profiles, GPO"
category: devops
version: 1.0.0
triggers: ["slow PC", "domain join", "hybrid join", "new profile", "GPO", "gpresult", "dsregcmd", "windows event log", "service crashing"]
---

# Windows Endpoint Troubleshooting (previous-employer fleet)

Diagnosing Windows endpoint issues for Andrew's work environment. He relays from a Windows laptop via **NinjaOne remote terminal (runs as SYSTEM)** and pastes screenshots back — give exact pasteable PowerShell, expect output as photos of screens.

## Environment facts

- AD domain, DC hostname and address: not in this file: `memory_search "previous employer AD domain DC"`.
- PCs hybrid Entra-join via SCP + Entra Connect. **Hybrid join is two-step**: domain join instant, Entra half completes only after Entra Connect syncs the computer object — `AzureAdJoined: NO` right after a domain join is normal lag, re-check later. `AzureAdJoined: YES` + `DomainJoined: YES` = hybrid (one state, not two joins). PRT check: `dsregcmd /status` → SSO State → `AzureAdPrt: YES`.
- Omnissa (ex-VMware EUC: Horizon/Workspace ONE) clients on some PCs. Windows MDM/RMM = NinjaOne; Macs = Jamf (see `apple-mdm-jamf`).
- Coworker Alex Bazzi handles mailbox/password resets — check with him when auth-loop symptoms coincide with resets.

## Diagnosis discipline (Andrew's standard)

1. **Mechanism, not coincidence** — every claim needs a checkable cause ("component X took N ms"), never "probably the join."
2. **Reversible test before destructive fix** — stop/disable a suspect service and observe, don't uninstall first. Uninstall destroys evidence and (for Workspace ONE Hub) can MDM-unenroll the device.
3. **Order-of-events check** — "started 2h ago" + "thing changed today": confirm which came first before declaring causality.
4. **Name the pegged resource** — Task Manager/resmon: which of CPU/disk/network/memory dominates narrows cause faster than any theory.

## SYSTEM-shell gotchas (NinjaOne terminal)

- `gpresult /r` as SYSTEM → "The user HQ\MACHINE$ does not have RSoP data" — NOT a failure. Use `gpresult /r /scope computer` or `gpresult /r /user HQ\<username>` (get user via `quser`).
- `whoami` shows the machine account with `$` suffix = you're SYSTEM.
- Stop-Service/Set-Service need elevation — fine from SYSTEM, fails from a user shell.

## PowerShell pitfalls (hit live)

- **"terminated unexpectedly" = event ID 7031** (service crash with recovery action), not 7034. When unsure, filter `ProviderName='Service Control Manager'` + message match instead of guessing IDs.
- **`Get-Service *foo*` matches the internal service NAME**, not display name. Event Viewer shows display names. Use:
  ```powershell
  Get-CimInstance Win32_Service | Where-Object {$_.DisplayName -match 'foo'} |
    Select Name, DisplayName, State, StartMode, PathName
  ```
  `PathName` identifies the owning product (which installer to repair/remove).
- **`C:\Users\<user>` folder CreationTime is unreliable** — new profiles copy `C:\Users\Default` and can inherit the image build date. Real profile birth: User Profile Service/Operational log (Ids 1,2,5), Security 4624 first logon for the account, or eyeball AppData sparseness (year-old profile has a year of cruft).

## Profile identity model

- Profiles are keyed to **SID**, never username. New sign-in principal (local → domain, Entra → domain) = new SID = fresh profile even for the same human.
- Folder name collision → suffix: `C:\Users\bentley.HQ`. The suffix means "sign-in identity changed", NOT "device got joined" — the join only enabled the identity switch.
- Map SIDs to folders: `HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\*`. SID prefix reveals old account type: `S-1-5-21-<machine>` = local, `S-1-12-1-` = Entra.
- Suffix-less domain profiles from earlier dates on the same box = machine was domain-joined long ago; today's event was per-user, not per-device.

## Sudden-slowness playbook

Slowness after join/identity change = **(unreachable domain resources) × (timeout each)**, or first-login churn, or an unrelated malfunction. Check in order:

1. **DC reachability + latency**: `nltest /dsgetdc:HQ`; `w32tm /stripchart /computer:<DC> /samples:3` (<10ms = LAN-grade, ruled out; 50–150ms = VPN path suspect); `Test-NetConnection <DC> -Port 445` (SYSVOL — blocked 445 = finds DC but stalls pulling policy) and `-Port 389`.
2. **First-login churn stack** (self-resolving; day-2 login dramatically faster = this was it): Search indexer rebuilding, Outlook OST download, OneDrive KFM resync, first-ever synchronous GP apply. Verify via resmon: SearchIndexer/OneDrive/Outlook dominating disk+network.
3. **Crash-looping services**: System log, SCM provider. Loop cost = crash → WerFault dump (CPU+disk) → restart → repeat. `Get-Process WerFault` cycling confirms. Reversible test: stop+disable suspect, observe 5 min.
4. **GPO payloads**: logon scripts, drive maps to dead shares (`net use` — Unavailable state hangs every File→Open dialog all session), folder redirection, "Always wait for network" policy. Per-phase ms timings: Event Viewer → GroupPolicy → Operational (8001/5016).
5. **Recent change scan**: `Get-HotFix | Sort InstalledOn -Descending`; MsiInstaller events last 24h; System errors/warnings ±1h of onset.

## Adjacent quick answers

- **Password reset does NOT clear MFA or Windows Hello** — MFA methods live in Entra independent of password; Hello PIN unlocks a TPM key, password not in that chain. Resets DO break stored-literal creds: app passwords, scan-to-email, mailing tools (auth-retry loops start the moment the reset lands).
- **Voice VLAN requests**: only correct for hardphones (LLDP-MED advertises voice VLAN; PC behind phone stays on data). Softphone on PC → wrong fix. UniFi way: port → keep native data VLAN, set Voice VLAN field — never change the native network. Check port stats (errors, 100/half negotiation) before VLAN theories.
- **Hybrid join slowness fears**: the join state itself costs nothing; DCOM permission-error events are chronic background noise on most boxes — ignore unless they're the only lead.
