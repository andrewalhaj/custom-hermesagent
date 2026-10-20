---
name: claude-code-windows-hooks
description: "Use when writing Claude Code hooks/installers on Windows"
---

# Claude Code hooks + installers on native Windows

Use when writing or debugging Claude Code hooks, settings merges, or PowerShell installers for Windows. Lessons from the DTG laptop parity add-on (2026-10-14); each was found live on Windows after Linux tests passed.

## Hook output schemas (check https://code.claude.com/docs/en/hooks first)
- **Stop hook block**: `decision`/`reason` must be TOP-LEVEL JSON (`{"decision":"block","reason":...}`). Nested inside `hookSpecificOutput` it is silently ignored. Keep the `stop_hook_active` loop guard.
- **PreToolUse context-only**: never emit `permissionDecision: "allow"` just to attach `additionalContext` - it bypasses the user's permission prompt (safety regression). Emit context without a decision; reserve `deny` for real failures.
- **Counting tool calls from the transcript JSONL**: the same assistant tool_use appears in multiple entries. Dedupe by `content[].id` or counts double (nudge fired at call 3 instead of 4).
- Sessions opened and closed without a message have no transcript file - SessionEnd hooks must treat 'not found' as a normal skip.

## Shell tool on Windows: Bash AND PowerShell
- Claude Code on Windows runs commands through its **PowerShell tool** by default (source: https://code.claude.com/docs/en/tools-reference#powershell-tool). A guard hook whose matcher is only `Bash`, or whose script exits early when `tool_name -ne 'Bash'`, never fires on a Windows laptop. Use matcher `Bash|PowerShell`, accept both tool names in the script, and include PowerShell-tool cases in the test suite.
- Mirror every shell deny rule: `Bash(git push --force *)` needs a `PowerShell(git push --force *)` twin. Current prefix syntax is a space then `*`; check https://code.claude.com/docs/en/permissions before reusing older `:*` examples.
- Deny rules match the command's prefix only, so `npm test && git push --force` gets past them. Rely on the hook for chained or variant commands, and on server-side branch protection as the real enforcement. Hooks that fail open on their own errors cannot be the only guard.

## Python on Windows
- `subprocess.run(..., text=True)` without `encoding=` uses cp1252 -> `'charmap' codec can't encode '\u2714'` when piping transcripts to `claude -p`. Always pass `encoding="utf-8", errors="replace"`. Reproduce on Linux by patching `locale/_locale.getencoding` + `getpreferredencoding` to cp1252 and running with `-X utf8=0`.
- Use `tempfile.gettempdir()`, never `TMPDIR`/`/tmp`.

## PowerShell installers (target = Windows PowerShell 5.1)
- `& claude mcp add ... -- npx ...` registered nothing under PS 5.1 while the http transport (no `--`) worked. Suspected: bare `--` consumed by PS 5.1; quote it (`'--'`) and wrap stdio npx servers as `cmd /c npx` per docs. Always verify each add with `claude mcp get <name>` and print FAIL loudly.
- `Sort-Object` has NO `-Ascending` parameter (ascending is the default; only `-Descending` exists). It errors in PS 5.1 and 7.
- Under `Set-StrictMode -Version 2`, `$obj.psobject.Properties.Name -contains 'x'` THROWS ('property Name cannot be found') when the object has zero properties (e.g. a freshly created `'{}' | ConvertFrom-Json`). Use the indexer: `$null -ne $obj.psobject.Properties['x']`. A first-time key like `enabledPlugins` hits this on every real laptop.
- Linux pwsh test harness: run a COPY of the script with every `\` replaced by `/`, USERPROFILE pointed at a temp profile seeded with the real laptop's settings shape (incl. empty objects). Without the replacement, `[IO.File]` writes literal-backslash filenames and the merge path is never exercised. Rerun-dedupe results in that copy are unreliable (escaped-path mismatch); confirm idempotency from the laptop's '[SKIP] already registered' output.
- Static parse checks plus a 'fake profile' run did NOT catch either bug above; subagent E2E claims need a seeded-profile rerun by the parent.
- .ps1 files: ASCII-only, UTF-8 BOM, CRLF. Write JSON with `New-Object Text.UTF8Encoding $false` (no BOM).
- `-WhatIf` prints a backup folder name without creating it - don't mistake it for a second run.
- Rollback must restore the OLDEST backup (the pre-install original); newest is wrong after a re-run.
- Plugins installed via `/plugin install` from inside a project can land at `scope: project`; enable at user scope (`enabledPlugins`) if they must load everywhere.
- `extraKnownMarketplaces` is an OBJECT keyed by marketplace name (`{"dtg-tools":{"source":{"source":"git","url":"https://...git"}}}` or `{"source":"github","repo":"org/repo"}`), not an array; `enabledPlugins` is an object `{"plugin@marketplace": true}`. Subagents repeatedly ship arrays or invented keys (`sourceUrl`, `sourceType`) that Claude Code silently ignores, so the plugin never loads. Before shipping any settings template or merge, diff its shape against https://code.claude.com/docs/en/settings-reference. When merging, leave existing values untouched and add a key only when absent.
- Settings JSON allows no comment keys (`"//"`, `"_comment"`); put placement notes in the README. Managed settings on Windows: `C:\Program Files\ClaudeCode\managed-settings.json`, HKLM `SOFTWARE\Policies\ClaudeCode` value `Settings` (Intune/GPO), or server-managed settings in the claude.ai admin console. Repo-level `extraKnownMarketplaces` applies only after the user trusts the folder.
- `claude plugin validate` passing does not check settings templates, the hook's tool matcher, or Windows behaviour. Audit those against the docs separately.

## Permission mode drift
- `permissions.defaultMode` in user settings.json flipped to `auto` after install. Likely cause (unconfirmed): accepting the prompt option 'Yes, and switch to auto mode'. Tell users never to pick it during setup; re-check with `Select-String settings.json -Pattern defaultMode`.
- Prompts like 'a variable in this command can't be checked' come from `$USERPROFILE` in Bash commands; instruct Claude to use literal paths in read-only commands.

## Versioning `~\.claude` in git
- If the repo root is `~\.claude` itself, add a `.gitignore` before the first `git add .`: `.credentials.json` (sign-in token), `projects/` (full transcripts, may contain secrets), `history.jsonl`, `todos/`, `shell-snapshots/`, `statsig/`, `ide/`, `plans/`, `*.local.json`, `.env`. Check with `git ls-files`. A secret already pushed: rotate it first, then rewrite history.
- Shareable files are `CLAUDE.md`, `settings.json`, `skills/`, `commands/`, `agents/`, `hooks/` and scripts. Scan them for tokens, IPs and `C:\Users\<name>` paths (use `$env:USERPROFILE`) before pushing to a company repo.
- A user-level `~\.claude` repo is a personal backup. Team-wide rules and skills belong in a plugin marketplace repo or each project's `.claude\` folder, because cloning someone's `~\.claude` overwrites the colleague's own config.
- `git log --show-signature` complaining about `gpg.ssh.allowedSignersFile` only means SSH verification isn't set up; harmless for unsigned commits.

## Verification habit
- Ship nothing on Linux-only test passes: have the user run the checklist inside Claude Code on the laptop and read the actual logs (`.claude-memory\logs\session-summary.log`).
