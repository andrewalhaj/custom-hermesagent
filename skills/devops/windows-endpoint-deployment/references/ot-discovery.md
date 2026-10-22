# Finding which software versions the field actually needs

## Mechanism
- Rockwell: Logix Designer **major version = controller firmware major rev** (KB 22724). View Designer major = PanelView 5000 firmware major. FT View ME / RSLogix 500 / RSLogix 5: one current version covers all.
- Siemens: TIA project extension = version (`.ap13`, `.ap15`, `.ap15_1`, archives `.zapNN`); opening in a newer version upgrades one-way. V13 needed only while un-migrated `.ap13` projects exist.
- The needed version list == set of firmware revs (and TIA project versions) in service. Flashing a controller to current firmware is what retires a software version — production's call, not IT's.

## Live discovery (read-only)
- EtherNet/IP devices answer CIP **ListIdentity** (UDP 44818, ENIP command 0x0063, 24-byte header, empty body) with product name, serial and firmware major.minor — same query RSLinx RSWho sends. Reply layout: item type @26, rev major @54, minor @55, serial @58, name len @62, name @63.
- S7 CPUs: TCP 102 answers; firmware needs TIA → Online → Accessible devices (or an SZL read).
- Pure PowerShell 5.1 implementation: `~/reports/controls-inventory/discover-plcs.ps1 -Subnet 10.x.x.0/24[,...]` → CSV + Studio 5000 version tally. ~20 s per /21.
- Office LAN usually returns nothing — machine networks are separate VLANs or behind cell NAT/Stratix; get the subnet list from the core switch (`show vlan`, `show ip route`) or the firewall address objects, or walk the floor and plug in per cell.
- Alternatives: RSLinx RSWho per subnet (manual, zero setup); `nmap -sU -p 44818 --script enip-info` / `nmap -p 102 --script s7-info` — those two ports only, no `-A`.

## History from the project share
- `scan-projects.ps1 -Path \\server\Projects`: counts TIA projects by extension, `.ACD` by last-saved version (text header `LastModifiedTool=... Version="NN..."` in the first 64 KB — community-documented, verify on one known file), `.RSS/.RSP` for RSLogix 500/5.
- Share = history, not what's live; a project for a scrapped machine still sits there. Live discovery is the authority.

## Inventory sheet
`Controller-Inventory.xlsx`: Controllers tab (one row per PLC, firmware major rev column) feeds COUNTIF tallies on the Software versions tab — zero-count versions are drop candidates.
