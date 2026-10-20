> **HISTORICAL (2026-10-16):** `~/.hermes/patches/` is deleted (bypass + classifier in P2, guard patches in P3) and the py3.11 `sitecustomize.py` hook is removed. Paths below under `patches/` no longer exist. Guards are native hooks in `~/.hermes/plugins/local-guards/guards/`.

> ⚠️ **GATEWAY-DEAD as of 2026-10-13.** The 3.14 gateway runtime has no sitecustomize.
> This document describes the CLI load path (py3.11 repo venv) only. The new
> provider-independent gateway loader is `~/.hermes/plugins/local-guards/` (enabled
> via `config.yaml` `plugins.enabled`; runs at both gateway AND CLI startup via
> `discover_plugins()`). The architectural reasoning below (provider isolation) is
> still valid; the implementation described no longer applies to the gateway.

# Provider-Independent Patch Installation (sitecustomize.py pattern)

Architecture decision (2026-06-06): delegation-checkpoint guard uses the
`sitecustomize.py` startup path for provider-independent installation.

## Why this matters

The `hermes-claude-auth` bypass (and any patch chained from it) only loads
when `agent.anthropic_adapter` is imported — which only happens on
`api_mode == "anthropic_messages"` sessions. A DeepSeek-only session at
fresh-gateway startup never triggers that import, so any guard chained from
the bypass alone would be silently absent for that session.

## The solution: sitecustomize.py startup block

A standalone `import delegation_checkpoint` block in sitecustomize.py
(provider-independent) PLUS a belt-and-suspenders chain from
`anthropic_billing_bypass.apply_patches()` (Anthropic path). Both calls are
idempotent — a module-level `_INSTALL_STARTED` flag prevents double-arming.

## What makes a guard provider-independent

The guard patches `AIAgent._execute_tool_calls` — a class method shared by
every agent instance in the gateway process. Once installed (at Python
startup via sitecustomize), it covers all providers identically.

## What it does NOT protect against

- The guard wraps `_execute_tool_calls` on ONE class. If Hermes ever
  introduces a parallel agent class or a separate tool-execution path, the
  guard silently doesn't apply there. Defensive design: the wrapper is fully
  exception-safe and no-ops on any failure, so a core refactor just means
  a missing guard, not a broken gateway.
- Counter state (`_deleg_ckpt_terminal`) lives on agent *instances*, not
  the class. A model switch that rebuilds the agent instance resets the
  counter. This is acceptable — a model switch is arguably a fresh logical
  stretch of work.
