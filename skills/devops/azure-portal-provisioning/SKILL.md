---
name: azure-portal-provisioning
description: "Use when filling Azure portal create wizards cost-safely"
version: 0.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Azure, Provisioning, Cost, Security, Entra, DTG]
    related_skills: [claude-code, it-vendor-quote-review, microsoft-graph-automation]
    created_by: agent
---

# Azure portal provisioning: screenshot-driven walkthroughs

Use when Andrew creates Azure resources himself in the portal (employer tenant) and sends screenshots of each wizard tab asking "what do I fill in" / "is this fine". The agent has no tenant access: it reads the screenshot, says what to change field by field, and checks the Review page before he clicks Create. He is learning Azure, so add one line of mechanism per non-obvious choice.

He is an admin in the tenant. Do not add "check with your manager" or approval gates; he decides and grants. Real limits (MFA, a role he lacks) come from the error message, not from assumed process.

## Procedure (per screenshot)

1. **Read every field yourself.** The gateway's auto-caption summarises and drops fields (autogrow, auth method, admin logins, checkbox states). Run `vision_analyze` on the image path asking to "list every field label and its exact value, including checkboxes" before answering "is this fine".
2. **Lead with the cost-dangerous default.** Wizards default to production sizing (e.g. Postgres "Production" workload → General Purpose D4ads_v5, ~$250+/mo). If the visible size or the estimated-cost panel is wrong for the workload, say "don't create it with this" first, then the fix.
3. **Separate permanent from changeable settings.** Permanent at creation: resource name, region, networking/connectivity method (public vs private/VNet), backup redundancy. Changeable later: compute size/tier (short restart), storage (grow only, never shrink), firewall rules, auth method, tags. Spend the user's attention on the permanent ones; answer "can I upgrade later" from this list.
4. **Apply the default posture** (small personal/dev workloads):
   - own resource group per project (delete group = one-click rollback); dev/test subscription if one exists;
   - region where the employer's other workloads live; never accept a far default region silently (data residency);
   - smallest burstable tier, HA off, geo-redundancy off, 7-day backups;
   - Microsoft Entra authentication only (a local password login bypasses MFA/Conditional Access); if he keeps a password login, name it as break-glass and store it in the password manager/Key Vault;
   - public access with a single-IP firewall rule; never tick "allow any Azure service", never add 0.0.0.0–255.255.255.255; private access only if a VPN/VNet already exists;
   - tags `owner`, `purpose`.
5. **Firewall rule from "Add current client IP"**: remind him it adds whatever network he is on right now, so confirm it is the office egress IP, not a hotspot/VPN.
6. **Review + create gate**: ask for the Review page screenshot; check every field against the plan and the estimated monthly total. Call out any deviation (e.g. autogrow on) with its practical effect, then say plainly "good to create" or what to fix.
7. **Post-create steps**: give the next one or two concrete portal actions and what to report back.
8. **"Step by step" / "how do I get to X"**: numbered steps starting from the exact screen in his latest screenshot, one click path per step (menu > item > button), with a terminal command after each portal step that proves it worked, its expected output, and an error-to-cause list. A summary sentence like "deploy the model, copy the endpoint, assign the role" is not an answer to that request.
9. **Screenshot shows the wrong page**: name what it actually is and route back. Common: the top search bar matching a different service ("access control" lands on Operator Nexus ACLs, not IAM; use the resource's own left-menu **Access control (IAM)**); a Foundry **project** page instead of its **parent resource** (Networking, Keys and Endpoint and IAM live on the parent; use the Essentials "Parent resource" link); an API endpoint opened in a browser (blank page is normal; test it from PowerShell).

## Rules

- Naming: Microsoft CAF prefixes (`rg-`, `psql-`, `oai-`, `vnet-`, `kv-`), but the house convention in the existing tenant wins; tell him to check existing names first.
- Billing starts when the resource runs: state the expected monthly cost before he clicks Create.
- Control plane and data plane are separate permissions. Owner/Contributor or a wizard-granted role (e.g. "Foundry User" on a project) can still return `PermissionDenied ... lacks data action` when calling the service. Assign the data-plane role (e.g. `Cognitive Services OpenAI User`) at the resource scope, confirm with `az role assignment list --assignee <upn> --scope <id>` (empty output = no role there; a wizard grant often sits on the Foundry project, not the account), wait 5-10 min, then fetch a fresh token. Propagation cannot be forced; `az logout` + `az login` for a fresh token is the only lever, and only after ~30 min is a remaining denial a real fault.
- `AADSTS50076` / `InteractionRequired` on an `az` write (role assignment, resource update) means Conditional Access wants MFA on the CLI session. Fastest fix: do the same change in the portal, whose session already passed MFA. For later CLI use: `az logout` then `az login --tenant <id> --scope https://management.core.windows.net//.default` (add `--use-device-code` if no prompt appears).
- Managed identity on the wizard: only needed when the resource itself calls other Azure services. If the wizard forces a choice, pick System assigned (free, no permissions, deleted with the resource).
- Prices: cite live sources (Azure pricing page/calculator); free-tier promotions apply to new personal accounts, not company tenants. Mention the $0 routes (existing server, free DB offers, VS subscription credits) when cost is asked.
- Deployment page title `<ResourceType>_<GUID>` (e.g. `PostgreSqlFlexibleServer_5d3b…`) is the portal's auto-named deployment record, not the resource name; it is normal and only shows in the resource group's Deployments history.
- Windows deliverables (.ps1) for his DTG laptop: pure ASCII + UTF-8 BOM + CRLF. PowerShell 5.1 reads BOM-less UTF-8 as cp1252; an em dash becomes a smart quote that PowerShell treats as a string delimiter -> 'missing string terminator'. Scan for bytes >0x7F before shipping.
- Windows shell gotchas: an admin PowerShell opens in C:\Windows\system32 (Claude Code then asks to trust system32: say No, cd to a work folder, use a non-admin window); a Git Bash/MSYS window is not PowerShell (`$powershell` expands to empty).
- Packages he runs: verify every documented step against the actual code before sending (e.g. init scripts that assume the database already exists; hook config path/shape) and fix or flag it in the step list.
- One screenshot = one short reply: the changes first, then "keep" items, then the next tab.

## DTG tenant facts (verified Oct 2026)

- Andrew sees ONE subscription: **JEV Training and DEV** (Owner, Central US; holds `rg-claude-mem`, `rg-mira-*`). Matt's apps (`impeller-dev-*`, `dtg-tech-survey-prod-*`, likely Kezia) CNAME to `*.azurewebsites.net` yet show no RG → they sit in a subscription he lacks RBAC on. Fix once: Entra → Properties → "Access management for Azure resources" = Yes → `az role assignment create --role Reader --assignee <upn> --scope /` → toggle back to No.
- House region = **Central US** (not East US). Match it.
- Entra and ARM are separate planes: an app registration (SSO) lives in no RG/subscription. "App has SSO but no resource group" means hosting is elsewhere, not a contradiction.
- DTG apps go on App Service, never a domain controller (Tier 0). Mira env scheme: `rg-mira-<env>`, `app-mira-<env>`, `kv-mira-<env>`, one app registration per env, hosts `mira-dev/mira-staging/mira.<company-domain>`. App Service staging = a slot (needs S1), not a third plan.
- DNS is split-brain: public at Network Solutions (no API, manual), internal on the DCs (`Add-DnsServerResourceRecordCName` via RSAT). Add both or the site works only on-site/off-site.
- Claude Code visibility stack on his laptop: `az` CLI (ARM + `az ad` for Entra) → Azure MCP (`claude mcp add azure -s project -- cmd /c npx -y @azure/mcp@latest server start`, reuses `az login`, no Entra writes) → RSAT for AD/DNS reads → PowerCLI (vCenter down Oct 2026; connect per ESXi host). Discovery read-only; writes as reviewed scripts.
- Mira decisions + Phase 0 walkthrough: `~/reports/mira/` (storage rev2 = Azure SQL serverless content + Blob media, push-on-publish to Foundry IQ, no Jev).

## References

- `references/postgresql-flexible-server.md`: field-by-field values for a small Postgres + pgvector store, cost figures, post-create steps, and moving to another region (dump/restore recipe).
- `references/azure-openai-embeddings.md`: Foundry / Azure OpenAI resource for an embedding model: wizard fields, model deployment, data-residency deployment type, endpoint format, Entra role, PowerShell smoke test and error table.
- `references/azure-ai-search.md`: AI Search / Foundry IQ for RAG: tier choice by per-file limits, getting on-prem files in (AzCopy → ADLS Gen2), keyless role set, pipeline choices, cite-or-refuse query pattern, GA vs preview permission trimming.
- Foundry resource vs project: the **resource** (e.g. `oai-*`, kind Foundry) holds model deployments, endpoint, IAM and billing; a **project** under it is a workspace for agents/evals/playground and is not called by apps that hit the resource endpoint. A project auto-created by the wizard can be left alone.
- An embedding deployment stores nothing; one deployment serves any number of databases/users/indexes. Everything compared together (stored and query vectors) must use one model and the matching vector dimension (`text-embedding-3-small` = `vector(1536)`). Add a second deployment of the same model only for throughput isolation (a bulk AI Search first indexing run shouldn't throttle interactive recall) or to split usage figures. Quota (TPM) is shared per subscription + region + model, so a second deployment splits it; more headroom = quota increase request.
- A database server in a different region from the embedder works (Entra auth is cross-region; tens of ms). If the region was chosen for data residency, the text still goes to the embedder's region; true residency needs an embedding deployment in that geography too.
- Per-user clone of a single-user Postgres store: one database per user on the existing server (`pgaadauth_create_principal`, `CREATE DATABASE`, `REVOKE CONNECT ... FROM PUBLIC`, grant CONNECT/CREATE + schema to the UPN, `CREATE EXTENSION vector`), shared embedder via an Entra group holding Cognitive Services User; a new server per user only adds cost. Strip the author's UPN from installer defaults before handing a pack out.
