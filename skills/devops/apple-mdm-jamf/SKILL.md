---
name: apple-mdm-jamf
description: "Apple MDM (Jamf, ABM/ADE): policies, smart groups, EAs"
---

# Apple MDM / Jamf Troubleshooting

Andrew manages Macs/iPads via Jamf Pro + Apple Business Manager (ServiceNow work context — he may relay errors secondhand from a Windows laptop, no direct Mac access mid-conversation).

## Jamf "policy not installing packages" triage — in order

1. **Get ground truth first**: `sudo jamf policy -verbose` + `tail -100 /var/log/jamf.log` on a target Mac, AND the policy's Logs tab in the console. Don't guess — the failure splits ~5 ways and the log collapses it to one.
2. Triage table:

| Symptom | Cause | Fix |
|---|---|---|
| Policy never runs | Wrong trigger, or frequency "Once per computer" already consumed by a prior failure | Set trigger to Recurring Check-in; **flush policy logs** to re-arm |
| "Could not download package" | DP sync incomplete / upload never finished | Re-upload; verify pkg shows size + checksum in Settings → Packages |
| Downloads, install fails | Bad pkg format or failing pre/postinstall script | See pkg format section; read install.log |
| "No policies were found" | Scope miss (smart group not matching, site mismatch) | Verify machine appears in scoped group |
| Runs but nothing installs | Package payload action set to **Cache**, not Install | Change to Install |

3. **"Installed but invisible" is a real end state** — see below before declaring failure.

## Detecting daemon-style agents (NinjaOne etc.) — CORRECTED 2026-07-22

Jamf's Applications inventory indexes `.app` bundles in subdirectories too, not just top-level `/Applications`. **NinjaOne DOES appear**: `njdialog.app` at `/Applications/NinjaRMMAgent/programfiles/njdialog.app` (versioned with the agent) and `ncstreamer.app` under `/Applications/NinjaRemote/`. Earlier claim that daemon agents "never appear in Applications inventory" was verified WRONG against live inventory.

- **Smart groups: key on `Application Title` does not have `njdialog.app`** (exact name, with the dot). Survives version bumps, catches agents installed outside Jamf.
- **Do NOT key on package receipts for Ninja**: receipt id is version-suffixed (`org.ninjarmm-macagent.13.0.7941`) so exact matches break every agent update, and `Packages Installed By Casper` only sees pkgs Jamf itself installed — manual/Ninja-deployed agents show as "missing" forever.
- A wrong/nonexistent value in a "does not have" criterion silently matches EVERY Mac — if a smart group pulls machines that clearly have the software, first suspect wrong criteria type or a typo'd value (e.g. `njdialog app` without the dot, or receipt criterion fed an app name).
- Ground-truth check on the Mac: `pkgutil --pkgs | grep -i <vendor>`, `ls /Library/LaunchDaemons | grep -i <vendor>`, `pgrep -il <vendor>`.
- End-to-end proof for RMM agents: the device appears in the vendor console (proves install AND registration/token).

### Smart group patterns
- Activity filter: `Last Check-in` `less than x days ago` `30` = only Macs active in last 30 days ("less than" = within). Use Last Check-in (every ~15 min online), not Last Inventory Update (daily, lags).
- Smart groups evaluate on **inventory (recon)**, not check-in. Test loop: `sudo jamf recon` on the Mac, or console → device → Management → **Update Inventory** (APNs, seconds–2 min if awake). MDM Update Inventory does NOT run policies; `sudo jamf policy` does (or wait out the check-in tick).
- Self-healing: scope the install policy to the "missing" smart group, trigger Recurring Check-in, frequency Once per computer — remediated Macs drop out after next recon.
- Self-refreshing group: policy with ONLY Maintenance→Update Inventory payload, trigger Recurring Check-in, frequency **Once every day**, scoped to the group (self-cleaning as members drop out). Never "Ongoing" — 15-min recons hammer the Jamf DB.
- OS version groups: operator **"less than"** does real version comparison (e.g. `less than 26.5.2` = outdated); "is / is not" is exact string match only. Pair with the activity filter.
- Config-profile coverage: `Profile Name does not have <exact name>` — use the `...` browse button to pick names instead of typing.

### Extension-attribute smart groups — the inflation trap (2026-07-22)
EA-based groups produced counts of 91 then 117 "not signed into OneDrive" before landing on the truth. Three compounding causes, all recurring patterns:
1. **`is not "<state>"` matches blank EAs** — every Mac that hasn't run recon since the EA was created has an empty value, and empty ≠ anything. It also matches every OTHER script output state (`Not installed`, `No user found`). **Always use positive match: `is "<exact state>"`.**
2. **Value must match script `<result>` output verbatim** — case, punctuation, spaces (`Installed, not signed in`, not `installed not signed in`).
3. **Detection markers must cover every install flavor** — first OneDrive EA only checked the standalone path; App Store (sandboxed Containers path) and File Provider (`~/Library/CloudStorage/OneDrive-*`) installs reported false negatives. Verify markers on a signed-in test Mac (`ls -d` each candidate path) BEFORE trusting fleet counts.
- Test loop: fix script → `sudo jamf recon` on test Mac → read the **computer record's EA value** first (ground truth), then the group membership.
- **EAs have NO scope** — the script runs fleet-wide at every recon; there is no scoping UI. Scope lives only on consumers (smart groups → policies). A blank EA value on a record = no recon since the EA was created, not a broken script. Avoid network-calling EAs (slow recon fleet-wide).
- Known-good scripts (Rosetta 2 install, OneDrive sign-in EA): `references/jamf-scripts-and-eas.md`.
- Jamf Connect sync EA, Entra WPJ-registration EA, NinjaOne agent-health EA + their smart groups: `references/jamf-connect-keychain-and-entra.md`.
- **EAs have NO scope** — every EA script runs on every Mac at every recon; scoping exists only on the smart groups / policies that consume the value. "Inventory Display" on the EA is cosmetic. Local plist/file-check EAs are negligible recon cost; avoid network-calling EAs.

## Jamf Connect / password reset / orphaned keychain (2026-07-23)

Any password reset WITHOUT the old password in the transaction (Entra portal, admin console, Jamf policy, `sysadminctl`, Recovery) leaves the login keychain encrypted to the OLD password — cryptographic, no admin override. Jamf Connect then force-syncs the local account hash (login works fine) but the keychain stays orphaned unless the user answers the old-password prompt or picks Update/Create at the macOS catch-net dialog. Symptom: Edge/Chrome constant M365 re-auth (Safe Storage key locked in wallet), Safari fine. 10-sec test: `security unlock-keychain ~/Library/Keychains/login.keychain-db` with the CURRENT password — fail = orphaned. **A PRT does not fix or survive this — it lives in the same keychain**; only Platform SSO Secure Enclave mode is structurally immune. Jamf Connect login ≠ Entra device registration: a whole Connect fleet can show zero macOS devices in Entra (no PRT → fleet-wide session-expiry re-auth). Full mechanism, repair runbook, replication recipe, PSSO deployment notes, Defender-without-registration facts: `references/jamf-connect-keychain-and-entra.md`.
- Identity/agent EAs (Jamf Connect password sync via `PasswordCurrent`, Entra WPJ-cert registration, NinjaOne agent liveness) + smart groups: `references/entra-sso-and-keychain.md`.
- **EAs have no scope** — every EA runs on every Mac at each recon; only the consumers (smart groups → policies/profiles) scope. Renamed EA = smart-group criterion silently matching nothing; re-pick the field from the browse list.

## Identity triage: keychain orphaning + Jamf Connect vs Entra registration (2026-07-22)

Full mechanism, EA scripts, fix/replication runbooks, Platform SSO rollout: `references/entra-sso-and-keychain.md`. Load it for any of these ticket shapes:
- **Per-tab/constant M365 re-sign-in in Edge or Chrome after a password reset** = orphaned login keychain (Safe Storage key locked), NOT an SSO/Entra problem. Splitter: Safari fine + Chromium broken. Any reset without the old password (Entra admin, Jamf policy, sysadminctl, FileVault key) orphans the keychain — cryptographic, no admin override; Jamf Connect force-syncs the account hash anyway so login always works. Fix = re-key or reset the login keychain + delete `Microsoft Edge Safe Storage`, never "reinstall Edge".
- **Entra shows zero macOS devices** on a Jamf Connect fleet = expected, not broken. Connect syncs user passwords only; the first-login M365 page is an ordinary OIDC sign-in that never registers the device. Device registration/PRT = Company Portal + SSO-extension profile (works without Intune). Per-tab prompts ≠ no-PRT (that causes periodic re-auth only).
- `In Sync` from the Jamf Connect EA coexists with a broken keychain — the EA measures account↔Entra, not keychain integrity.
- **EAs have NO scope** — every EA script runs on every Mac at every recon; there is no scoping UI (by design: EAs are inventory collection). Scope lives only on the smart groups/policies that consume the EA value. EA criteria appear under **Show Advanced Criteria** in the smart-group builder. A sub-second local plist/file check is negligible recon weight; avoid network-calling EAs.
- Hardened EA scripts (Jamf Connect password sync via `PasswordCurrent`, Entra WPJ-cert registration, NinjaOne agent liveness) + Tier-1 candidate list: `references/entra-registration-and-keychain.md`.

## Jamf Connect ≠ Entra device registration (2026-07-22)

Jamf Connect syncs **user passwords** to Entra; it does NOT register devices. A Jamf Connect fleet can show ZERO macOS devices in Entra for years — the M365 page at first login is an ordinary OIDC app sign-in (Entra sign-in logs, app=Jamf Connect, device blank), not Workplace Join. Device registration (WPJ cert `MS-ORGANIZATION-ACCESS` + PRT) comes only from Company Portal / Platform SSO. No PRT = no silent browser SSO (periodic web re-auth) while native apps (OneDrive/Outlook) stay signed in via their own keychain MSAL tokens — that asymmetry is the fingerprint of an unregistered fleet. Registration needs NO Intune (Company Portal is just the SSO broker; compliance-gated CA is the only Intune dependency). Defender for Endpoint also deploys fine unregistered (pure Jamf: pkg + profiles + onboarding plist; needs UAMDM) — registration only gates Entra-side integration (risk→CA). Full layer table, PSSO deployment sketch, CA What-If check: `references/entra-registration-and-keychain.md`.

## Login-keychain orphaning after password resets (2026-07-22)

Any password reset WITHOUT the old password in the transaction (Entra admin/portal, helpdesk, `sysadminctl`, Recovery) orphans the login keychain — it's encrypted with a password-derived key, migration is cryptographically impossible without old+new together. Jamf Connect syncing the local account afterward does NOT heal it: "In Sync"/`PasswordCurrent=true` coexists with an orphaned keychain (three-password chain: Entra ↔ local ↔ keychain; Connect owns only the first link).

- **Signature:** per-tab/per-nav M365 re-sign-in in Edge/Chrome (Safe Storage key locked in keychain → cookies undecryptable), saved browser passwords gone, Safari/Firefox fine. Per-tab ≠ the unregistered-fleet symptom (that's periodic re-auth) — per-tab is always local storage damage.
- **Field diagnostic:** recent reset + Edge/Chrome broken + Safari fine = orphaned keychain. Definitive: `security lock-keychain` then `unlock-keychain` on `~/Library/Keychains/login.keychain-db` — current password fails, old succeeds.
- **Repair:** Keychain Access → login → Edit → Change Password (needs old pw) or Reset Default Keychains; then delete `Microsoft Edge Safe Storage` entry, relaunch Edge, sign in once. Reinstalling Edge/clearing cache does NOT fix it.
- **Why not rampant:** most resets aren't true forgets (old pw known → dialogs migrate); the next-login macOS dialog heals or replaces most of the rest; only repeated "Continue Log In" clicks by a Chromium-centric user produces the month-long ticket — usually misfiled as browser flakiness.
- **PRT caveat:** the PRT caches in the same login keychain, so Entra registration does NOT protect against this; only Secure Enclave PSSO moves keys out of the password-derived keychain.
- **Runbook prevention:** at reset time, have the user click *Update Keychain Password* (old pw known) or walk Reset Default Keychains + one re-sign-in.
- Replication recipe for a test Mac + full mechanism: `references/entra-registration-and-keychain.md`.

## Jamf Connect ↔ Entra identity chains (2026-07-22)

**Jamf Connect syncs user passwords to Entra; it does NOT register devices.** Device registration (WPJ cert `MS-ORGANIZATION-ACCESS` + PRT) is Company Portal / Platform SSO — a separate deployment. Verified on Andrew's fleet: zero macOS devices in Entra despite years of Jamf Connect. Fleet-wide "re-sign into M365 every tab" = WPJ never deployed; a single Mac doing it after a password reset = keychain/PRT desync (fix keychain first, then Connect sync, then re-register). <!-- gitleaks:allow -->

Full detail — identity-chain table, hardened EAs (Jamf Connect Password Sync via `com.jamf.connect.state.plist` `PasswordCurrent`, Entra Device Registration, NinjaOne Agent Health), Platform SSO deployment payload values, and the NinjaOne "Adapter Deleted = last-gasp before offline" event reading: `references/jamf-connect-entra-identity.md`.

## Pitfall: Jamf script parameters crash option parsers

Jamf passes `$1`=mount point, `$2`=computer name, `$3`=username, **plus EMPTY strings for unused parameters 4–11**. A generic script's `while [[ $# -gt 0 ]]; do case "$1"` parser hits an empty `""` arg, falls to the `*)` catch-all → "Invalid option:" with nothing after the colon, exit 1. Policy log signature: `Script result: Error: Invalid option:` (blank after colon) repeated identically every attempt.

- **Fix: Jamf-deployed scripts should parse NO arguments at all.** Strip `-h/--help` handling entirely; ignore `$1..$n`.
- Also `shift 3` silently fails in bash when fewer than 3 args exist — don't rely on it as a guard.
- Never send `softwareupdate`/installer output to `/dev/null` in deployed scripts — capture with `OUTPUT=$(cmd 2>&1)` and `tee -a` to a log AND stdout so failures show in the Jamf policy Details modal.
- Rosetta 2: receipt is `com.apple.pkg.RosettaUpdateAuto` on modern macOS (grep loosely, `grep -qi rosetta`); guard with `sysctl -n machdep.cpu.brand_string | grep -q Apple`; Intel Macs must `exit 0` so the policy logs success.

## Check-in timing facts
- Check-in daemon: `/Library/LaunchDaemons/com.jamfsoftware.task.1` with `StartInterval` (default 900s). Timer is relative to boot and **does not fire during sleep** — laptops check in on wake, so fleet-wide "inconsistent check-ins" is usually normal sleep noise, not breakage.
- Verify on device: `sudo defaults read /Library/LaunchDaemons/com.jamfsoftware.task.1 StartInterval` (should match console Settings → Check-In) and `grep "recurring check-in" /var/log/jamf.log | tail` for real timestamps.

## Login trigger
- Checking "Login" on a policy is NOT enough — enable the global setting first: Settings → Computer Management → Check-In → **Login Events**. Deploys to each Mac at its next check-in (so test Mac needs one `sudo jamf policy` before logout/login testing). Enabling it is invisible to users unless heavy payloads get scoped to login.
- Fires ONLY on a real new login session (reboot→login, logout→login). **NOT** on wake-from-sleep, screen unlock, or fast user switching. Laptop users rarely log out — treat login as an accelerant; recurring check-in remains the workhorse.
- Logout triggers were removed from Jamf (~10.40+); don't design around them.

## New-device onboarding: profile timing + asset tags
- Scoped config profiles queue over APNs seconds after enrollment, but in **no guaranteed order** — a fast Setup Assistant clicker reaches first login before they land. Attach ONLY login-critical profiles (Jamf Connect Login + license key) to the PreStage (PreStage → Configuration Profiles); leave the rest as normal scoping (scope is the system of record; PreStage is a day-zero timing fix). "Await Configuration" in the PreStage shrinks the race further. Smart-group-scoped profiles arrive even later (need first recon).
- Jamf Connect with missing/expired license = **graceful degradation**: falls back to plain macOS login window, menu bar app shows license warning; nothing uninstalled, no lockout — the risk is silent local↔IdP password desync. When rotating license profiles: deploy new → verify installed → then remove old (a gap = unlicensed window). Watch for "No scope defined" on the profile that carries the live key.
- **Asset tags without manual entry**: Settings → Global → Inventory Preload. CSV headers must exactly match Jamf's template (`Serial Number` + `Device Type` required; unrecognized columns reject the whole upload; EA columns as `EA <name>`). Matches at **enrollment time only** — existing fleet needs MUT or API (`PATCH /api/v1/computers-inventory-detail/{id}`); `POST /v2/inventory-preload/records` (JSON) is the route for a ServiceNow/SharePoint sync. Preload rows persist across re-enrollments.
- **Preload UI is VIEW-ONLY** (verified 2026-07-22): no add-record or edit-row buttons — only History / Template / File Upload / Delete Data. All writes go through CSV upload or API. Re-uploading a full CSV is safe + idempotent (matching serials update in place) — keep a master CSV and treat re-upload as \"save\". **Delete Data nukes the ENTIRE table**, not selected rows (single-record delete is API-only). Computer Name is not a preload field — naming is a rename policy/script concern.
- **Preload re-stamps offboarded users**: preload is a dumb serial→data lookup applied at EVERY enrollment — wipe a leaver's Mac for a new hire and the old username comes back. Preferred pattern: keep preload **tags-only** (no user columns → nothing to clean at offboarding); make live usage the source of user truth via post-enrollment `sudo jamf recon -endUsername <user>`. If user fields are preloaded anyway, blanking them must be an offboarding checklist item.
- Before building any tag backfill from a Jamf export, **check the Asset Tag column actually has data** — if tags were never recorded in Jamf, the backfill source is purchasing records, or tags must be minted fresh. Parse the export first; don't build an empty pipeline.
- SharePoint list → Power Automate → preload API sync (flow spec, Jamf API client setup, token flow, smoke test): `references/jamf-api-and-preload-sync.md`.
- **"User Approved MDM: No"** on a record = user-initiated enrollment without profile approval — blocks system extensions/PPPC payloads (breaks EDR/RMM/PrinterLogic deployment). ADE/PreStage enrollment = approved + supervised; fix by re-enrolling via ADE.

## Mac printing triage (PrinterLogic fleets)
- Print preview spinner in Edge/Chromium ("Total: 0 sheets") = the browser's preview renderer, not CUPS. Splitter: does "Print using system dialog" (⌃⌘P) work? Yes → Edge preview issue (toggle off `edge://settings/system` → graphics acceleration; fleet policy `HardwareAccelerationModeEnabled=false` in `com.microsoft.Edge` domain — but test single-machine first).
- System dialog prints but output is **garbled/messy lines** = wrong-driver signature (PCL/PS mismatch or broken vendor driver). Prove it: add a test queue via System Settings → Printers → IP → **AirPrint/IPP Everywhere** (driverless) to the same printer IP. Clean test print → fix in PrinterLogic admin (update driver or switch macOS deployment to driverless). Garbled too → printer-side (firmware/emulation).
- On Apple Silicon, Intel-only print drivers without Rosetta 2 hang rather than error — check `arch -x86_64 /usr/bin/true` before deeper triage. Macs enrolled while a Rosetta policy was broken are a latent fleet-wide landmine; flush with the fixed policy + arm64 smart group.

## Pitfall: pkg format assumptions

- Verify before wrapping: `xar -tf pkg | grep -i distribution`. Presence of `Distribution` = already a distribution/product archive; `productbuild --package` on it errors with *"is a product archive, not a component package"*.
- **NinjaOne's macOS agent download is ALREADY a distribution pkg**, signed (Developer ID Installer: NinjaRMM LLC) and notarized — upload to Jamf as-is, no wrapping. Details: `references/ninjaone-agent.md`.
- If a genuine component pkg needs wrapping: `productbuild --package in.pkg out-dist.pkg` — macOS only (no Windows equivalent worth using). If Andrew is on the Windows laptop, offer to run it on a Mac over SSH (WRITE GATE applies).
- Check signature/notarization: `pkgutil --check-signature pkg` (macOS only). On Linux (no xar/bsdtar), inspect structure + extract pkg identifiers with `scripts/xar_inspect.py <pkg> list|identifiers`.

## ABM / ADE enrollment ("Remote Management" prompt won't go away)

- The Setup Assistant prompt is driven by Apple's **activation record**, not the ABM web UI. Assignment is checked **at activation time** — erasing before an ABM removal propagates re-caches the old profile; the fix is a second erase/restore after propagation.
- Unassigning from an MDM server ≠ releasing the device — separate ABM operations.
- MDM-side: delete inventory record AND remove from PreStage scope (default prestages silently re-claim returning serials).
- Can't run Erase All Content from Setup Assistant — use Recovery Mode restore (Finder/Apple Devices), choose **Restore** not Update.
- Non-skippable prompt = still ADE-assigned somewhere; skippable = stale cache.

## Working with Andrew on MDM issues

- He's often relaying from a Windows laptop; give him exact commands to run on the target Mac and read pasted output.
- Large files (>20 MB) can't come via Telegram — have him paste the tokenized download URL (NinjaOne installer URLs embed the org token; treat as credential) or use litterbox.
- Fetching a vendor pkg server-side + inspecting it on a Linux box over SSH beats speculating about its format.
