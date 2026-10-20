# Endpoint onboarding without Intune — PPKG, imaging, service account

## Decision (ask first: is Intune/Autopilot available?)
| Situation | Method |
|---|---|
| Intune + Entra P1 licensed | Autopilot + Intune. No image, no USB. The target state; everything below is the bridge. |
| AD-joined fleet, mixed laptop models, no Intune | **PPKG** (Windows Configuration Designer). Model-agnostic, ~10 min to build, applied at OOBE from USB. |
| Same model, heavy identical app stack, no Intune | Sysprep + DISM image, deploy with MDT/`dism /Apply-Image`. Driver-bound, sysprep fragile. |

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
