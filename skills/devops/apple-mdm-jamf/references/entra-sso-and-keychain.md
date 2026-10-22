# Entra SSO, keychain orphaning, and identity EAs (session 2026-07-22)

## The keychain-orphaning failure mode (per-tab M365 re-sign-in ticket)

**Symptom:** one user must re-sign into M365 on every new Edge tab; other users fine; started after a password reset. Native apps (OneDrive/Outlook) keep working on cached MSAL tokens — only browser/web breaks.

**Mechanism — three passwords, two links:**
```
Entra password ←sync→ local account password ←???→ login keychain password
       (Jamf Connect owns)              (NOBODY can force this one)
```
- Local account password = a **hash**; root authority (Jamf Connect helper, sysadminctl, Jamf policy) can force-set it anytime WITHOUT the old password. User always gets into the Mac.
- Login keychain = **encrypted** under the old password. Migration requires old+new in one transaction (decrypt→re-encrypt). Cryptographically impossible without the old password — no admin override, by design.
- Edge/Chrome store their cookie-encryption key in the login keychain ("Microsoft Edge Safe Storage" / "Chrome Safe Storage"). Orphaned keychain → key unreachable → every tab is a cold session. Safari/Firefox mostly unaffected (own stores) — **field diagnostic: Safari fine + Edge/Chrome broken + recent reset = orphaned keychain, no Terminal needed.**

**Why it isn't universal after every reset (the funnel):**
1. Resets WITH old password known (self-change, Jamf Connect sync dialog answered) → migrated, no issue.
2. True-forgot resets ALWAYS orphan, but macOS offers a login dialog: **Update Keychain Password** (needs old pw → healed) / **Create New Keychain** (fresh wallet, secrets lost, works) / **Continue Log In** (stays broken). Most users hit Create New and self-resolve invisibly.
3. Month-long tickets = the minority who click Continue repeatedly + are Chromium-centric.

**Jamf Connect specifics:** sync prompt ("Please sync your passwords." / Local Password field / Cancel+Sync) asks for the OLD local password — a courtesy repair attempt, skippable. If skipped/unknown, Connect force-syncs the account hash and the keychain orphans. `PasswordCurrent=true` (In Sync) coexists with an orphaned keychain — the EA measures account↔Entra, not keychain integrity.

**Definitive test on the Mac (as the user):**
```bash
security lock-keychain ~/Library/Keychains/login.keychain-db
security unlock-keychain ~/Library/Keychains/login.keychain-db   # prompts
```
Current password fails to unlock → orphaned confirmed.

**Fix runbook:**
1. Keychain Access → login → Edit → Change Password for Keychain "login" (needs old pw), or Keychain Access → Settings → Reset Default Keychains if unknown.
2. Quit Edge → delete keychain entry `Microsoft Edge Safe Storage` → relaunch Edge.
3. Sign into M365 once ("Stay signed in") → verify new tabs hold.

**Replicate on a test Mac (throwaway account):** baseline Edge/M365 working → from another admin: `sudo sysadminctl -resetPasswordFor testuser -newPassword 'NewPass2026!'` → log in as testuser with new pw → at the keychain dialog click **Continue Log In** → Edge now shows the ticket. Verify with the lock/unlock test.

**Prevention (helpdesk runbook line for EVERY reset path):** "After an admin/cloud reset: user clicks Update Keychain Password with old pw if known; else walk through Reset Default Keychains + one re-sign-in." Jamf-side reset paths (Local Accounts payload, sysadminctl script, FileVault recovery key) ALL orphan the keychain; Local Accounts payload can additionally desync FileVault/Secure Token on token users — prefer Entra reset + Jamf Connect reconcile in a Connect shop.

## Jamf Connect ≠ Entra device registration (critical distinction)

Jamf Connect syncs USER passwords with Entra. It never registers the DEVICE. A Jamf Connect fleet can show **zero macOS devices in Entra** while every first-login shows an M365 sign-in page (that page is Connect's OIDC webview — an ordinary app sign-in, no device object, no PRT).

Four separate Jamf↔Microsoft links:
| Layer | Connects | Notes |
|---|---|---|
| Jamf Connect | local account/password ↔ Entra | login window + sync; `com.jamf.connect.state.plist` |
| Company Portal / WPJ | device ↔ Entra | plants `MS-ORGANIZATION-ACCESS` cert + PRT; what CA sees |
| MS Enterprise SSO extension (profile) | browser/apps ↔ tokens | silent sign-in via PRT |
| Jamf Pro ↔ Entra (server) | consoles | identity provider / compliance connector |

**PRT** = Primary Refresh Token: device-bound master session key, ~14-day rolling, cached in the **login keychain** (so an orphaned keychain kills silent SSO too — registration does NOT fix keychain tickets; only Platform SSO Secure Enclave mode moves keys out of the password-derived keychain).

**No-PRT symptom is periodic re-auth, NOT per-tab.** Per-tab = keychain/Safe Storage. Don't conflate.

**Registration WITHOUT Intune works:** Company Portal is just the broker binary; the SSO-extension config profile drives registration. Intune only needed later for compliance-gated CA (via Jamf Device Compliance connector). Defender for Endpoint also deploys/protects fine without registration (pkg + system-extension/PPPC/onboarding profiles via Jamf; needs User Approved MDM) — registration only gates Defender risk-score→CA integration. Rule: on-Mac functionality never needs registration; Entra making decisions ABOUT the Mac does.

**Platform SSO deployment sketch:** Company Portal via Jamf App Installers (silent; users never open it) + SSO Extension payload: Redirect, ExtensionID `com.microsoft.CompanyPortalMac.ssoextension`, Team `UBF8T346G9`, URLs login.microsoftonline.com / login.microsoft.com / sts.windows.net, Platform SSO auth method **Password** (coexists with Jamf Connect). User sees one "Registration Required" notification → auths once → device appears in Entra. Verify: `app-sso platform -s`, Entra→Devices, silent Edge sign-in. Confirm CA posture first via Entra **What If** tool (user + Office 365 + macOS platform → check for require-compliant/hybrid grants).

## Identity/agent EA scripts (all follow enumerated-positive-states rule)

### Jamf Connect password sync
```bash
#!/bin/bash
# States: In Sync | Out of Sync | Jamf Connect state not found | No user logged in
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
Stock Jamf template (jamf/jamfconnect repo, built_in_extension_attributes/) uses `ls -l /dev/console` and leaks raw values/blank states — the hardened version above is preferred. Companion key `LastSignIn` (same plist) = last successful IdP auth; epoch-zero default for date-type smart groups.

### Entra device registration (WPJ cert presence)
```bash
#!/bin/bash
# States: Registered | Not registered | No user logged in
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
Proves cert existence, not PRT validity — cert-missing is the triage-hard state. Doubles as Platform SSO rollout progress tracker.

### NinjaOne agent health (presence + liveness)
```bash
#!/bin/bash
# States: Running | Installed, not running | Not installed
agentDir="/Applications/NinjaRMMAgent"
daemonPresent=$(ls /Library/LaunchDaemons 2>/dev/null | grep -ci "ninjarmm")
if [[ ! -d "$agentDir" && "$daemonPresent" -eq 0 ]]; then echo "<result>Not installed</result>"; exit 0; fi
if /usr/bin/pgrep -if "ninjarmm" &>/dev/null; then
    echo "<result>Running</result>"
else
    echo "<result>Installed, not running</result>"
fi
```
Complements the `Application Title does not have njdialog.app` smart group: that catches absent, this catches installed-but-dead.

### Smart groups for these
- `Jamf Connect Status is "Out of Sync"` / `Entra Device Registration is "Not registered"` / `NinjaOne Agent Health is "Installed, not running"` — each **and** `Last Check-in less than 30 days ago`. Positive `is` matches only; pick criteria field from Show Advanced Criteria browse list (typing typos silently match nothing); value must match `<result>` verbatim.

## EA facts confirmed this session
- **EAs have no scope** — every EA script runs on every Mac at every recon; only smart groups/policies/profiles scope. A local plist read is negligible; worry only about network-calling or slow EAs.
- Inventory Display setting on an EA is cosmetic (which record section shows it), not scope.
- If smart group criteria reference an EA by its old/renamed name, the group matches nothing forever — verify criterion name after renaming an EA, and check for duplicate EAs after iterating.

## Dialog reference (for helpdesk docs)
- macOS keychain dialog: title "The system was unable to unlock your login keychain."; modern = 2 buttons (Create New Keychain / Update Keychain Password), older adds Continue Log In. Example image: mrmacintosh.com wp-content/uploads/2019/11/10.15.1-Update-Keychain-Password-1.png (annotated blog header).
- Jamf Connect sync dialog: "Please sync your passwords." + Local Password field + Cancel/Sync. Example: it.fdu.edu wp-content/uploads/2023/11/Local-Password-1.png.
- Pixel-perfect versions for docs: run the sysadminctl replication recipe on a test Mac and screenshot.

## NinjaOne activity-log note
"Network Adapter Deleted (en0)" as a device's LAST event followed by permanent silence = the machine going dark (shutdown/retirement), not a config action. Triage by cross-checking Jamf last check-in (splits machine-offline from Ninja-agent-broken), Entra sign-in logs for the user, UniFi client history for the MAC.
