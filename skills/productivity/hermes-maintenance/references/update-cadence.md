# Safe Update Strategy: Hermes Core + Infrastructure

> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

## Update surface

| Component | Mechanism | Risk |
|---|---|---|
| Hermes Core | `hermes update` (git pull + pip reinstall) | Medium |
| Bundled Node | Updated with core | Low |
| Config | `hermes config migrate` | Medium |
| Skills | Curator (7d) + manual patches | Low |
| Manifest Router | Separate service | Low |
| Honcho | Cloud (managed) | None |
| Cron jobs | Manual | Low (scripts, not LLM) |

## The safe pattern

```
Present analysis → Get greenlight → Pre-snapshot → Update → Post-snapshot → Test → Verify → Lock
```

### 0. Present analysis first (MANDATORY gate)

Before touching anything, present to the user:

1. What's pending (`hermes update --check`)
2. What could break (breaking config changes, API deprecations, tool removals)
3. Risk assessment (low/medium/high with justification)
4. Recommendation (proceed / wait / skip this cycle)

Do NOT execute the update. Wait for explicit greenlight. This gate applies to `hermes update`, any config changes, skill patches that modify system behavior, and profile operations. It does NOT apply to user-requested content work (writing, research, code review) — only to infrastructure changes.

### 1. Pre-update safety net

```bash
hermes update --check                    # See what's coming
hermes profile create pre-update-YYYY-MM --clone-all  # Full snapshot
```

Force backup regardless of config:
```bash
hermes update --backup --yes
```

Or enable auto-backup permanently:
```bash
hermes config set updates.pre_update_backup true
```

### 2. Update

```bash
hermes update --yes --backup
```

`--yes` auto-accepts the update's OWN interactive prompts (config-migration confirm, stash restore). `--backup` forces backup even if `pre_update_backup: false`.

**PITFALL (verified v0.16.0): `hermes config migrate` is a SEPARATE command and takes NO flags.** Do NOT call `hermes config migrate --yes` — `--yes` was removed and the call errors `unrecognized arguments: --yes`, so the migration silently never runs and your config stays one schema version behind. After `hermes update`, run bare `hermes config migrate` (no args). It is interactive-free in v0.16.0. The update's own `--yes` does NOT migrate config for you on a big jump — verify the version bumped with `hermes config check | grep -i version` (look for `Config version: N ✓`, not `N → M (update available)`).

**PITFALL: satellite profiles do NOT auto-migrate.** `hermes config migrate` only touches the profile you run it under (default). Each satellite (executor, ha-bot, voice-changer, stable-*) keeps its own `config.yaml` and must be migrated separately:
```bash
for prof in executor ha-bot voice-changer stable-2026-06-02; do
  cp ~/.hermes/profiles/$prof/config.yaml ~/.hermes/profiles/$prof/config.yaml.bak-$(date +%Y%m%d-%H%M%S)
  hermes --profile $prof config migrate
done
# verify all landed:
for prof in executor ha-bot voice-changer stable-2026-06-02; do
  echo "$prof: $(hermes --profile $prof config check 2>&1 | grep -i version | head -1)"
done
```
Symptom if skipped: a satellite shows `Config version: 25 → 27 (update available)` long after the default is on 27. They won't break loudly — they drift.

### 3. Post-update snapshot

```bash
hermes profile create post-update-YYYY-MM --clone-all
```

### 4. Test in isolation

```bash
hermes --profile post-update-YYYY-MM chat -q "verify all skills, cron jobs, and platform connections"
```

Check:
- Skills load without errors
- Cron jobs fire normally
- Platform connections (Discord, Telegram) functional
- Manifest router healthy
- Memory/knowledge DB accessible

### 5. Lock and cleanup

After confirming stability (wait a week):

```bash
# Replace stable snapshot
hermes profile create stable-YYYY-MM --clone-all
hermes profile delete stable-OLD-DATE

# Remove pre/post snapshots (keep pre-update for a week as safety)
hermes profile delete pre-update-YYYY-MM
hermes profile delete post-update-YYYY-MM
```

### Rollback if broken

```bash
hermes profile use pre-update-YYYY-MM   # Instant revert
```

## ⚠️ The venv-rebuild wipes the OAuth bypass (MANDATORY post-update step)

`hermes update` rebuilds the venv → **deletes `sitecustomize.py`** from
`venv/lib/python3.11/site-packages/`. If the main model is Anthropic-via-OAuth-bypass
(hermes-claude-auth), **every Anthropic call 401s the instant the venv rebuilds** until
the bypass is reinstalled. This is NOT optional cleanup — it is a required step in the
update sequence, and it's why the update should run as a detached script that reinstalls
the bypass before the controlling session can even check.

Post-update bypass restore:
```bash
cd ~/hermes-claude-auth && HOME=/root ./install.sh   # HOME must be set or it errors 'HOME: unbound variable'
ls venv/lib/python3.11/site-packages/sitecustomize.py    # must exist again
# verify live (must go through hermes, not raw curl — bypass is in the call chain):
hermes chat -q 'Reply with exactly: AUTH OK' --provider anthropic -m claude-sonnet-4-6 -Q
```

**PITFALL (verified 2026-06-06): install.sh CLOBBERS a customized bypass file.**
`hermes-claude-auth/install.sh` ships a VANILLA `anthropic_billing_bypass.py` — it does
NOT contain locally-added customizations (the complexity classifier `_classify_complexity`/
`_maybe_upgrade_model`, or any chained patches). Running install.sh silently overwrites the
customized file (observed: 931 lines → 833, classifier gone), so complex tasks quietly stop
auto-upgrading to Opus with NO error. After install.sh, ALWAYS re-verify customizations
survived and restore from a golden copy if not:
```bash
grep -c _classify_complexity ~/.hermes/patches/anthropic_billing_bypass.py   # expect >=1
```
The durable fix for this whole class of silent clobbering is now native hooks in
`plugins/local-guards/`, tracked in git (`git -C ~/.hermes checkout -- plugins/local-guards`
restores); `scripts/infra_watchdog.py` check 9 verifies they are armed (patch_guard retired 2026-10-16).

**PITFALL: install.sh can hang ~60s.** It may block on an internal step under the tool's
foreground cap. The bypass often already works (sitecustomize survives if install.sh got far
enough) — verify the live `AUTH OK` test rather than assuming the hang means failure.

## ⚠️ The update severs your own controlling session — run it detached

If you're driving the update from inside a gateway session (CLI or Telegram), `hermes update`
restarts the gateway and **kills the session mid-command**. Fire-and-forget naively and you
can be left with a half-done update, the bypass down, and no controlling session.

Pattern (full worked script: `references/detached-update-runner.md`):
1. Write a runner script under `~/.hermes/scripts/`.
2. Launch it via a system-level transient unit so it survives the gateway restart:
   `systemd-run --unit=hermes-update-$(date +%s) --collect bash ~/.hermes/scripts/hermes-update-runner.sh`
3. The runner reports each step to Telegram OUT-OF-BAND via the Bot API (the gateway is down,
   so in-session delivery won't work). It reads the bot token at runtime from `.env` — never
   inline the token (credential filter corrupts it; write the curl in a script file instead).
4. **`systemctl --user` does NOT work from a system-level systemd-run unit** (no user session
   bus: `Failed to connect to user scope bus`). The update itself already restarts the gateways,
   so the runner's own restart step is redundant and its failure is harmless — but don't rely on
   it; verify gateway PIDs/start-times afterward instead.
5. A `no_agent` runner's final "done" line can mask mid-step failures — read the log tail and
   verify live state (version, config version, bypass health) rather than trusting the last line.

## Cadence

**Weekly:** `hermes update --check` — monitor what's pending. Don't pull every week.

**Monthly:** Actual update. 99-commit gaps are tolerable for a month but not longer — risk of straddling a breaking change that requires intermediate migration steps you'd skip if you fell further behind.

**On-demand:** When a specific feature/fix is needed or a critical security patch drops.

## What NOT to update in lockstep with core

- **Skills** — curator handles staleness on its own 7-day cycle. Don't batch-update skills just because core updated.
- **Manifest** — separate service, separate update cycle. Only update when there's a reason.
- **Cron jobs** — scripts are stable. Audit monthly, not per-update.
- **Profiles** — rotate stable snapshots after confirmed successful updates, not before.

## Profile snapshot sizing

- `--clone-all`: 200–500MB (copies everything including cache, backups, state-snapshots). Use for major version jumps.
- `--clone`: ~10MB (config, .env, SOUL.md only). Sufficient for config tweaks or minor updates.
- For monthly Hermes core updates: `--clone-all` is right — the risk justifies the disk cost.

### Post-clone cleanup (critical)

`--clone-all` copies junk directories that should NOT live in snapshots. After every clone-all, immediately strip:

```bash
rm -rf ~/.hermes/profiles/<name>/backups/
rm -rf ~/.hermes/profiles/<name>/state-snapshots/
rm -rf ~/.hermes/profiles/<name>/image_cache/
rm -rf ~/.hermes/profiles/<name>/audio_cache/
rm -rf ~/.hermes/profiles/<name>/cache/
```

This can save 200MB+ per snapshot. The node/ directory (~204MB) is a bundled dependency — leave it.
