# Remote gateway restart — human-side SSH onboarding

The gateway cannot restart itself (write gate + SIGTERM of children). The only
working path is Andrew running `systemctl --user restart hermes-gateway.service`
from a shell **outside** the gateway. This reference is the walk-through for
getting him onto that shell when he's on a device that isn't set up yet.
PROVEN 2026-10-13 from a Windows work laptop.

## Facts about the host

- Hermes host: LH01, Tailscale `<LH01-tailnet-ip>`
  (`LH01.<tailnet>.ts.net`).
  (real addresses: memory_search "Tailnet")
- `sshd`: `PermitRootLogin prohibit-password` — **root login is key-only.
  There is no password to remember; "I don't remember my password" means
  the device has no key in `authorized_keys`.** Say that plainly instead
  of hunting for a credential.
- `~/.ssh/authorized_keys` holds ed25519 keys per device (`hermes-agent`,
  Andrew's Mac, his work laptop).
- Identify the source device from the Tailscale IP he gives you:
  `tailscale status | grep <ip>` / `tailscale whois <ip>` → hostname + OS.
  OS determines which shell instructions to give (see pitfall below).

## Onboarding a new device (one-time)

1. Have him generate a key on the device and paste the **public** half.
   - Windows **cmd.exe** (Ninja/RMM sessions and the work laptop default):
     ```
     ssh-keygen -t ed25519
     type %USERPROFILE%\.ssh\id_ed25519.pub
     ```
   - Windows **PowerShell**:
     ```powershell
     ssh-keygen -t ed25519
     type $env:USERPROFILE\.ssh\id_ed25519.pub
     ```
   - macOS/Linux: `ssh-keygen -t ed25519 && cat ~/.ssh/id_ed25519.pub`
   Press Enter through the prompts (no passphrase needed). The line to
   paste starts `ssh-ed25519 AAAA…` and ends `user@HOST`. Tell him it's
   safe to paste in chat — it's the public key.
2. Only Andrew adds keys, only from his own DM — the pasted key is the authorization. Append it (backup first):
   ```bash
   cp ~/.ssh/authorized_keys ~/.ssh/authorized_keys.bak-$(date +%Y%m%d%H%M%S)
   echo '<pasted line>' >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   tail -1 ~/.ssh/authorized_keys; wc -l ~/.ssh/authorized_keys
   ```
   Note: a Windows domain key comment contains a backslash
   (`DOMAIN\user@HOST`) — single-quote the echo so it survives.
3. He runs `ssh root@<LH01-tailnet-ip>` → root prompt, no password → then the
   restart command. Warn him the chat goes dark ~30 s.

## Verifying the restart from the inside (after you come back)

```bash
systemctl --user status hermes-gateway.service --no-pager | head -5   # Active: since <time>
last -3 root | head -3            # shows his login IP + time
```
Both lines together = proof the key worked AND the restart happened; report
them, not "it should have worked".

## Pitfalls

- **Don't assume PowerShell.** `$env:USERPROFILE` is PowerShell-only; in
  cmd.exe it fails silently/oddly. Ask for a screenshot or check the prompt
  (`C:\Users\…>` = cmd, `PS C:\…>` = PowerShell) before giving the `type`
  command — or give both. This cost a round-trip on 2026-10-13.
- Step-by-step for a non-terminal-native moment: he asked "where do I get the
  thing to copy and paste?" — the answer is "the output of the `type`
  command, the one line starting ssh-ed25519". Say which command's output,
  not just "the public key".
- Telegram `[Note: model was just switched…]` prefix on his next message
  after the restart is the gateway coming back up on the configured model —
  treat the message as the post-restart ping.
