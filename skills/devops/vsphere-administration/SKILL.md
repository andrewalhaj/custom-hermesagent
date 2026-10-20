---
name: vsphere-administration
description: "Use when administering vSphere/vCenter: VMs, roles, hosts."
---

# vSphere / vCenter administration (DTG fleet, Andrew learning track)

Andrew is learning vSphere on the job. Every answer: **mechanism in 1–3 lines first** (the model he can restate), then **numbered click-by-click** for the HTML5 client, then the licensing consequence if there is one. Contrast with things he already knows (M365, AD, Hyper-V) when it sharpens the point. Terse in chat.

## Always-on rules
- Read the screenshot before diagnosing: error page vs login page, URL (`/ui/error.jsp` = post-auth), inventory tree (hosts with red alert icons, VM naming convention). Name the VM to the existing convention (`DTG-xxx`), never invent a scheme.
- Flag a red-alert host in the tree even when unasked, and never place new VMs on it — cheap to say, expensive to miss.
- Never put real IPs/hostnames in shared artifacts (chat with Andrew is fine).
- Every new Windows guest = a Windows Server licensing question. Datacenter on the host → unlimited VMs; Standard → 2 VMs per 16-core license per host — check allocation before adding.

## Login / permissions
- "Unable to login because you do not have permission on any vCenter Server systems" = **auth succeeded, authz failed**. Identity source (AD) only answers *who*; roles are role × object × propagate and default to none for every AD principal. Bad password errors on the login page itself.
- Causes in order: no role granted; wrong login format when AD isn't the default identity source (use `user@domain` or `DOMAIN\user`); stale group token after membership change (full logout / wait).
- Fix: login `administrator@vsphere.local` → ☰ Administration → Access Control → Global Permissions → Add → AD domain → group (prefer a group like `vSphere-Admins`) → role → Propagate to children. If administrator@vsphere.local gets the same error, the SSO Administrators group is empty → VCSA shell route.

## New VM (three decisions: compute, datastore, port group)
1. Upload the ISO first: Storage view → datastore → Files → Upload Files.
2. Right-click cluster → New Virtual Machine → Create new → name + datacenter → compute (cluster; if refused, DRS is off → pick a healthy host) → datastore (most free, default policy) → compatibility default → guest OS family/version.
3. Customize hardware: right-size small (ALM-class server = 2 vCPU/4 GB/60 GB **thin**), VMXNET3 NIC on the port group the peer servers use (check a sibling VM → Summary → Networks), CD/DVD → Datastore ISO File → **Connect At Power On**.
4. Power on → Launch Web Console → install OS → Actions → Guest OS → Install VMware Tools → static IP, DNS, domain join, updates.

## Datastore choice (wizard step 4) — explain "safe" as shared / not overpromised / headroom / blast radius
- Rule out host-local (`<host> Local Datastore`): no HA or vMotion, VM dies with the host.
- Rule out thin-overcommitted (Provisioned > Capacity): a fill pauses every VM on it.
- Respect the per-VM-volume convention: never a second VM on another VM's LUN (esp. vCenter's or OT volumes). Small shared infra → the general-purpose datastore; convention-correct later = own volume + Storage vMotion (location-bound keys off first).

## Hardware / options defaults that bite
- Wizard pre-selects the first alphabetical port group — at DTG that is the **iSCSI storage network** (`VM iSCSI1`). Re-pick the servers' port group every time; adapter VMXNET 3.
- CD/DVD defaults to Client Device → Datastore ISO File + Connect At Power On. Disk defaults thick → Thin.
- Compatibility default = highest version all hosts support; leave it. It reveals the ESXi level (DTG = 7.0 U2 → past end of general support; upgrade conversation pending).
- VBS: off for headless infra servers (nested-virt dependency on every host, can't flip on later); on for DCs / jump boxes.
- VM Options: confirm EFI firmware; leave guest time sync off for domain members (DC is the time source).
- DTG license server VM = `DTG-LS01` (Andrew's name — generic beats `DTG-ALM`).

## Console / boot
- EFI Boot Manager menu at first power-on = nothing bootable: the disk is empty and the CD has no ISO behind it (still Client Device or not Connected). Edit Settings while powered on → Datastore ISO File → Connected, then pick the SATA CDROM entry in the menu; hit a key at "Press any key" or Reset and retry.
- Windows guest product key: Datacenter-licensed hosts → AVMA key for the edition, activates against the host; Standard → a VLSC key and one of the 2-per-16-core slots.

## Wizard walkthrough etiquette
- Andrew steps through wizards one screenshot per message. Every reply = the pick + the one-line why, ending in prose. A turn that ends on a tool call (memory_add, file edit) reads as silence and earns a "?" — finish the tool work, then answer, in the same turn.
- Paginated pickers (datastores, port groups): read every page before choosing; the right option is often on page 2.

## Pitfalls
- Imaging/cloning a guest that holds location-bound licenses (Siemens ALM keys) kills the license — see skill `siemens-alm-licensing`; move keys out before disk-level work on the VM.
