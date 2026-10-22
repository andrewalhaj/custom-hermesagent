# Endpoint onboarding without Intune — PPKG, imaging, service account

## Decision (ask first: is Intune/Autopilot available?)
| Situation | Method |
|---|---|
| Intune + Entra P1 licensed | Autopilot + Intune. No image, no USB. The target state; everything below is the bridge. |
| AD-joined fleet, mixed laptop models, no Intune | **PPKG** (Windows Configuration Designer). Model-agnostic, ~10 min to build, applied at OOBE from USB. |
| Same model, heavy identical app stack, no Intune | Sysprep + DISM image, deploy with MDT/`dism /Apply-Image`. Driver-bound, sysprep fragile. |
| Vendor stack with non-silent installers (PLC tools: pre-v30 Studio 5000, RSLogix, TIA) | Image for the non-silent layer + RMM-run resumable script for the rest — see *Automating a heavy vendor stack* below. |

`.ppkg` is **authored**, never captured from a laptop. A `.wim` is captured — but only from a clean reference build, never a user's machine. If the user says "grab an image/ppkg from this laptop", untangle the two words before proceeding.

## PPKG build (WCD, Store app)
0. Prep: dedicated AD join account (below); silent installers staged in one folder, each tested by hand with its silent switch (a UI prompt hangs the whole package).
1. WCD → Advanced provisioning → name `<ORG>-Onboard-v1` → All Windows desktop editions → skip import → Finish.
2. Runtime settings:
   - `Accounts/ComputerAccount`: `Account` = `svc-domainjoin@<dns-domain>` (UPN; if NetBIOS form, read the pre-Windows-2000 name in ADUC — it often differs from the DNS name), `AccountOU` = laptop OU DN, `ComputerName` = `<ORG>-%SERIAL%`, `DomainName`, `Password`.
   - `Accounts/Users`: local break-glass admin, UserGroup Administrators.
   - `OOBE/Desktop/HideOobe` = TRUE.
   - `ProvisioningCommands/PrimaryContext/Command`: one per installer, `ContinueInstall` TRUE, `RestartRequired` FALSE, dependent files attached under the same command.
   - `ConnectivityProfiles/WLAN` if no wired drop at OOBE — the laptop must reach a DC during apply.
3. Export → Provisioning package → **Encrypt** (the join creds are inside) → sign if a code-signing cert exists → `.ppkg` to the root of a FAT32 stick.
4. Apply: first OOBE screen → insert USB (auto-prompt) or Windows key ×5 → organisation setup → install package → password → joins, renames, installs, reboots to domain logon.
5. Bump the version in the name on every change; Windows will not re-apply a same-named package.

Hybrid Entra join follows automatically once the computer object lands in an OU inside the Entra Connect sync scope and an SCP exists — no PPKG setting needed.

### Desktop wizard variant ("Provision desktop devices") and apps-only
- Pages: Set up device → Set up network → Account Management → Add applications → Add certificates → Finish. The wizard's AD join has **no OU field** (only the advanced editor's `AccountOU` does): machines land in the domain's default computer container. Either use the advanced editor, or on a DC run `redircmp "OU=Laptops,DC=<domain>,DC=<tld>"` once — this redirects *every* future join (manual ones too); flag that reach before running it.
- `CN=Computers` / `CN=Users` are containers, not OUs: the `container` class has no `gPLink`, so GPMC cannot link policy to them (machines there get only domain/site-linked GPOs), and they are normally outside Entra Connect's OU filter, so nothing in them hybrid-joins. Create a real OU for laptops, add it to the Connect sync scope, redirect with `redircmp`.
- Apps-only package: Set up network Skip; Account Management — type nothing (the `Contoso\ScottLock` text is placeholder), Next; Add applications: one row per installer with its command line and return codes `0` / `3010`; prerequisites (VC++, .NET) first. Apply by double-click or Settings → Accounts → Access work or school → Add a provisioning package. Verify per-command exit codes in Event Viewer → Applications and Services Logs → Microsoft → Windows → Provisioning-Diagnostics-Provider.
- Join creds entered on the wizard page (`<NETBIOS>\svc-...` or UPN both work) are stored in the package → tick **Encrypt package** on Finish. Join silently fails (apps still install) when no DC is reachable at apply time — wired LAN or the Wi-Fi page filled in.
- Post-apply check: `dsregcmd /status` → `DomainJoined : YES`; the computer object must appear in the laptops OU, not `Computers` — in `Computers` means `redircmp` did not take.

### Delegation ACL for the join account (Delegate Control → custom task on the laptops OU)
- Scope: *Only the following objects* → Computer objects, tick Create / Delete selected objects.
- On descendant Computer objects: Reset Password, Write Account Restrictions, Validated write to DNS host name, Validated write to service principal name. Nothing else; not Domain Admins / Account Operators.
- Without delegation any domain user still joins 10 machines (`ms-DS-MachineAccountQuota`) — fine for one test, wrong for a fleet account because the objects land in the default container.

## Installed-programs dump → software standard
When a raw export (Win32_Product / Uninstall-registry dump, fixed-width columns) arrives with "make that readable", the deliverable is a **product list, not a prettier table**: a Telegram summary of the ~40–50 real products grouped by vendor, plus `~/reports/<org>-<role>-laptop-software-standard.md` with sections *Install list* → *Comes along automatically* → *Flags* → *Packaging notes / install order* → appendix (full inventory by category). Parse in execute_code, never by hand:
- Fixed-width columns in the Windows export: name `[0:114]`, version `[114:134]`, vendor `[134:165]`, install date `[165:]` — verify with `.index()` on one line; regex on 2+ spaces breaks because vendor names contain double spaces.
- Rows with a blank name and vendor `Rockwell Automation` are Add-On Profile sub-MSIs (hundreds); count them, do not list them.
- Auto-installed dependencies to collapse into one line each: Rockwell/third-party *Module Profiles* (match `profile`), VC++ redists, .NET targeting packs/runtimes, SQL Server components + LocalDB, VS Shell/Build Tools/Help Viewer, OPC Foundation redists, CodeMeter, Siemens SeCon/Automation Access Control/Event Database.
- Flags to call out every time: EOL components (Flash ActiveX, MSXML 4, .NET 1.1, SQL 2008 files), and the count of parallel PLC-tool versions (each Studio 5000 / RSLogix 5000 / TIA major is a separate multi-GB install and licence slot — ask which firmware revisions are actually in the field before cloning all of them).
- Controls (PLC) laptops: Siemens and Rockwell installers are not winget-able and tens of GB — stage on a share, not inside the PPKG; install order VC++/.NET → FactoryTalk Services Platform → RSLinx → Studio 5000 oldest→newest → View Studio → TIA oldest→newest → WinCC RT → ALM → Acrobat.

## Automating a heavy vendor stack ("is PPKG the right method?" → no)
- PPKG runs its commands once, blind, with no reboot orchestration or resume — wrong for 100+ GB stacks with forced reboots. Intune Win32 caps at 30 GB per package and pulls over the internet. Answer with a two-layer design and say which products force it:
- **Silent-install facts that decide the split** (verified against Rockwell docs / SiePortal forum): Studio 5000 Logix Designer **v30+** and all FactoryTalk-era installers support unattended `Setup.exe /Q /IAcceptAllLicenseTerms [/AutoRestart] [/SetupLanguage=ENU] [/Product="…"]` (exit 0 / 1641 / 3010). **Studio 5000 v23–v29 and every RSLogix 5000/500/5 have no supported unattended mode.** TIA Portal silent install exists only via a copied/edited `steuerdatei.ini` + `Start.exe` — community-known, not Siemens-documented; default it into the image.
- **Layer 1 golden image** (sysprep /generalize + DISM capture to .wim) holds the non-silent legacy set; it is frozen software for frozen controllers, so image staleness does not apply. Strip the RMM agent and all node-locked licences (FactoryTalk Activation, ALM, CodeMeter) before capture; agent + activations go on per laptop after deploy.
- **Layer 2 script** (PowerShell 5.1, declarative step table, JSON state file, idempotent detect rules, transcript log, exit 0/1/2/3010) installs everything v30+/FT/Siemens-runtime/Adobe/Dell from a LAN share. Deliver it through the RMM already on the laptop (the employer's RMM) as a scheduled automation run as SYSTEM against a device group — not PPKG, not Intune. SYSTEM reads the share via `Domain Computers` read ACL. Design rules that matter:
  - Resume after a reboot via a **SYSTEM scheduled task triggered at startup**, not HKLM RunOnce — RunOnce fires only at user logon, and a laptop parked at the login screen never resumes.
  - Reboot with `shutdown /r /t 30` and `exit 3010`, never `Restart-Computer` — the script must return 3010 so the RMM shows *reboot pending* instead of *failed*. Pass `/Q /IAcceptAllLicenseTerms` without `/AutoRestart` so the script, not the installer, owns the reboot.
  - Give the RMM a `-Detach` launcher mode (register the task, start it, return in seconds) — an RMM job timeout or agent restart would otherwise kill a 45-minute Studio 5000 install half-way.
  - Pin the version in every detection rule (`DisplayName` + `DisplayVersion`) — the golden image already carries older FactoryTalk/RSLinx components from the legacy installs, and name-only detection skips the upgrade.
  - Mark every switch not confirmed by vendor docs `# VERIFY` (Siemens ALM/TIA Administrator/WinCC RT, Acrobat MSI name, Dell Command Update, the small Rockwell utilities); only the Rockwell `/Q` family is documented. First run on a test laptop is `-DryRun`.
- Explaining the script when asked "what does it do / does it inventory?": three sentences first — *it carries the target list, compares the machine against it (registry Uninstall keys, exact version), and installs the gaps; finished laptop → does nothing, fresh image → installs everything missing* — then mechanics. Linux can only prove the `.ps1` parses (download PowerShell 7 into scratch and `Parser::ParseFile`); only a Windows test box proves it runs — say that ceiling plainly instead of calling a parse a test. Layer 1 has no script: the image is a procedure (sections 6–7 of the plan) plus `capture.cmd` / `apply.cmd` / `layout.txt` on the WinPE stick if he wants typed-from-photo relief.

## Stripping the list — need is decided by the controller inventory, not the laptop
- Mechanism to state up front (verified): Rockwell software **major version = controller firmware major revision** (Rockwell KB 22724); a TIA project's extension is its version (`.ap13` / `.ap15` / `.ap15_1`) and projects only upgrade forward; View Designer major = PanelView 5000 firmware major; current FT View ME / RSLogix 500 / RSLogix 5 each cover all older projects. So every parallel PLC-tool version maps to a firmware rev someone programmed, and the only ways a version leaves the list are (a) no controller at that rev is in service, or (b) production flashes the controller — a downtime decision, not IT's.
- Deliverables: (1) an inventory `.xlsx` for the controls engineers — one row per PLC (site, machine, model, firmware major rev as a number, HMI, TIA project version, still-in-service, OK-to-flash dropdowns) plus a *Software versions* tab that `COUNTIF`s controllers per version (zeros = drop candidates); (2) `scripts/scan-projects.ps1` run read-only against the project share — TIA by extension, `.ACD` by the `LastModifiedTool … Version="NN` text header in the first 64 KB (community-documented; confirm on one known file), `.RSS`/`.RSP` counts. The share shows history, the engineers show what is live — the sheet is the authority.
- Strip with no inventory: Flash Player ActiveX, TIA Tour, NCM GPRS, Logix Designer SDK, Studio 5000 Samples/Online Books/Start Page Media, Dell SupportAssist (keep Command | Update), and **TIA Portal Multiuser Server** — a server role (Siemens "TIA Project-Server"); laptops need only the Multiuser Client.
- Rockwell KB and theautomationblog pages are login-walled to `web_extract`; cite from search snippets or the industrialmonitordirect knowledgebase mirrors.
- Pin the version list before building: the 18-version Controls list shrinks the image to 2–3 legacy installs if the field only runs a few firmware revs. If the user says "assume the list is accurate", build for all of it and delegate the plan + script to a child (`~/reports/<org>-controls-laptop-deployment-plan.md`, `~/reports/<org>-controls-layer2-install.ps1`).

## Domain-join service account
- Purpose: the credential inside the PPKG that creates the computer object. Never an admin account — the password sits in the package on a shared stick and in WCD's project file in plaintext.
- Create `svc-domainjoin` in the existing **Service Accounts OU** (not the default Users container). Long random password, *User cannot change password*, *Password never expires* (calendar the rotation; a rotation silently breaks the package), *Account is sensitive and cannot be delegated*, Domain Users only.
- Rights come from **Delegate Control on the laptops OU** (not from where the account lives): custom task → Computer objects → Create/Delete only. Worst-case leak = a rogue machine joined, not a breach.
- The service-account OU should be outside Entra Connect sync scope (no cloud identity to license/MFA), carry a GPO denying *Log on locally* and *through RDS*, and sit under a fine-grained password policy with no expiry.

## Admin tooling on a laptop with no AD tools
- Fastest: RDP to a DC → Server Manager → Tools → ADUC. Otherwise RSAT, one capability per command (`Rsat.ActiveDirectory.DS-LDS.Tools~~~~0.0.1.0`, `Rsat.GroupPolicy.Management.Tools~~~~0.0.1.0`, `Rsat.Dns.Tools~~~~0.0.1.0`).
- Laptop not domain-joined → ADUC finds no domain: `runas /netonly /user:<NETBIOS>\<admin> "mmc dsa.msc"` then Change Domain; needs line-of-sight to a DC. RSAT 0x800f0954 = WSUS blocking Features-on-Demand (registry bypass or temporary WSUS opt-out).

## One-off laptop imaging (preserve / clone, not deploy)
- Preserve a user's machine before reissue → Veeam Agent for Windows (free, own recovery ISO, dissimilar-hardware restore). Disk swap → Macrium Reflect. Dead/unbootable source or Linux → Clonezilla.
- Suspend or decrypt BitLocker first, or capture with a tool that takes the key — Clonezilla captures ciphertext you cannot mount.
- Location-bound licenses (Siemens ALM keys) die inside any image: move them out before capture (skill `siemens-alm-licensing`).
