---
name: azure-storage-migration
description: "Use when mirroring/migrating a file share to Azure storage."
version: 0.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Azure, Blob, ADLS, AzCopy, file-server, migration, bandwidth]
    related_skills: [it-vendor-quote-review, azure-portal-provisioning, retrieval-benchmarking, windows-server-administration]
    created_by: agent
---

# On-prem file share -> Azure storage (mirror or migration)

## When to Use
A Windows file share is to be copied or synced into an Azure storage account (Blob/ADLS Gen2) -
for a search/RAG index, a cloud copy, or a real move - and for the questions around it: sizing bandwidth,
scheduling AzCopy, simulating before game day, and reading an MSP's "bandwidth" objection.

## Always-on rules
- **Say which design it is before anything else: mirror or migration.** Mirror = server stays source of
  truth, nightly one-way sync, users never browse the container (the blob is the indexer's reading copy and
  the citation target). Migration = users work from Azure over the WAN - a different project with latency
  and VPN costs. Most "cloud search" asks are mirrors; most objections assume migrations.
- **Employer data stays off the personal stack.** Simulations use synthetic trees; the inventory script
  emits aggregates only (no paths) by default. No real hostnames/IPs in shared artifacts.
- **Mirror = derived data.** Protect it from accidents (soft delete, versioning, resource lock, optional
  immutability policy on the container); do not rent backup for it - the originals are already backed up.
  The day the server is retired, that flips and the container needs a real vaulted backup.
- Hand-paste rule: one short exact command per code block; scripts go as files, not pasted heredocs.

## Procedure
1. **Inventory (read-only, per share)** - `scripts/Get-ShareInventory.ps1` on the server, elevated:
   `powershell -ExecutionPolicy Bypass -File .\Get-ShareInventory.ps1` (add `-Share <name>` for one share,
   `-IncludePaths` only if the 10 largest files' paths are wanted). Output JSON decides: tier (files >16 MB
   skip on AI Search Basic), exclusions (extension mix, `untouched5y`), path risk (`longest_path_chars`,
   `paths_over_260` - NTFS 260 vs Blob 1,024-char names), churn (`mod30d` -> nightly delta), and whether the
   share may be copied at all (`open_to_everyone`, NTFS root ACL - AzCopy drops NTFS permissions, so sync only
   what everyone may read; policy before platform).
2. **Bandwidth = measured upload, not the plan name.** Get: the ISP contract line (carrier, up/down, DIA vs
   best-effort), a 30-day WAN graph from the firewall (shows the overnight baseline - backups already use the
   window), the backup job history (GB/night), and a measurement from the server itself: speedtest upload at
   02:00 and 14:00 (screen - 15 s burst to a nearby node), then `azcopy bench <container-url> --file-count 50
   --size-per-file 100M` and again with `--size-per-file 50K --file-count 5000` (proof - real region, and
   small files are latency-bound). `tracert 8.8.8.8` hop 2-3 names the carrier in ten seconds.
3. **Hours = GB x 8 / Mbps / 3.6 / 0.7** (0.7 = small-file and retry overhead). Decide: fits a weekend ->
   one `azcopy copy`; days -> nightly task + daytime `--cap-mbps` at ~50-60% of upload; weeks -> Azure Data
   Box for the bulk, wire for the delta. Nightly delta = `mod30d` churn / 30.
4. **Storage account (Terraform or portal):** ADLS Gen2 (hierarchical namespace on - the GA indexer wants
   it), one container per share, public access off, soft delete + versioning, resource lock, RBAC role for the
   copy identity and `Storage Blob Data Reader` for any citation-reader group. Same region as the search
   service and embedder (house region first). Wizard walking: skill `azure-portal-provisioning`.
5. **Scheduled task on the server** - action = `azcopy.exe sync "<share path>" "<container url>"
   --cap-mbps <N> --log-level WARNING` directly (NOT a .bat/.ps1 wrapper: Task Scheduler's "stop the task if
   it runs longer than" stops the container process and leaves the child azcopy running). Nightly trigger with a
   stop-after of the window length and "do not start a new instance"; weekend trigger uncapped. `--dry-run`
   before the first real run and before ever adding `--delete-destination`; leave delete-destination off
   until the copy is complete and verified (a moved folder or a typo'd source would wipe the container).
   `--exclude-path` scratch/temp folders. Don't `jobs resume` nightly - the next `sync` re-scans and skips.
6. **Cutover (migrations only):** share read-only -> one final `sync --compare-hash` (catches edits that
   didn't bump mtime; too slow for nightly) -> reconcile counts + hashes both sides -> repoint. Bulk-copy
   nights can run with users active (a file changed mid-upload fails or re-sends next night; nothing partial
   lands); the cutover run is the one where "no modifications during transfer" must be literally true.
7. **Simulate first when game day is on managed servers:** Azurite in Docker + a synthetic tree with the
   inventory's shape (depth, spaces/unicode names, a 0-byte file, one 300 MB file, a locked file, a .lnk) ->
   first sync, edit/delete, re-sync, `--delete-destination` behaviour, reconcile script. `terraform validate`
   proves the module with no credentials; `terraform plan` against the personal dev subscription only with a
   greenlight (read-only, adds SKU/region/policy checks). Nothing emulates AI Search/Foundry IQ locally - the
   managed retrieval layer is measured only on a (free-tier) real service: see the retrieval-benchmarking
   skill's managed-RAG-platform reference.
8. **Notify the MSP by ticket, not by asking** - footprint on the managed host, owner = in-house IT as
   co-managed provider, rollback, concerns in writing by date. Clause map and objection triage: the
   it-vendor-quote-review skill's MSP-contract-clauses reference.

## Pitfalls
- A speedtest rules out a thin plan; it does not prove sustained throughput to Azure's region. Lead the
  meeting with the WAN graph + `azcopy bench`, keep the speedtest as the screen.
- Upload saturation stalls everyone's download (TCP ACKs ride the upstream): cap daytime runs even when the
  window math says they fit.
- `azcopy sync` enumerates both sides every run - minutes on a million files - so nightly, not every 15 min.
- "Thin circuit" is carrier vocabulary for one number in both directions; translate every "bandwidth" claim
  to "upload, sustained, overnight, to the Azure region" before agreeing or disagreeing.
- Blob storage never carries NTFS ACLs; permission trimming is rebuilt as ADLS ACLs / group fields on the
  index side, so the pilot copies only an everyone-readable share.

## Support files
- `scripts/Get-ShareInventory.ps1` - read-only per-share inventory JSON (counts, size buckets, age, depth,
  path length, extensions, largest-10, share + NTFS root ACLs). ASCII-only on purpose (PS 5.1 + BOM-less
  UTF-8 mangles em dashes); untested on a live server until first run - paste red text back.
