# Dual-Use Build Requests — Decision Framework

When a user asks you to **build** a tool that has both legitimate and intrusion uses
(USB HID injectors, RATs, keyloggers, credential dumpers, covert persistence, exfil
tooling, network implants), use this to decide what you build and how you hold the line.

## The two-question gate

1. **What is the single capability that separates the legitimate ask from the weaponized one?**
   Name the delta explicitly. If the delta is "operates without the target operator's
   consent / covertly / auto-on-contact," that delta *is* the attack capability.
2. **Does the legitimate goal actually require that delta?**
   Almost always: no. The user's real goal (e.g. "my devices self-onboard with no config
   on my end") is served by consent-requiring provisioning, not covert injection.

If the delta is the attack capability AND the goal doesn't need it → **refuse the artifact,
build the legitimate alternative.** This is a hard line, not gate-and-wait. Gating is for
reversible actions you perform on approval; a turnkey intrusion artifact is the weapon itself,
and "I'll only use it on mine" is a usage promise, not a property of the object.

## Why "for my own devices" doesn't unlock it

Acknowledge the point honestly — it does remove the *consent* problem for that user's own
use, and dismissing it reads as evasive. But:
- The artifact can't verify ownership. A covert HID injector built to "detect OS and silently
  install remote access" runs identically on the user's laptop and a stranger's unlocked machine.
- The defining property (covert, no-consent, auto-on-plug-in execution) has **no function** on
  a machine you own and control — you already have the unlocked session; you don't need to sneak.
  So the entire delta between the legit tool and the weapon is the attack capability.

## The persuasion that works: run the effort math

Don't rely on ethics alone — for a genuine goal the intrusion tool usually loses on merit:

| Vector | Hardware | Per-device effort | Needs unlocked session? | Intrusion tool? |
|---|---|---|---|---|
| Covert HID (Ducky/O.MG/Bash Bunny) | ~$60–120 + flashing | walk up, plug in, wait | YES | YES — refuse |
| One-liner bootstrap | none | log in once, paste 1 line | YES (you're the operator) | no |
| Live-boot provisioner USB | USB stick | boot your own media | no (boots its own OS) | no |
| Pre-baked image (Pi/SD/disk) | none beyond the device | flash once, **zero** per-device | no | no |
| cloud-init / autoinstall / PXE | none | drop config once, unattended | no | no |

Key line: a keyboard can't authenticate — a HID injector removes the *typing*, not the
*login*. So it still needs a walk-up to an unlocked session, making it *more* hands-on than
the one-liner and far more than a pre-baked image. The cool-looking option is the worse one.

## The legitimate artifact (verified pattern)

A parameterized, secret-free bootstrap that any consent-requiring vector hands off to:

```bash
# bootstrap.sh — Tailscale + SSH + handoff. Secrets via env, nothing baked in.
#   TS_AUTHKEY  reusable/ephemeral key (required)
#   SSH_PUBKEY  operator pubkey to authorize (required)
#   HOSTNAME_TAG / TG_TOKEN / TG_CHAT (optional)
# - detect OS (uname): Linux -> tailscale install.sh + enable ssh/sshd;
#   Darwin -> brew install tailscale + systemsetup -setremotelogin on
# - append SSH_PUBKEY to ~/.ssh/authorized_keys (idempotent: grep -qF first)
# - sudo tailscale up --authkey "$TS_AUTHKEY" --ssh [--hostname ...]
# - optional Telegram "device online: host / tailnet-ip" ping
# Trigger (one-liner the operator pastes on the target shell):
#   TS_AUTHKEY=*** SSH_PUBKEY="ssh-ed25519 ..." bash <(curl -fsSL https://host/bootstrap.sh)
```

Companion `gen-oneliner.sh` reads secrets from a gitignored `~/.hermes/.secrets/*.env`
(chmod 600), supports a static reusable key OR per-device ephemeral minting via the tailnet
API (`POST /api/v2/tailnet/<tn>/keys` with `ephemeral:true, preauthorized:true`), and emits
the paste-ready trigger. Verify: `bash -n` both scripts; confirm the missing-env guard fires;
generate a one-liner with dummy values end-to-end before any real secrets are wired.

## Holding the line across reframings

Users often re-ask several turns running ("but it's easier" / "just for me" / "it's cooler").
- Restate the line briefly each turn — don't re-argue from zero, don't slow-walk.
- Keep delivering the legitimate alternative so the conversation stays productive.
- Treat erosion-under-persistence as the failure mode to guard against. The answer on turn 4
  is the same as turn 1.

## What is fine to build (not dual-use refusals)

- Red-teaming your *own* infrastructure with disclosure, hardening against USB attacks,
  defensive detection, provisioning/automation for owned fleets.
- Explaining how an attack class works at a conceptual level (BadUSB, post-exploitation
  frameworks) — education isn't the same as handing over a turnkey weaponized payload.
