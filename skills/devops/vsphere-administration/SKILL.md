---
name: vsphere-administration
description: "Use when administering vSphere/vCenter: VMs, roles, hosts."
---

# vSphere / vCenter administration (Andrew learning track)

Andrew is learning vSphere on the job. Every answer: **mechanism in 1–3 lines first** (the model he can restate), then **numbered click-by-click** for the HTML5 client, then the licensing consequence if there is one. Contrast with things he already knows (M365, AD, Hyper-V) when it sharpens the point. Terse in chat.

## Always-on rules
- Read the screenshot before diagnosing: error page vs login page, URL (`/ui/error.jsp` = post-auth), inventory tree (hosts with red alert icons, VM naming convention). Name the VM to the existing convention (`<employer-prefix>-xxx`), never invent a scheme.
- Flag a red-alert host in the tree even when unasked, and never place new VMs on it — cheap to say, expensive to miss.
- Never put real IPs/hostnames in shared artifacts (chat with Andrew is fine).
- Every new Windows guest = a Windows Server licensing question. Datacenter on the host → unlimited VMs; Standard → 2 VMs per 16-core license per host — check allocation before adding.

## Login / permissions
- "Unable to login because you do not have permission on any vCenter Server systems" = **auth succeeded, authz failed**. Identity source (AD) only answers *who*; roles are role × object × propagate and default to none for every AD principal. Bad password errors on the login page itself.
- Causes in order: no role granted; wrong login format when AD isn't the default identity source (use `user@domain` or `DOMAIN\user`); stale group token after membership change (full logout / wait).
- Fix: login `administrator@vsphere.local` → ☰ Administration → Access Control → Global Permissions → Add → AD domain → group (prefer a group like `vSphere-Admins`) → role → Propagate to children. If administrator@vsphere.local gets the same error, the SSO Administrators group is empty → VCSA shell route.

## New VM (three decisions: compute, datastore, port group)
1. Get the ISO onto a **shared** datastore first. Browse existing datastores' Files tabs for an `ISO`/`<os>_OS` folder before downloading — the media the sibling VMs were built from is usually still there (a 2022 VM's CD/DVD setting in Summary → VM Hardware may still name it). Two traps: `vmimages/tools-isoimages/windows.iso` is VMware Tools (~100 MB), not an installer (4–6 GB); and a host-local datastore (`.<n>_datastore1_Local_`) only shows in the ISO picker when the VM runs on that host — tick the ISO → **Copy to** → the shared datastore (`ISO/` folder) instead of migrating the VM to the host. Upload path if nothing exists: Storage view → datastore → Files → Upload Files.
2. Right-click cluster → New Virtual Machine → Create new → name + datacenter → compute (cluster; if refused, DRS is off → pick a healthy host) → datastore (most free, default policy) → compatibility default → guest OS family/version.
3. Customize hardware: right-size small (ALM-class server = 2 vCPU/4 GB/60 GB **thin**), VMXNET3 NIC on the port group the peer servers use (check a sibling VM → Summary → Networks), CD/DVD → Datastore ISO File → **Connect At Power On**.
4. Power on → Launch Web Console → install OS → Actions → Guest OS → Install VMware Tools (in the guest: mounted DVD → `setup64.exe` → **Typical**; Complete is only for VMs that move between VMware products) → reboot → static IP, DNS, domain join, updates. Tools before anything else: until it's in, the NIC driver, mouse and clean shutdown are all degraded, and vSphere Summary can't show the guest IP.

## Datastore choice (wizard step 4) — explain "safe" as shared / not overpromised / headroom / blast radius
- Rule out host-local (`<host> Local Datastore`): no HA or vMotion, VM dies with the host.
- Rule out thin-overcommitted (Provisioned > Capacity): a fill pauses every VM on it.
- Respect the per-VM-volume convention: never a second VM on another VM's LUN (esp. vCenter's or OT volumes). Small shared infra → the general-purpose datastore; convention-correct later = own volume + Storage vMotion (location-bound keys off first).

## Hardware / options defaults that bite
- Wizard pre-selects the first alphabetical port group — in the employer's cluster that is the **iSCSI storage network**. Re-pick the servers' port group every time; adapter VMXNET 3.
- CD/DVD defaults to Client Device → Datastore ISO File + Connect At Power On. Disk defaults thick → Thin.
- Compatibility default = highest version all hosts support; leave it. It reveals the ESXi level (the employer's fleet is on an ESXi release past end of general support; upgrade conversation pending).
- VBS: off for headless infra servers (nested-virt dependency on every host, can't flip on later); on for DCs / jump boxes.
- VM Options: confirm EFI firmware; leave guest time sync off for domain members (DC is the time source).
- The license server VM carries a generic role name chosen by Andrew rather than a product-specific one (exact name: memory_search "vSphere network facts license server VM").

## Console / boot
- Copying a shared ISO onto the shared/general datastore is fine despite the one-volume-per-VM convention — that convention is for VM disks; shared media goes in `ISO/` on the shared store, never on a datastore with a warning/alert icon. Check free space (ISO + thin disk growth during install) first.
- `Time out.` / `→ EFI Network...` at power-on = nothing bootable: either the CD isn't Connected, or the 5-second `Press any key to boot from CD or DVD` prompt was missed (EFI falls through to PXE). Click into the console for focus, Actions → Power → Reset, press a key at the prompt; or tap `Esc` at the logo → Boot Manager → EFI VMware Virtual SATA CDROM. Edit Settings while powered on → Datastore ISO File → Connected if it wasn't.
- Windows Setup picks for an infra/app server: edition **Standard (Desktop Experience)** unless the hosts are Datacenter-licensed (then Datacenter is free and consistent with siblings — check `winver` on one); Core only for admins who live in PowerShell. Installation type **Custom**, select the unallocated disk, let Setup partition.
- The web console has **no clipboard** (`isolation.tools.copy/paste.disable` default TRUE per VM). Do the minimum there — VMware Tools, static IP, enable Remote Desktop — then switch to `mstsc` for everything else. Don't set a random-generated local admin password through the console; do it over RDP where the manager → clipboard → server path works, or type a passphrase. Note the console's keyboard layout (`Enforce US Keyboard Layout` button) before typing any password.
- EFI Boot Manager menu at first power-on = nothing bootable: the disk is empty and the CD has no ISO behind it (still Client Device or not Connected). Edit Settings while powered on → Datastore ISO File → Connected, then pick the SATA CDROM entry in the menu; hit a key at "Press any key" or Reset and retry.
- Windows guest product key: Datacenter-licensed hosts → AVMA key for the edition, activates against the host; Standard → a VLSC key and one of the 2-per-16-core slots.

## Wizard walkthrough etiquette
- Andrew steps through wizards one screenshot per message. Every reply = the pick + the one-line why, ending in prose. A turn that ends on a tool call (memory_add, file edit) reads as silence and earns a "?" — finish the tool work, then answer, in the same turn.
- Paginated pickers (datastores, port groups): read every page before choosing; the right option is often on page 2.

## Pitfalls
- Employer network facts (subnet/scope, DHCP exclusions, DC/vCenter/ESXi/license-server addresses, installer paths, Siemens customer no.) are not in this file — it is published: memory_search "vSphere network facts license server VM".
- Free-IP method: DNS A records for the block (`Get-DnsServerResourceRecord -ZoneName <zone> -RRType A | ? IPv4 -like '<server-subnet>.*'`) + a 1–254 `Test-Connection -Quiet` sweep from a DC; take a number absent from both, above .20, next to the sibling servers; confirm with `Test-NetConnection` + `arp -a` right before setting. Prefer a DHCP reservation when there is no server exclusion range.
- Post-join rename is fine (`Rename-Computer` renames the AD object and re-registers DNS); verify `nltest /sc_query:<domain>`, `Resolve-DnsName`, `Get-ADComputer`, delete the stale `WIN-xxxx` A record, move the object out of `Computers` into the servers OU. `Test-ComputerSecureChannel -Repair` if nltest fails.
- Imaging/cloning a guest that holds location-bound licenses (Siemens ALM keys) kills the license — see skill `siemens-alm-licensing`; move keys out before disk-level work on the VM.
