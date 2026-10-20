---
name: windows-server-administration
description: "Use when administering Windows Server/AD DNS or DCs."
version: 0.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Windows Server, Active Directory, DNS, DTG]
    related_skills: [azure-portal-provisioning, veeam-bare-metal-restore]
    created_by: agent
---

# Windows Server / Active Directory administration

## When to Use
DNS records, domain controller tasks, or other Windows Server role work on DTG's on-prem servers, usually from a screenshot or a forwarded Teams request. Grow this skill with a section per role (DHCP, GPO, AD users) as those come up.

Andrew works on DTG's domain controllers over RDP and sends screenshots or Teams requests (often from Matt). He is the admin; no approval gates. Answer with the GUI path and the PowerShell equivalent, then a check command, its expected output, and what each failure means.

## Procedure
1. **Read the screenshot's exact values** with `vision_analyze` before saying "looks right". Hostnames like `wly7rl` vs `wly7rt` are often misread; compare letter by letter against the request.
2. **Say which DNS view the request touches.** DTG runs split-horizon DNS: `<ad-domain>` is hosted internally on the DCs (AD-integrated) and publicly at Network Solutions. A record added only at the registrar won't resolve on the office LAN/VPN; internal clients need the same record on the DC zone.
3. **Do the change.** GUI: Server Manager → Tools → DNS → Forward Lookup Zones → zone → right-click → New Alias (CNAME); leave "Allow any authenticated user to update" unticked. PowerShell:
   `Add-DnsServerResourceRecordCName -ZoneName <ad-domain> -Name <alias> -HostNameAlias <target>`
   A CNAME can't share a name with any other record; "already exists" = remove the stale A record first.
4. **Verify on the DC:** `Resolve-DnsName <fqdn> -Server 127.0.0.1` should show the CNAME chain, then an IP. A CNAME with no final IP = mistyped target. Then test from a client with no `-Server` (what users get); `ipconfig /flushdns` if a client caches the old answer.
5. **Hand back the next owner's step.** For CNAMEs to `*.azurewebsites.net`: an Azure "404 Web Site not found" or a certificate error over https means DNS is done and the App Service still needs the custom domain + certificate bound. Flag targets that look wrong for the purpose (e.g. a `-dev` app behind a production name). Offer a one-line reply for the requester.

## Entra / hybrid identity (DTG)
- On-prem AD is authoritative; Entra Connect (PHS) runs on **DTG-DR2021**. Connect Health agent is not installed, so the portal says "No servers found" - find the sync server from the MSOL_ account description, no RSAT needed: `([adsisearcher]'(&(objectClass=user)(sAMAccountName=MSOL_*))').FindAll() | % { $_.Properties.description }`.
- Password resets go in AD, not Entra (no writeback). PHS health on the sync box: `Get-ADSyncAADPasswordSyncConfiguration -SourceConnector <ad-domain>`, `Get-ADSyncScheduler`, Application-log events 656/657 from *Directory Synchronization*.
- Lost Authenticator (user deleted/restored the app - work accounts never survive restore): admins cannot issue a QR. Delete the stale Authenticator method on the user, issue a one-time Temporary Access Pass, user re-registers at aka.ms/mysecurityinfo -> Add method -> Authenticator. Adding from the app's own "+" is the path that loops.
- Read sign-in logs before blaming MFA: 50072 = enrollment required (clear worked), 50074 = MFA required/no method (loop), **50126 = wrong password** - repeated 50126 means mistyped/stale password, not an MFA problem. Microsoft Account Controls v2 = mysecurityinfo; Microsoft Authentication Broker = the Authenticator app itself. Before calling it a sync desync, confirm the account is plain in ADUC (Account tab: no must-change-at-next-logon, not locked) and Connect Health shows a recent error-free cycle; then a reset in AD with must-change UNticked (the cloud cannot satisfy that flag -> 50126 forever) settles it in ~2 min.
- **Partner-branded sign-in page (Ford, Dell...) = the partner's tenant.** The user is a B2B guest there; after your tenant authenticates the password, the partner's Conditional Access demands MFA and guests register it separately in that tenant unless the partner trusts inbound MFA. Your External Identities > Cross-tenant access page only shows your outbound side and cannot tell you whether they trust you. Fix the home-tenant MFA first, then read the partner attempt in sign-in logs: "satisfied by claim in the token" = they trust it; a register prompt = second Authenticator entry in their tenant; a hard block = their admin. Nothing on your side fixes a partner-tenant block.
- Reading AD from the laptop: DC list `nltest /dclist:<ad-domain>`, current DC `$env:LOGONSERVER`; module `Add-WindowsCapability -Online -Name Rsat.ActiveDirectory.DS-LDS.Tools~~~~0.0.1.0` (also installs ADUC/`dsa.msc`). If `Get-ADUser -Properties *` returns blank values for `Enabled`/`pwdLastSet` even as Domain Admin, stop debugging the module: RDP to a DC and read the Account tab in ADUC, which is authoritative. The NetBIOS domain is not derivable from the DNS name — check `$env:USERDOMAIN` (pre-2000 logon names).
- Flag, don't fix today: "Password never expires" + "User cannot change password" on a human account is a service-account pattern. Hybrid status of a device: `dsregcmd /status` (`AzureAdJoined` + `DomainJoined` both YES = hybrid-joined); identity is already hybrid via PHS, device join/writeback/Intune are the layers that may be missing and each needs the P1/Intune licence check (`Get-MgSubscribedSku`) first.

## Endpoint onboarding (no Intune yet)
- Default path: PPKG authored in Windows Configuration Designer, applied at OOBE; Autopilot is the project, imaging is the last resort. A `.ppkg` is never "extracted" from a laptop and a used laptop is never the image reference — say so when the request conflates them.
- The PPKG join credential is a dedicated `svc-domainjoin` in the Service Accounts OU with Create/Delete Computer objects delegated on the laptops OU only; never an admin account, never the default Users container.
- Build steps, service-account hardening, RSAT/`runas /netonly` for a non-joined laptop, and one-off imaging tool choice: `references/endpoint-onboarding.md`.

## Pitfalls
- `Resolve-DnsName ... -Server localhost` can time out while records are fine: `localhost` may go to `::1`, or the DC's first recursive lookup of an external CNAME target is slow. Retry with `-Server 127.0.0.1`, check the record alone with `-Type CNAME`, and test an external name (`microsoft.com`) to separate a forwarder/outbound-53 problem from a missing record. Confirm `hostname` too: the window may be the laptop, not the RDP session.
- AD-integrated zones replicate to the other DCs in minutes; no need to add the record on each DC.
- Andrew pastes my blocks from Telegram into PowerShell: a `>>` prompt or a cmdlet prompting for a mandatory parameter means the paste lost quotes/lines - tell him Ctrl+C and retype the one line by hand. Fence one command per block, no `#` comments.
- Incident write-ups for DTG: issue -> cause chain -> fix -> "for next time" bullets, first person, plain. Leave out dead ends and attempted steps unless he asks; "make it sound human" = the de-AI pass in skill linkedin-writing.
