# Hermes Host Migration Runbook (VPS-B → new Linux host)

> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

Verified footprint and sequence from the 2026-06-12 migration to the T2 LH01
(LH01, <LH01-tailnet-ip>, Ubuntu 24.04). Generalizes to any same-OS move.
(real addresses: memory_search "Tailnet")

## What constitutes a Hermes install (ALL must move)

| Piece | Path (source = VPS-B) | Notes |
|---|---|---|
| App code + venv | `/usr/local/lib/hermes-agent/` | venv pinned Python 3.11 — do NOT rsync the venv to a host with a different Python; reinstall via `hermes setup` and let it build fresh |
| Data home | `~/.hermes/` (~3.6GB; ~2GB after profile prune) | skills, memories, config.yaml, .env secrets, knowledge_db, profiles/, patches/, scripts/ |
| Systemd units | `~/.config/systemd/user/hermes-gateway.service`, `hermes-gateway-ha-bot.service` | USER-scoped, not /etc. Per-profile units set their own `HERMES_HOME` (ha-bot → `~/.hermes/profiles/ha-bot`) |
| OAuth bypass | `~/hermes-claude-auth/` | `install.sh` MUST be re-run on the target after rsync — it hooks into the venv's `sitecustomize.py`, which is target-local |
| Claude creds | `~/.claude/` | OAuth credentials for the bypass |

## Hard constraint: user identity

VPS-B runs Hermes as **root**. 60+ files in `~/.hermes/patches/`
and `~/.hermes/scripts/` hardcode `~/` paths. A target running as a normal
user breaks ALL of them. **Decision: run as root on the target too** — straight
rsync, zero path surgery. Enable root SSH by copying authorized_keys to
`~/.ssh/` via the regular user's sudo.

## Sequence (phases, each verifiable)

1. **Prune** dead-weight profiles on source (`pre-update-*`, `stable-*`, `swarm-*`) before transfer. Keepers: default + `executor` + `ha-bot`.
2. **Install** Hermes fresh on target (`hermes setup`) — gets correct venv for target's Python.
3. **Sync** (no downtime): rsync `~/.hermes/`, `~/hermes-claude-auth/`, `~/.claude/`, the two systemd user units. Re-run bypass `install.sh` on target.
4. **Cutover** (~2–5 min): start gateway on target → verify Telegram connects → stop gateway on source. Messages during the window queue and deliver after.
5. **Decommission**: disable source units, update memory/topology records, knowledge-store the new layout.

**Rollback at any point before phase 5:** source gateways are intact — `systemctl --user start hermes-gateway` on source reverts.

## Pitfalls

- **The cutover severs your own session** (same as PITFALL 4 in `hermes-core-update-with-bypass`): stopping the source gateway kills the controlling chat. Run the cutover steps as a detached unit reporting out-of-band, or expect to resume from a fresh session — keep state in a durable runbook/todo, not context.
- **Two gateways, not one:** default + ha-bot are separate services with separate HERMES_HOME. Verify BOTH post-cutover.
- **`EnvironmentFile=-~/.hermes/.env`** may be missing from units (PITFALL 6 in the update skill) — check on the target while you're touching the units anyway.
- **Agent SSH keypair:** verify the PRIVATE key exists (`ls ~/.ssh/id_ed25519`) before relying on key auth — a pubkey line in memory/authorized_keys is worthless without it. Diagnose with `ssh -vvv ... 2>&1 | grep -E "Trying|no such identity"`. Regenerate (`ssh-keygen -t ed25519 -N ""`) and redistribute if gone.
- **Python version drift** (source 3.11, target 3.12): another reason to never rsync the venv — `hermes setup` on target owns that.
