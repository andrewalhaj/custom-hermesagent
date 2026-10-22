# Jamf Connect, the Login Keychain, and Entra/Platform SSO (diagnosed 2026-07-23)

Root-caused from a live ticket: user's Mac re-prompted M365 sign-in on EVERY new Edge tab,
starting after a password reset a month earlier. Full mechanism, runbook, and fleet context.

## The three-password model (the key mental model)

```
Entra password  ←─sync─→  local account password  ←─???─→  login keychain password
      (Jamf Connect owns this link)              (NOTHING owns this link without the old pw)
```

- The **login keychain is ENCRYPTED with a key derived from the account password** — not linked by
  reference. Migration = decrypt with OLD password + re-encrypt with NEW, atomically. No old
  password in the transaction → migration is cryptographically impossible. No admin override
  exists (by design: the keychain protects users FROM password-reset authority).
- The **local account password is just a hash** — root authority (Jamf Connect's helper,
  `sysadminctl`, Jamf policy, Recovery) can force-set it anytime WITHOUT the old password.
- This asymmetry is why "user signs in fine" and "keychain broken" coexist: sign-in is an
  authorization check admins can override; the keychain is cryptography nobody can.

## How the orphaning happens (every admin-path reset)

1. Password reset in Entra/M365 portal, helpdesk console, or Jamf → only the cloud/account side
   changes; the Mac keychain was not in the transaction.
2. Jamf Connect detects drift (60-min check or at login) and asks the user for the OLD local
   password. If they know it → proper change → keychain migrates → healed. If not (true forgot) →
   Connect **force-resets the account hash** → user signs in fine → **keychain orphaned**.
3. macOS shows the catch-net dialog at each login: *"The system was unable to unlock your login
   keychain"* → **Update Keychain Password** (needs old pw; full heal) / **Create New Keychain**
   (fresh empty wallet; heals, secrets lost) / **Continue Log In** (fixes nothing — the
   month-long-ticket branch). Users overwhelmingly click Continue.

**It orphans EVERY time the old password is absent.** It only becomes a lingering ticket when
forgot-password reset × Continue-clicked repeatedly × Chromium-heavy user line up.

## Symptom signature & 10-second test

- Edge/Chrome: constant M365 re-auth, worst case per-tab (their cookie stores are encrypted with a
  key IN the keychain — `Microsoft Edge Safe Storage` / `Chrome Safe Storage`). Saved browser
  passwords gone. **Safari and Firefox mostly fine** (keychain-independent stores) — the fastest
  field diagnostic: Safari OK + Edge broken + recent reset ≈ orphaned keychain.
- Native apps (OneDrive/Outlook/Teams) limp on cached MSAL tokens, throw "wants to use the login
  keychain" prompts that users dismiss for weeks.
- Definitive test (Terminal, as the user):
  `security unlock-keychain ~/Library/Keychains/login.keychain-db`
  with the CURRENT password → **fails = orphaned**.

## Repair runbook

1. Confirm with the unlock test above.
2. Re-key the wallet:
   - **Old password known:** Keychain Access → select *login* → **Edit → Change Password for
     Keychain "login"** → old → new. Everything preserved.
   - **Unknown:** Keychain Access → Settings → **Reset Default Keychains…** → current password →
     log out/in. Contents were cryptographically lost at the reset, not at this step — set that
     expectation with the user.
3. Edge re-bind (Path-A/re-key only): quit Edge → delete keychain entry `Microsoft Edge Safe
   Storage` → relaunch. (After Reset Default Keychains it's already gone — skip.) Same for
   `Chrome Safe Storage`.
4. Sign into M365 once, "Stay signed in", verify: new tabs hold, relaunch holds, unlock test now
   passes, no keychain pop-ups.

**Prevention line for the reset procedure:** after any admin/portal reset for a Mac user, tell
them at next login to click **Update Keychain Password** with the old password; if unknown →
**Create New Keychain**. Never Continue.

## Replicating on a test Mac (validated recipe)

Throwaway account → baseline Edge/M365 healthy → log out → from another admin:
`sudo sysadminctl -resetPasswordFor testuser -newPassword 'NewPass2026!'` → log in with new
password → at the keychain dialog click **Continue Log In** → Edge now shows the ticket.
Then rehearse the repair (runbook above). Undo = the repair or delete the account.

## Jamf-side reset paths (all orphan the keychain; Secure Token wrinkle)

- Policy → Local Accounts payload, scripts (`sysadminctl`), FileVault recovery-key flow — all
  old-password-less → all orphan.
- On APFS, resets on Secure-Token users via the policy payload can additionally desync FileVault
  pre-boot unlock (token ops need another token-admin's creds, which a fire-and-forget policy
  lacks). Preferred admin path in a Jamf Connect shop: **reset in Entra, let Connect reconcile at
  the login window** — keeps FileVault/token clean automatically. Keychain orphans regardless;
  runbook still applies.

## What Entra registration / PRT does and does NOT fix

- **PRT (Primary Refresh Token)** = device-level master token, issued ONLY to Entra-registered
  devices through the broker (Company Portal SSO extension). Browser "stay signed in" = per-browser
  cookie; native app sign-in = per-app MSAL refresh token; neither is a PRT and no checkbox can
  produce one.
- Jamf Connect login (the M365 web view at the login window) authenticates the USER and creates
  the local account. It does **NOT** register the device — a whole Jamf Connect fleet can show
  ZERO macOS devices in Entra. Consequences: no silent SSO (session-expiry re-auth fleet-wide),
  blank device info in sign-in logs, no per-device revocation, no device-based CA possible.
  Users signing into M365 daily from unregistered Macs = proof no CA policy requires
  registered/compliant macOS (confirm via Entra → Conditional Access → **What If**, platform macOS,
  app Office 365 → check Grant controls).
- **Platform SSO deployment (registration-only) needs NO Intune**: Company Portal app via Jamf App
  Installers (users never open it) + Extensible SSO config profile (Redirect,
  `com.microsoft.CompanyPortalMac.ssoextension`, Team `UBF8T346G9`, URLs login.microsoftonline.com
  / login.microsoft.com / sts.windows.net, Platform SSO auth method **Password** alongside Jamf
  Connect). User sees one "Registration Required" notification → Mac appears in Entra. Verify:
  `app-sso platform -s`. Intune only enters for the compliance connector / CA "compliant device"
  phase.
- **The PRT does NOT fix the keychain problem** — it is CACHED IN the login keychain, so an
  orphaned wallet locks the PRT away too (silent SSO dies with the cookies). Wallet analogy:
  keychain = wallet; cookies-key, app tokens, PRT = cards in it. Orphan the wallet, all cards
  locked. Only **Platform SSO Secure Enclave mode** (keys in hardware, not password-derived) is
  structurally immune to reset orphaning.
- Defender for Endpoint deploys/protects fine WITHOUT registration (pkg + system-extension/PPPC
  profiles + onboarding plist; needs User Approved MDM). Registration only gates the
  Defender↔Entra integration layer (risk-score CA, device correlation). Pattern: anything living
  ON the Mac works unregistered; anything where Entra must decide ABOUT the Mac needs
  registration.

## EA + smart-group pack built for this (state-enumerated, positive-match)

All follow the EA discipline: enumerated `<result>` states, never blank; smart groups use
`is "<exact state>"` only; console path Settings → Computer Management → Extension Attributes →
+ New, Data Type String, Input Type Script. NOTE: **EAs have no scope** — they run fleet-wide at
every recon; scope lives only on smart groups / policies consuming the value.

### 1. Jamf Connect Password Sync (`PasswordCurrent` in com.jamf.connect.state.plist)
```bash
#!/bin/bash
currentUser=$(/usr/sbin/scutil <<< "show State:/Users/ConsoleUser" | awk '/Name :/ && ! /loginwindow/ { print $3 }')
if [[ -z "$currentUser" || "$currentUser" == "root" ]]; then
    echo "<result>No user logged in</result>"; exit 0
fi
statePlist="/Users/$currentUser/Library/Preferences/com.jamf.connect.state.plist"
if [[ ! -f "$statePlist" ]]; then
    echo "<result>Jamf Connect state not found</result>"; exit 0
fi
pwCurrent=$(/usr/libexec/PlistBuddy -c "Print :PasswordCurrent" "$statePlist" 2>/dev/null)
case "$pwCurrent" in
    true)  echo "<result>In Sync</result>" ;;
    false) echo "<result>Out of Sync</result>" ;;
    *)     echo "<result>Jamf Connect state not found</result>" ;;
esac
```
Smart group: `<EA> is "Out of Sync"` and `Last Check-in less than 30 days ago`. Companion key
`LastSignIn` (same plist) = last successful IdP auth — catches stale-sync Macs. Caveat: reflects
Connect's LAST check, not real-time; and In Sync says nothing about the keychain (third chain).

### 2. Entra Device Registration (WPJ cert presence)
```bash
#!/bin/bash
currentUser=$(/usr/sbin/scutil <<< "show State:/Users/ConsoleUser" | awk '/Name :/ && ! /loginwindow/ { print $3 }')
if [[ -z "$currentUser" || "$currentUser" == "root" ]]; then
    echo "<result>No user logged in</result>"; exit 0
fi
userKeychain="/Users/$currentUser/Library/Keychains/login.keychain-db"
if /usr/bin/security find-certificate -c "MS-ORGANIZATION-ACCESS" "$userKeychain" &>/dev/null; then
    echo "<result>Registered</result>"
elif /usr/bin/security find-certificate -c "MS-ORGANIZATION-ACCESS" /Library/Keychains/System.keychain &>/dev/null; then
    echo "<result>Registered</result>"
else
    echo "<result>Not registered</result>"
fi
```
Proves cert EXISTS, not that the PRT is currently valid. Doubles as the PSSO rollout tracker.
Smart group: `is "Not registered"` + activity filter.

### 3. NinjaOne Agent Health (presence AND liveness — app-title groups can't see dead daemons)
```bash
#!/bin/bash
agentDir="/Applications/NinjaRMMAgent"
daemonPresent=$(ls /Library/LaunchDaemons 2>/dev/null | grep -ci "ninjarmm")
if [[ ! -d "$agentDir" && "$daemonPresent" -eq 0 ]]; then
    echo "<result>Not installed</result>"; exit 0
fi
if /usr/bin/pgrep -if "ninjarmm" &>/dev/null; then
    echo "<result>Running</result>"
else
    echo "<result>Installed, not running</result>"
fi
```
Smart group: `is "Installed, not running"` + activity filter. Keep the existing
njdialog.app-title group for coverage; this catches installed-but-dead.

### Tier-1 EA candidates queued (not yet built)
Jamf Connect LastSignIn · Rosetta 2 installed · FileVault recovery key escrowed · Bootstrap Token
escrowed. Tier 2: Edge version, local admins, battery condition, last reboot, Secure Token users.
Skip anything Jamf collects natively (Settings → Inventory Collection) and network-calling EAs.

## Dialog reference (for helpdesk docs / user comms)

- macOS: **"The system was unable to unlock your login keychain."** — buttons Create New Keychain /
  Update Keychain Password (older builds add Continue Log In).
- Jamf Connect sync prompt: **"Please sync your passwords."** — field "Local Password:" (= the OLD
  password), buttons Cancel / Sync.
- Pixel-perfect fleet-matching screenshots: run the replication recipe on the test Mac and capture.

## NinjaOne "Adapter Deleted" + long-offline context (same fleet)

A final "Network Adapter removed: en0" activity followed by permanent silence = the machine's last
gasp before going dark (shutdown/departure), NOT a config/policy change. Triage splits: Jamf last
check-in (also stopped → machine offline, not a Ninja problem; still alive → Ninja agent broken →
reinstall via Jamf), Entra sign-in logs for the user, UniFi client history for the MAC.
