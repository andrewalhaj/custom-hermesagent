# Scaling a per-user Claude Code setup to a team, and the memory vs knowledge-hub split

Use when the user asks how to roll the work-laptop setup out to more people, or whether its memory database should become a shared/central store or the base of a knowledge product. Verify managed-settings paths, setting keys and the plugin format against current docs before building; they change often.

## Distribution: one plugin plus central policy
1. Package the add-on (skills, subagents, hooks, MCP servers) as ONE plugin in a private employer Git repo used as a plugin marketplace. Updates ship as a push, not a re-run installer per laptop.
2. Deploy machine-wide managed settings (Intune) that register that marketplace, enable the plugin and required third-party plugins, optionally lock other marketplaces, and enforce the deny list, default permission mode, model, and no-bypass. Check whether the Enterprise admin console's server-managed settings cover the same keys.
3. Same Intune package installs prerequisites via winget (Claude Code, Node, Python, Azure CLI, Git).
4. Split identity: SOUL rules + employer boundaries + deny list become org policy; USER.md is per person (blank template).
5. Pilot 2-3 users with metrics fixed in advance (recall usefulness, hooks disabled as annoying, usage per seat from admin analytics), then roll out. The health checklist is the per-user acceptance test.

## Memory store at team scale
- Run the memory MCP centrally as an HTTP server (e.g. Azure Container Apps, Entra auth) instead of a per-laptop venv; removes local Python and per-IP firewall rules.
- Per-user isolation in the database: owner column + Postgres row-level security keyed to the Entra principal.
- Grant DB access and the embeddings data-plane role to an Entra GROUP; onboarding = group membership.
- Network: private endpoint + VPN or office IP ranges, not single-IP rules.
- Migration from the single-user store is cheap (add owner, backfill the current user); do not build team schemas before requirements exist.

## Memory is not the knowledge hub
Recommend separate stores with a reviewed promotion path; never point a shared hub at raw agent memory.
- Agent memory is noisy (guesses, session summaries, facts stale within a week) and personal/sensitive; unreviewed it degrades any shared corpus.
- Structure: `memory` (private per user, RLS) -> `proposals` (flagged for team value) -> reviewed -> `knowledge`/hub (owner, source, last-verified date, review status, visibility).
- If the hub is a product with its own app (e.g. a training/procedures app), it owns its own database and schema migrations; agent memory feeds it only by submitting drafts into the app's own review workflow. No shared tables, and a dev/test-subscription personal store is never the production base.
- AI answers over the hub cite only published/reviewed versions; store chunks + vectors per published version.
- Text and metadata (pages, versions, review state, completions) in the app database; media and large files in blob storage with links in the DB.
- Existing docs: import what people train on or follow step by step (gets versions, review, tracking; bulk-convert as drafts); link out to vendor/reference material (cannot be cited by the in-app AI).
- If most hub content is existing SharePoint/Teams material with its own permissions, consider a search service with native permission trimming (Azure AI Search) for that part rather than re-implementing ACLs in Postgres.

Before designing the hub, ask who reads it (team / company / customers) and where the content lives today; read the product's build plan first if one exists and anchor the answer in its decisions.
