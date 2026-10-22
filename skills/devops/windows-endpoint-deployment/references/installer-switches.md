# Silent-install reality table (OT / engineering vendors)

| Product | Unattended? | Switch | Source |
|---|---|---|---|
| Studio 5000 Logix Designer v30+ and all FactoryTalk-era installers (FTSP, Linx, A&E, Diagnostics, Activation Mgr, View Studio ME, ViewPoint, RSLinx Classic, ControlFLASH) | Yes | `Setup.exe /Q /IAcceptAllLicenseTerms [/AutoRestart /SetupLanguage=ENU /Product="..." /SerialNumber= /ProductKey=]`; `/QS` = quiet with progress; exit 0 ok, 1641/3010 reboot | Rockwell release notes / KB |
| Studio 5000 v23–v29, RSLogix 5000 v13–v20, RSLogix 500/5 | **No** supported unattended mode | — → golden image | Rockwell (silent support starts at v30) |
| TIA Portal V13/V15/V15.1, WinCC RT Advanced | Unofficial only | copy `steuerdatei.ini` from DVD root, strip the interactive parameter, run `Start.exe` with it — community (SiePortal forum), not Siemens-documented → prefer image, mark VERIFY | SiePortal forum |
| Siemens ALM, TIA Administrator | InstallShield-style | `/s /v"/qn REBOOT=ReallySuppress"` — VERIFY on test laptop | unconfirmed |
| Adobe Acrobat (Admin Console MSI) | Yes | `msiexec /i AcroPro.msi /qn /norestart` — VERIFY property names | Adobe |
| Dell Command \| Update | Yes | `<installer>.exe /s` — VERIFY file name per release | Dell |

Rules
- Omit `/AutoRestart`; the script owns reboots so it can resume.
- Install order: VC++/.NET → FactoryTalk Services Platform → Activation Manager → RSLinx → FT Linx/A&E/Diagnostics → Studio 5000 oldest→newest → View Designer → View Studio ME → ControlFLASH → utilities → ALM → TIA Administrator → WinCC RT → Acrobat → Dell.
- FTSP upgrade over an older image-resident FTSP is the step most likely to misbehave — test it alone first.
