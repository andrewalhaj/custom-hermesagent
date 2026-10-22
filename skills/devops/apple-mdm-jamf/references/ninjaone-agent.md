# NinjaOne macOS Agent — deployment facts (verified 2026-07-20)

## Installer format — verified server-side (Linux box over SSH)
Downloaded pkg (v13.0.7941, ~77 MB) inspected with `xar -tf`:

```
ninjarmm-macagent.pkg          ← inner component pkg
ninjarmm-macagent.pkg/{Bom,Payload,Scripts,PackageInfo}
Resources/ (banner pngs, welcome.html, conclusion.html)
Distribution                   ← product archive marker
```

- **Already a distribution (product archive) pkg.** `productbuild --package` on it fails: "is a product archive, not a component package". Do NOT wrap.
- Signed: `Developer ID Installer: NinjaRMM LLC (EBNT3ZX97E)`, notarized, trusted timestamp. Verify with `pkgutil --check-signature`.

## Download source
NinjaOne console → Administration → Library → Installers (or Add device → macOS Agent). URL pattern:
`https://app.ninjarmm.com/agent/installer/<org-token-uuid>/<version>/NinjaOne-Agent_<uuid>_<OrgName-Location-Policy>.pkg`

- The pkg is **org/location-tokenized** — the UUID in URL+filename IS the org token. Treat the URL as a credential; don't reuse across orgs; re-download after rotating installers (never re-upload a stale copy to Jamf).

## Post-install detection (corrected 2026-07-22 vs live inventory)
- Installs as LaunchDaemon; app dir `/Applications/NinjaRMMAgent/` (not a normal top-level .app bundle).
- **Jamf Applications inventory DOES list it**: `njdialog.app` at `/Applications/NinjaRMMAgent/programfiles/njdialog.app`, version = agent version (e.g. 13.0.7941). Also `ncstreamer.app` under `/Applications/NinjaRemote/`. (Earlier note claiming it never appears was wrong.)
- Receipt identifier is **version-suffixed**: `org.ninjarmm-macagent.13.0.7941` (extracted from PackageInfo). Exact receipt matches break on every agent update; `pkgutil --pkgs | grep -i ninja` for loose ground-truth on the Mac.
- Daemon: `ls /Library/LaunchDaemons | grep -i ninja`; process: `pgrep -il ninja`.
- End-to-end proof: device appears in NinjaOne console (install + token registration).
- **Jamf smart groups: key on `Application Title` does not have `njdialog.app`** (exact, with dot). NOT receipts — version suffix + "Packages Installed By Casper" misses non-Jamf installs.
- Org token in pkg name encodes org/location/policy (e.g. `<Org>-<Dept>-Auto`) — every Mac installed from one pkg lands in that Ninja location; other locations need their own tokenized pkg.

## Session log (2026-07-20)
Andrew's Jamf policy "not installing" NinjaOne → chased pkg format (component vs distribution) on bad advice that Ninja ships a component pkg → server-side fetch + inspection proved it's already distribution/signed/notarized → actual resolution: install HAD succeeded (package receipt present); it just never shows in Applications inventory because it's a daemon.
