# Azure Database for PostgreSQL Flexible Server: small store (e.g. agent memory + pgvector)

## Basics tab
- Resource group: create new, dedicated.
- Server name: lowercase letters, digits, hyphens; permanent.
- Region: where the company's other workloads live (avoid default far regions).
- PostgreSQL version: 17 unless the needed extension is confirmed on the newest major.
- Workload type: Dev/Test (Production pre-selects General Purpose sizing).
- Compute + storage → Configure server: Burstable, Standard_B1ms (1 vCore, 2 GiB), Premium SSD 32 GiB, HA disabled, 7-day backup, geo-redundancy off. Autogrow: either is fine for a tiny store; on prevents read-only-when-full, off guarantees cost cannot creep.
- Availability zone: no preference.

Observed estimates (Central US): B1ms compute ~USD 14/mo + 32 GiB storage ~USD 4.16/mo = ~USD 18/mo. B2s compute ~USD 56/mo. Embeddings (text-embedding-3-small) ~USD 0.02 per 1M tokens: cents per year.

## Authentication
- "Microsoft Entra authentication only"; set the user as Entra admin. The "PostgreSQL and Entra" option adds a password login that bypasses MFA. Changeable later under Security → Authentication.

## Networking (connectivity method is permanent)
- "Public access (allowed IP addresses) and private endpoints", one firewall rule for the office egress IP.
- "Allow public access from any Azure service": unticked. No 0.0.0.0–255.255.255.255 rule.
- Firewall rules are editable later (add home IP when needed; remove unused ones).

## Security / Tags
- Data encryption: service-managed key.
- Tags: owner, purpose.

## Changes after creation
- Compute size/tier: change any time; brief restart (minutes).
- Storage: increase only.
- Resource group / subscription: portal **Move**, no downtime, no client change.

## Moving region (= new server + copy)
| Option | When | Notes |
|---|---|---|
| pg_dump / pg_restore | small store (default) | minutes of downtime |
| Geo-restore | geo-redundant backup was enabled at creation | paired region only |
| Cross-region read replica → promote | near-zero downtime needed | not on Burstable |

Dump/restore with Entra tokens:
```powershell
$env:PGPASSWORD = az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv
pg_dump -h <old>.postgres.database.azure.com -U <upn> -d <db> -Fc -f mem.dump
# new server: same major version, Entra-only, azure.extensions += VECTOR, CREATE DATABASE, CREATE EXTENSION vector,
# re-create each principal with pgaadauth_create_principal(...)
pg_restore -h <new>.postgres.database.azure.com -U <upn> -d <db> --no-owner mem.dump
```
Vectors copy as-is (same embedding model, no re-embed). Repoint each client's DB host, run its diagnostic, keep the old server a week, then delete. An empty just-created server in the wrong region: delete and recreate (or fold into the existing server as a new database) before anyone depends on it.

## Review + create check
- Entra-only shows up as an Entra administrator line with NO separate admin login line; if an admin login appears, the password login is still on.
- Expected Central US total for the values above: ~USD 18/mo. A much higher figure means a size change did not stick.

## Post-create for pgvector
The Overview page is the SERVER; the app connects to a DATABASE inside it. Create that first: Settings → Databases → + Add → name (e.g. `claude_memory`) → Save. Setup scripts usually create tables, not the database.
1. Server parameters → `azure.extensions` → tick VECTOR → Save (allow-lists the extension).
2. Then `CREATE EXTENSION vector;` in the target database, followed by the schema.
3. Wiring it to Claude Code as memory (MCP server, Entra token auth, FTS-first): `claude-code` skill, `references/work-device-starter-pack.md`.
