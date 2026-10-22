# Entra registration, Jamf Connect layers, and the keychain-orphan failure mode

Worked out 2026-07-22 from a real ticket: month-old password reset → user re-signs into M365 on every new Edge tab.

## The four Jamf↔Microsoft layers (don't conflate them)

| Layer | Connects | Notes |
|---|---|---|
| Jamf Connect | local account/password ↔ Entra | Login window is an Entra OIDC web view; creates local account, syncs password (`PasswordCurrent` in `com.jamf.connect.state.plist`). **Does NOT register the device in Entra.** |
| Company Portal / WPJ (Platform SSO) | device ↔ Entra | Plants `MS-ORGANIZATION-ACCESS` cert, gets PRT, creates the Entra device object. What Conditional Access checks. |
| MS Enterprise SSO extension (profile) | apps/browser ↔ tokens | Uses the PRT for silent Edge/M365 sign-in. |
| Jamf Pro server integrations | consoles ↔ each other | Cloud IdP, optional Device Compliance connector (needs Intune; registration does not). |

Key consequences (all verified against Andrew's fleet):
- A Jamf Connect fleet can run for **years with zero macOS devices in Entra**. First-login shows an M365 sign-in page (Jamf Connect Login) — users/admins assume the Mac is "connected"; it's an ordinary OAuth app sign-in (visible in Entra sign-in logs, app=Jamf Connect, device blank). No device object, no PRT.
- No PRT = no silent browser SSO → *periodic* web re-auth fleet-wide (NOT per-tab — per-tab is keychain damage, see below). Native apps (OneDrive/Outlook/Teams) keep their own MSAL refresh tokens in keychain and stay signed in regardless.
- **Defender for Endpoint does NOT need Entra registration** — deploys purely via Jamf (pkg + sysext/PPPC/netfilter profiles + onboarding plist; needs User Approved MDM). Registration only gates the Entra-integration layer (risk score → CA, device correlation). Rule of thumb: on-Mac functionality works unregistered; anything where Entra must *decide about* the Mac needs registration.
- Confirm CA posture: Entra → Conditional Access → **What If** (user + Office 365 + platform macOS) → look for "Require compliant/hybrid-joined device" in grant controls. Logical shortcut: if unregistered Macs reach M365 daily, no device-based CA applies.
- Registration WITHOUT Intune is fine: Company Portal app is just the SSO broker binary (users never open it); the config profile drives registration. Compliance-gated CA is the only Intune dependency (via Jamf Device Compliance connector) — separate later phase.

## Platform SSO deployment sketch (registration rollout)
1. Company Portal via Jamf App Installers (silent, fleet).
2. Config profile → SSO Extension payload: type Redirect, ext id `com.microsoft.CompanyPortalMac.ssoextension`, Team `UBF8T346G9`, URLs `https://login.microsoftonline.com`, `https://login.microsoft.com`, `https://sts.windows.net`; Platform SSO section → auth method **Password** (coexists with Jamf Connect; Secure Enclave = later passwordless phase).
3. User sees one "Registration Required" notification, auths once → device appears Entra-registered. Verify: `app-sso platform -s`, Entra → Devices, Edge new tab silent.
4. Rollout: test Mac → pilot ~10 → fleet. The `Entra - Not Registered` smart group doubles as the progress tracker.

## PRT facts
Primary Refresh Token = per-user-per-registered-device master session key (~14-day rolling). Silent SSO source, device-bound, carries MFA claim. **Cached in the login keychain on macOS** — so keychain orphaning kills the PRT too; registration does not protect against the keychain failure mode. Only Secure Enclave PSSO moves auth keys out of the password-derived keychain.

## Keychain-orphan failure mode (the ticket)

Mechanism: login keychain is **encrypted with a key derived from the account password**. Migration requires old+new password in the same transaction — cryptographic requirement, no admin override (by design). Any reset without the old password (Entra admin/self-service portal, helpdesk, `sysadminctl -resetPasswordFor`, Jamf, Recovery) orphans the keychain. Jamf Connect syncing the *local account* afterward does NOT retroactively heal the keychain — `PasswordCurrent=true` / "In Sync" coexists with an orphaned keychain (they measure different links). Three-password chain: Entra ↔ local account (Jamf Connect owns) ↔ keychain (only old-password-in-hand migration owns).

Symptom signature: Edge/Chrome can't unlock their Safe Storage key (`Microsoft Edge Safe Storage` / `Chrome Safe Storage` in login keychain) → cookies undecryptable → **per-tab/per-nav M365 sign-in**, saved browser passwords gone. Safari and Firefox largely unaffected (don't key session storage off login keychain).

**Field diagnostic (no Terminal):** recent password reset + Edge/Chrome broken + Safari fine = orphaned keychain, near-certain.

**Definitive test** (as the user):
```bash
security lock-keychain ~/Library/Keychains/login.keychain-db
security unlock-keychain ~/Library/Keychains/login.keychain-db
```
Unlock fails with current password (succeeds with old) → confirmed.

**Repair:** Keychain Access → *login* → Edit → Change Password for Keychain "login" (needs old pw), else Keychain Access → Settings → **Reset Default Keychains**. Then quit Edge → delete `Microsoft Edge Safe Storage` entry → relaunch → sign in once ("Stay signed in") → new tabs must hold. Reinstalling Edge / clearing cache does NOT fix it.

**Why it's not rampant (funnel):** most resets aren't true forgets (expired/lockout — user knows old pw, sync dialogs migrate); of true forgets, the next-login macOS dialog catches most (Update Keychain = heal; Create New Keychain = fresh wallet, silently self-resolves); only repeated **"Continue Log In"** clicks + Chromium-centric user produces the month-long ticket. Orphaning happens EVERY old-password-less reset; the unresolved tail is rare and usually misfiled as browser flakiness.

**Replication on a test Mac:** throwaway account → baseline Edge/M365 healthy → from another admin `sudo sysadminctl -resetPasswordFor testuser -newPassword '...'` → log in with new pw → click **Continue Log In** at the keychain dialog → Edge now shows the ticket. Verify with the lock/unlock test; rehearse the repair.

**Prevention (runbook line for helpdesk):** after any admin-path reset on a Mac — tell the user to click *Update Keychain Password* if they know the old password; otherwise walk Reset Default Keychains + one Edge re-sign-in. 2 minutes at reset time vs. a month-long mystery ticket.

## EA scripts from this session

All follow the enumerated-positive-states discipline (never blank; smart groups use `is "<state>"` only). EAs have **no scope** — every EA runs on every Mac at every recon; scope lives on the smart groups/policies that consume them. EA criteria live under **Show Advanced Criteria** in smart group builder. Display value must match script `<result>` verbatim.

### Jamf Connect password sync
States: `In Sync | Out of Sync | Jamf Connect state not found | No user logged in`
```bash
#!/bin/bash
currentUser=$(/usr/sbin/scutil <<< "show State:/Users/ConsoleUser" | awk '/Name :/ && ! /loginwindow/ { print $3 }')
if [[ -z "$currentUser" || "$currentUser" == "root" ]]; then echo "<result>No user logged in</result>"; exit 0; fi
statePlist="/Users/$currentUser/Library/Preferences/com.jamf.connect.state.plist"
if [[ ! -f "$statePlist" ]]; then echo "<result>Jamf Connect state not found</result>"; exit 0; fi
pwCurrent=$(/usr/libexec/PlistBuddy -c "Print :PasswordCurrent" "$statePlist" 2>/dev/null)
case "$pwCurrent" in
    true)  echo "<result>In Sync</result>" ;;
    false) echo "<result>Out of Sync</result>" ;;
    *)     echo "<result>Jamf Connect state not found</result>" ;;
esac
```
Source key: `PasswordCurrent` in `com.jamf.connect.state.plist` (official templates: github.com/jamf/jamfconnect `built_in_extension_attributes/`; stock scripts use `ls -l /dev/console` and leak raw `true/false`/root — harden as above). Companion key `LastSignIn` (last IdP auth) = staleness EA. Caveat: reflects the agent's last check, not real-time.

### Entra device registration
States: `Registered | Not registered | No user logged in`
```bash
#!/bin/bash
currentUser=$(/usr/sbin/scutil <<< "show State:/Users/ConsoleUser" | awk '/Name :/ && ! /loginwindow/ { print $3 }')
if [[ -z "$currentUser" || "$currentUser" == "root" ]]; then echo "<result>No user logged in</result>"; exit 0; fi
userKeychain="/Users/$currentUser/Library/Keychains/login.keychain-db"
if /usr/bin/security find-certificate -c "MS-ORGANIZATION-ACCESS" "$userKeychain" &>/dev/null; then
    echo "<result>Registered</result>"
elif /usr/bin/security find-certificate -c "MS-ORGANIZATION-ACCESS" /Library/Keychains/System.keychain &>/dev/null; then
    echo "<result>Registered</result>"
else
    echo "<result>Not registered</result>"
fi
```
Proves WPJ cert exists, not PRT validity (mid-desync Mac still reads Registered — acceptable; cert-missing is the triage state).

### NinjaOne agent health
States: `Running | Installed, not running | Not installed` — splits "agent dead" from "not installed", which the `njdialog.app` app-title smart group can't.
```bash
#!/bin/bash
agentDir="/Applications/NinjaRMMAgent"
daemonPresent=$(ls /Library/LaunchDaemons 2>/dev/null | grep -ci "ninjarmm")
if [[ ! -d "$agentDir" && "$daemonPresent" -eq 0 ]]; then echo "<result>Not installed</result>"; exit 0; fi
if /usr/bin/pgrep -if "ninjarmm" &>/dev/null; then
    echo "<result>Running</result>"
else
    echo "<result>Installed, not running</result>"
fi
```

### Tier-1 EA candidates (fleet-vetted list, not yet deployed)
Jamf Connect LastSignIn (staleness), FileVault recovery key escrowed, Bootstrap Token escrowed (`profiles status -type bootstraptoken` — gates sysext/update management on AS), Rosetta 2, Edge version (patch-lag groups via "less than"). Skip: anything native inventory collects (check Settings → Inventory Collection first), network-calling EAs (slow recon fleet-wide), asset-tag EA (Inventory Preload is source of truth).

## NinjaOne "Adapter Deleted" event reading
"Monitor: Network Adapter Deleted" for `en0` on a MacBook = interface disappeared from the agent's periodic scan — normally sleep/Wi-Fi-off/roam noise (paired "Adapter Added" minutes later). If it's the **last event ever** and the device never checks in again: it's the machine's last gasp at shutdown, not a config change. Triage: Jamf last check-in (splits machine-offline from Ninja-agent-dead) → Entra sign-in logs for the user → UniFi client history for the MAC → contact user.
