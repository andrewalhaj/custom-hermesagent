# Azure AI Search (aka Foundry IQ): sizing, setup, permissions

Use when designing or provisioning an AI Search service for RAG (file-share knowledge hub, Mira's Ask Mira index, a Claude Code search tool). Full design doc: `~/reports/azure-ai-search-dtg-guide.md`. Re-check prices/limits on the live pages before quoting — they move.

## Naming
Microsoft markets it as **Foundry IQ (Azure AI Search)**; the pricing page title says "Foundry IQ". Same service and portal blade. "Foundry IQ knowledge bases" are the optional agentic layer on top.

## Tier choice — decide on per-file limits, not total GB
| Tier | List $/SU/mo (US) | Storage/partition | Vector quota/partition | Max file (indexer) | Max chars extracted/file |
|---|---|---|---|---|---|
| Basic | ~74 | 15 GB | 5 GB | 16 MB | 512,000 |
| S1 | ~245 | 160 GB | 35 GB | 128 MB | 4 M |

- Basic silently skips files >16 MB and truncates text past 512k chars — large PDFs/scanned binders hit both. Pilot on Basic, size production from an inventory of % files >16 MB.
- Serverless (preview): per CU-hour + per GB, scales to zero, 1 GB/index, no SLA — sandbox only.
- Semantic ranker: 1k requests/month free, then $1 per 1k. OCR/Layout bill on an attached Foundry resource; embeddings bill on the Azure OpenAI resource.
- 2 replicas = read SLA; add only when needed (doubles cost).
- Same region as storage + embedding deployment.

## Getting on-prem files in
- AzCopy `sync` (scheduled task) → **ADLS Gen2** storage account (hierarchical namespace on) → ADLS Gen2 indexer (GA). Recommended.
- Azure File Sync → Azure Files works, but the **Azure Files indexer is preview**.
- Copilot File Share connector respects NTFS ACLs but indexes into M365 Search/Copilot — not usable as an app's own index.
- AzCopy does not carry NTFS ACLs to Blob/ADLS: what you sync is what's searchable.

## Keyless setup
1. Search service: Keys → API access control = RBAC; Identity → system-assigned on.
2. Roles: search MI → **Storage Blob Data Reader** (storage), **Cognitive Services OpenAI User** (embedding resource), **Cognitive Services User** (Foundry resource for OCR/Layout). Admin → Search Service Contributor + Search Index Data Contributor. App → Search Index Data Reader (+ Data Contributor if it pushes). Token scope `https://search.azure.com/.default`.
3. Build the first pipeline with **Import data (new) → RAG/vectorize** wizard, then export index/skillset/indexer JSON to a repo and manage as code.
4. Use a dedicated embedding deployment so bulk indexing can't throttle other consumers.

## Pipeline choices
- Heading-aware chunks (Document Layout skill) match our measured winner; Text Split (~2,000 chars, ~10% overlap) is the cheap fallback.
- OCR on for shares with scans, else scans index as empty.
- Soft-delete detection so server deletions disappear from search.
- App-owned content (Mira) uses the **push API** from its publish queue, not an indexer.
- Use an **index alias** so schema rebuilds swap behind a stable name.

## Query pattern for cite-or-refuse RAG
`search` + `vectorQueries` (hybrid/RRF) + `queryType: semantic` + permission `filter`, top 5. Refuse when the best `@search.rerankerScore` (0–4) is under a calibrated cutoff — that's the fix for vector search always returning k hits.

## Permissions
- **Security filter** (group IDs in a filterable, non-retrievable field; app passes the user's groups) — GA; use for app-owned indexes.
- **Native ACL/RBAC trimming** (ADLS ACLs ingested; user token in `x-ms-query-source-authorization`) — preview, REST/preview SDK only, no portal, owning-user/group/other ACL entries unsupported, permission changes need a re-trigger (touch, `/resetdocs`, `/resync`). Start with shares everyone can read; restricted shares wait for GA + NTFS→Entra group mapping.
- Run a permissions inventory (shares open to Everyone/Domain Users holding HR/finance/contract/export-controlled data) before syncing anything.
