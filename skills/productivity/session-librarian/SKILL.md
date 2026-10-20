---
name: session-librarian
description: "Use when finding, renaming, archiving or pruning sessions"
version: 1.0.0
author: Hermes Agent + Teknium
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Sessions, Organization, Cleanup, Library, Productivity]
    category: productivity
    related_skills: []
---

# Session Librarian

Manage the user's session library conversationally: find past sessions about a
topic, summarize what they decided, rename them meaningfully, split work into
parallel sessions, and propose stale ones for archive or deletion — all from a
plain-language request like *"find my sessions about Q3 pricing, keep the
useful ones, and clean up the duplicates."*

Inspired by Perplexity Computer's prompt-driven session management (Aug 2026):
the agent starts, organizes, and cleans up the user's own session library, and
always shows the plan before touching anything.

## When to Use

- "What sessions do I have about X?" / "What did we decide about X?"
- "Rename these sessions to something meaningful."
- "Clean up my session library" / "archive the stale ones."
- "Fork that session into a follow-up focused on Y."
- "Split this into one session per ticket" (see Parallel workstreams below).
- "Continue where we left off" in a fresh session after a model switch / /new /
  restart, or "why was that reply so slow" → `references/session-handoff-and-latency.md`.

## The Two Surfaces

| Task | Surface |
|---|---|
| Find sessions by topic, read content, summarize decisions | `session_search` tool (FTS5 over the message store) |
| List/filter by metadata (age, source, cost, tokens, workspace) | `hermes sessions list` / `stats` via terminal |
| Rename | `hermes sessions rename <session_id> <title...>` |
| Bulk soft-hide (reversible) | `hermes sessions archive <filters>` |
| Delete (destructive) | `hermes sessions delete` / `hermes sessions prune <filters>` |
| Export before deleting anything valuable | `hermes sessions export --session-id <id> --format md` |
| Continue work in a new place | `/branch` (fork current session) or start a fresh session and cite the summary |

## Procedure

① **Discover.** Use `session_search(query=..., limit=5-10)` with topic
keywords; vary phrasing (feature name, symptom, project name). For metadata
sweeps ("sessions older than 60 days from telegram"), use
`hermes sessions list --source telegram --limit 50` instead.

② **Summarize per session.** The discovery result's `bookend_start` (goal),
match window, and `bookend_end` (resolution) usually suffice — only dump a
full session (`session_search(session_id=...)`) when the user asks for
decisions in depth. Report each as: link (`@session:` form) — one-line goal —
one-line outcome.

③ **Plan before acting (MANDATORY for anything that mutates).** Present a
plan table first: which sessions get renamed to what, which get archived,
which are proposed for deletion and why (duplicate of which keeper, stale,
empty). Wait for the user's go-ahead. Exception: a single rename the user
explicitly dictated can be done directly.

④ **Act with the safest primitive.**
- Prefer `archive` (reversible soft-hide) over `delete`/`prune`.
- Always run destructive commands with `--dry-run` first and show the output,
  then re-run with `--yes` after confirmation.
- Before deleting anything with meaningful content, offer
  `hermes sessions export --format md` as a backup.

⑤ **Report.** Renames applied, sessions archived (count + how to undo:
archived sessions remain in the DB and are listed with `--include-archived`),
anything exported, anything skipped and why.

## Parallel Workstreams

For "one session per ticket, investigate each, report back": do NOT try to
drive other live sessions. Use `delegate_task` with one task per workstream —
each subagent runs in its own session automatically — then synthesize their
summaries. Mention that each delegation's transcript is itself searchable
later via `session_search`.

## Session Handoff (model switch / new session)

The recall tool is `session_search` (a plugin — find it via `tool_search` if it is not in the
loaded list; there is no `chat_history_lookup`). Start with no args to list the most recent
sessions, then `query=` for the topic, then `session_id=` + `around_message_id=` to scroll.
For a compact "what did the user say last" view, query `state.db` read-only and print only
`role='user'` rows for that session id, skipping gateway-injected `[IMPORTANT …]` /
`[ASYNC …]` rows.

A model switch or `/new` creates a new session id but does NOT stop the old
one — it keeps executing tool calls and can commit to the same repo minutes
later. Before continuing "where we left off": reconstruct from `agent.log`
+ the message store, verify live (`git log -1`, rerun the verification
script), then grep `agent.log` for the old session id — if it is still making
API calls, ask the user to stop it or stand down; never write to the same
working tree in parallel. Full procedure and the slow-reply diagnosis ladder:
`references/session-handoff-and-latency.md`.

## Pitfalls

- **Never delete without a dry-run + explicit confirmation in this
  conversation.** A standing "clean things up" is authority to *propose*, not
  to prune.
- **`session_search` finds content, not metadata.** Age/cost/source filters
  live in the CLI; combine both when the request mixes them ("old sessions
  about pricing").
- **Titles are identity for `/resume <title>`.** When renaming, keep titles
  short, unique, and prefix-friendly; warn the user if a rename collides with
  an existing title.
- **Archived ≠ deleted.** Archive hides sessions from default listings only.
  Say which one you did.
- **Cross-profile session links** (`@session:<profile>/<id>`) are read-only
  from another profile; management commands act on the current profile's DB.
- **Recalling a past fact or attachment ("what was the name of X", "paste the
  screenshot I sent"):** check `memory_search` first, then `session_search`, and
  answer from the hit with its `@session:` link. If the fact came from a session
  and wasn't in Supabase, `memory_add` it in that turn so the next recall is one
  call. For a user-sent image, the session text gives its cached filename
  (`img_<hash>.jpg`). The live `cache/images/` folder gets pruned, so if the file
  is missing there, search the whole disk by filename before saying it's gone:
  `find / -xdev -name 'img_<hash>*'`. Profile snapshots taken before updates
  (`profiles/pre-update-*/cache/images/`) often keep a copy. Check it with
  `vision_analyze` to make sure it's the right image, then send it with
  `MEDIA:<path>` and say where it was found.

## Verification

After a cleanup pass, re-run the discovery query and `hermes sessions list`
to confirm the library reflects the plan (keepers present with new titles,
archived ones gone from the default listing).
