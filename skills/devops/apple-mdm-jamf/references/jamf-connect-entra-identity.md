# Jamf Connect ↔ Entra identity chains + EAs (session 2026-07-22)

## The four separate Jamf↔Microsoft links (do NOT conflate)

| Layer | Connects | Job |
|---|---|---|
| **Jamf Connect** | Local macOS account/password ↔ Entra | Login-window auth + password sync. **Does NOT register the device in Entra.** |
| **Company Portal / WPJ (Workplace Join)** | The *device* ↔ Entra | Plants `MS-ORGANIZATION-ACCESS` cert, mints the PRT, makes the Mac appear in Entra → Devices. What Conditional Access checks. |
| **MS Enterprise SSO extension** (config profile) | Apps/browser ↔ Entra tokens | Uses the PRT for silent Edge/M365 sign-in. |
| **Jamf Pro ↔ Entra** (server-side) | Consoles | Admin IdP login; optional Device Compliance connector. |

**Verified live on Andrew's fleet (2026-07-22): a Jamf Connect fleet can run for years with ZERO macOS devices in Entra** — Connect never registers devices. Symptom of the missing layer: users re-sign into M365 on every new tab/app (no PRT → cookie-only sessions). One Mac doing it after a password reset = keychain/PRT desync; the *whole fleet* doing it = WPJ never deployed. Check Entra → Devices for macOS entries before triaging per-machine. <!-- gitleaks:allow -->

## Password-reset → per-tab M365 sign-in triage (single Mac)

Reset (esp. admin-initiated) desyncs login keychain and/or PRT; SSO extension can't store/mint tokens. Order:
1. Keychain: Keychain Access → login keychain → Edit → Change Password (old→new), or Reset Default Keychains if old pw unknown.
2. Jamf Connect menu bar → run password sync (if it never prompted, reset happened outside its flow).
3. `MS-ORGANIZATION-ACCESS` cert present in keychain? Re-register via Company Portal if not.
4. Only then Edge-side checks (InPrivate default, clear-on-close, `edge://policy`).

## EA facts

- **EAs have NO scope** — script runs on every Mac at every recon, fleet-wide, no scoping UI. Scope lives only on the consumers (smart groups → policies/profiles). A sub-second plist read is negligible; avoid network-calling EAs.
- Smart group criteria for EAs: **Show Advanced Criteria** (EAs hidden in basic list); pick the EA name from the browse list, don't type. Value matches `<result>` VERBATIM; positive `is` only.
- Jamf Connect state lives in `~/Library/Preferences/com.jamf.connect.state.plist` — keys: `PasswordCurrent` (true/false = local pw matches IdP), `LastSignIn` (last successful IdP auth; stale = Connect not talking to Entra). Official templates: github.com/jamf/jamfconnect `built_in_extension_attributes/` (stock scripts use `ls -l /dev/console` and leak blank/root states — harden as below).
- State reflects the agent's **last check**, not real-time — a Mac asleep since a reset shows stale `true`.

## EA: Jamf Connect Password Sync

States: `In Sync` | `Out of Sync` | `Jamf Connect state not found` | `No user logged in`

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

Smart group: `is "Out of Sync"` + `Last Check-in less than 30 days ago` (keeps dead Macs out).

## EA: Entra Device Registration

States: `Registered` | `Not registered` | `No user logged in`. Proves the WPJ cert EXISTS, not that the PRT is valid — cert-missing is the hard-triage state; pw-desync is covered by the Connect EA.

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

## EA: NinjaOne Agent Health

States: `Running` | `Installed, not running` | `Not installed`. Complements the `Application Title does not have njdialog.app` presence group — this catches installed-but-dead agents.

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

## Platform SSO deployment (fixes per-tab sign-in fleet-wide + registers devices)

macOS 13+; deployed entirely from Jamf; coexists with Jamf Connect (Connect owns login window, PSSO owns tokens/registration) and is Jamf's strategic direction.
1. Deploy **Microsoft Company Portal** via Jamf App Installers (user never opens it).
2. Config profile → Single Sign-On Extension payload: Type **Redirect**, Extension ID `com.microsoft.CompanyPortalMac.ssoextension`, Team ID `UBF8T346G9`, URLs `https://login.microsoftonline.com`, `https://login.microsoft.com`, `https://sts.windows.net`. Enable Platform SSO → Auth Method **Password** (least-astonishment alongside Connect; Secure Enclave = passwordless alternative).
3. User sees "Registration Required" notification → one Entra auth (+MFA) → device appears as *Entra registered*.
4. Verify on device: `app-sso platform -s`; in Entra → Devices; new Edge tab signs in silently.
5. Rollout: test Mac → ~10-Mac pilot → fleet. The `Entra - Not Registered` smart group doubles as the rollout tracker. Compliance connector + Conditional Access = separate later project.
Entra prereq: Devices → Device settings → users may register devices (default on).

## NinjaOne "Network Adapter Deleted" event reading

`Monitor: Network Adapter Deleted` for `en0` (built-in Wi-Fi) = agent's interface scan saw configured interface disappear. Paired with an "Adapter Added" minutes later → sleep/roam noise. As the **final event before permanent silence** → last-gasp telemetry of a shutdown, not a policy action. Device offline for weeks after: split "machine dead" vs "agent dead" via Jamf last check-in, Entra sign-in logs, UniFi client history for the MAC, then the assigned user.
