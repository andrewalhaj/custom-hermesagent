# Known-good Jamf scripts & extension attributes (session 2026-07-22)

Both scripts below follow the house rules: NO argument parsing (Jamf's empty $4–$11 params crash option parsers), never /dev/null the payload output, `tee -a` so output lands in both the local log and the Jamf policy Details modal.

## Rosetta 2 install (policy script)

Replaced a GreatKemo community script whose `-h/--help` option parser died on Jamf's empty params (`Error: Invalid option:` with nothing after the colon, exit 1, three identical attempts).

```bash
#!/bin/bash

# Install Rosetta 2 — Jamf-deployed, no argument parsing.
# Jamf passes $1-$3 (+ empty $4-$11); we ignore all of them deliberately.

LOG="/Library/Logs/install_rosetta.log"

log_info() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')] Info: $*" | tee -a "$LOG"
}

log_error() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')] Error: $*" | tee -a "$LOG" >&2
  exit 1
}

touch "$LOG"

# Intel Mac — Rosetta not applicable. Exit clean so Jamf logs success.
if ! sysctl -n machdep.cpu.brand_string | grep -q "Apple"; then
  log_info "This Mac does not have Apple Silicon. Rosetta 2 is not required."
  exit 0
fi

# Already installed?
if /usr/sbin/pkgutil --pkgs | grep -q "com.apple.pkg.RosettaUpdateAuto"; then
  log_info "Rosetta 2 is already installed."
  exit 0
fi

# Install, up to 5 attempts, capturing softwareupdate's actual output.
for ATTEMPT in 1 2 3 4 5; do
  log_info "Installing Rosetta 2 (attempt $ATTEMPT of 5)..."
  OUTPUT=$(/usr/sbin/softwareupdate --install-rosetta --agree-to-license 2>&1)
  log_info "softwareupdate output: $OUTPUT"
  if /usr/sbin/pkgutil --pkgs | grep -q "com.apple.pkg.RosettaUpdateAuto"; then
    log_info "Rosetta 2 installed successfully."
    exit 0
  fi
  sleep 5
done

log_error "Failed to install Rosetta 2 after 5 attempts."
```

Notes:
- Receipt on modern macOS is `com.apple.pkg.RosettaUpdateAuto` (loosen to `grep -qi rosetta` if in doubt).
- No macOS-version check needed — Apple Silicon can't run pre-Big Sur.
- Manual one-liner for testing on a Mac in hand: `sudo softwareupdate --install-rosetta --agree-to-license`; verify with `arch -x86_64 /usr/bin/true && echo "Rosetta OK"`.
- After fixing a failed policy: **flush policy logs** if frequency is "Once per computer" or it won't re-run.

## OneDrive business sign-in status (extension attribute, v2)

v1 only checked the standalone-install path → 117 false "not signed in" (App Store + File Provider installs missed). v2 checks all three markers; File Provider sync root is the cleanest signal on macOS 12.3+.

```bash
#!/bin/bash

# OneDrive Sign-in Status — Jamf Extension Attribute
# Detects work/school (business) OneDrive sign-in across all install types.

USER=$(/usr/bin/stat -f%Su /dev/console)
[[ "$USER" == "root" || -z "$USER" ]] && USER=$(/usr/bin/defaults read /Library/Preferences/com.apple.loginwindow lastUserName 2>/dev/null)

if [[ -z "$USER" || ! -d "/Users/$USER" ]]; then
  echo "<result>No user found</result>"
  exit 0
fi

HOME_DIR="/Users/$USER"

signed_in="no"
# 1. File Provider sync root (macOS 12.3+, any OneDrive flavor)
if /bin/ls -d "$HOME_DIR/Library/CloudStorage/"OneDrive-* >/dev/null 2>&1; then
  signed_in="yes"
# 2. Standalone install settings
elif [[ -d "$HOME_DIR/Library/Application Support/OneDrive/settings/Business1" ]]; then
  signed_in="yes"
# 3. App Store (sandboxed) install settings
elif [[ -d "$HOME_DIR/Library/Containers/com.microsoft.OneDrive/Data/Library/Application Support/OneDrive/settings/Business1" ]]; then
  signed_in="yes"
fi

if [[ "$signed_in" == "yes" ]]; then
  echo "<result>Signed in</result>"
elif [[ -e "/Applications/OneDrive.app" || -e "$HOME_DIR/Applications/OneDrive.app" ]]; then
  echo "<result>Installed, not signed in</result>"
else
  echo "<result>OneDrive not installed</result>"
fi

exit 0
```

Outputs (smart group values must match VERBATIM, positive `is` match only):
- `Signed in`
- `Installed, not signed in`
- `OneDrive not installed`
- `No user found`

Marker diagnostic (run on a signed-in test Mac to confirm which path exists before trusting fleet counts):
```bash
U=$(stat -f%Su /dev/console)
ls -d "/Users/$U/Library/CloudStorage/"OneDrive-* 2>/dev/null
ls -d "/Users/$U/Library/Application Support/OneDrive/settings/Business1" 2>/dev/null
ls -d "/Users/$U/Library/Containers/com.microsoft.OneDrive/Data/Library/Application Support/OneDrive/settings/Business1" 2>/dev/null
```
`Business1` = work/school account; `Personal` = consumer account (deliberately not counted).

## Related console facts
- EAs re-evaluate ONLY at recon; smart groups recalc on inventory submission. Companion policy for fast drain: Maintenance→Update Inventory payload, Recurring Check-in trigger, Once every day, scoped to the "missing" group.
- Jamf ↔ Ninja truth gap: receipt/app presence proves install; only the NinjaOne console proves registration. Diff Jamf group members against Ninja console device list for the real gap.
