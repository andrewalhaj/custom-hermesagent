# MSP managed-services contract — clause map and how to read an objection

Use when the MSP objects to an in-house change ("bandwidth", "security", "we recommend against"), when the
user asks whether he is *allowed* to do something on managed servers, or before any renewal redline.

## 1. Get the governing text, in this order
1. **The signed Quote / Services Statement** — it overrides the MSA where they conflict, and may attach a
   dated MSA copy or incorporate one by URL. Find the "subject to the Master Services Agreement …" line first.
2. **The vendor's public MSA** — `site:<vendor>.com "Services Agreement"` usually finds one PDF under
   `wp-content/uploads/`. Download it (`curl -o`), `pdftotext`, and grep the clause types below yourself —
   a subagent's summary of contract language is a claim; quote the clause verbatim in any reply.
3. The customer's internal brief paraphrases clauses ("they must approve changes"). Paraphrase ≠ wording:
   "strongly recommend you refrain unless we authorize" is a recommendation with a billing consequence,
   not a prohibition. Say which it is.

## 2. Clause types to grep for (typical SMB-MSP MSA shape)
| grep | What it usually says | What it means for the customer |
|---|---|---|
| `Modif` / `install` / `authoriz` | Refrain from modifying the Environment unless authorized; remediation of issues *caused by* your change is out of scope | You may change your own servers; you pay to fix what you break. Send a written **notice** (ticket), not a request |
| `Co-Manag` | Where the customer designates other vendors *or personnel*, MSP yields to the Co-Managed Provider's determination and flags disagreement | In-house IT **is** a co-managed provider. MSP objections are advisory. Get the designation in writing from the exec sponsor (one email) so the clause unambiguously applies |
| `Advice` | MSP may advise (replace hardware etc.); not following = remediation out of scope, possible termination | Turns a hardware quote into a contractual lever — redline at renewal |
| `proprietary` / `shall not be disclosed` | Configurations the MSP custom-creates are theirs and need not be disclosed to the customer | Lock-in: Veeam jobs, firewall rules, runbooks. Redline #1: all config/documentation of customer systems belongs to the customer. Meanwhile export the documentation platform (IT Glue etc.) the customer already has access to, once, transparently, and store the plaintext-password archive in the customer's own vault |
| `Transition` / `passwords` / `log files` | Credentials, logs, admin info are "transition services": hourly, only after all fees paid | Keys held hostage in a dispute. Redline: admin credentials are the customer's at all times |
| `pass through` / `Pass Through Increases` | Third-party price rises passed through, excluded from any fee cap | Cap it, or notice + right to drop the service |
| `solicit` | Mutual non-solicit, term + 1 yr, liquidated damages (≥ $100K typical) | Usually livable; note it |
| `Termination Fee` / `without cause` | No early exit; fee = all remaining amounts; per-seat (NCE) licences non-cancellable | Know the exit cost before any renewal posture |
| `Minimum Requirements` | Customer must maintain MSP-set minimums | Pairs with Advice |
| `exclusiv` / `procure` / `sole` | (usually absent) | If absent, split sourcing (hardware from MSP, licences from a CSP) breaks nothing |

## 3. Translate an objection into a number before arguing
"Bandwidth" / "the circuit is thin" hides three different claims — ask which:
1. **Architecture misread** — MSP pictures users working from cloud storage over the WAN. If the design is a
   one-way mirror for search/indexing (server stays source of truth), say so; the objection evaporates.
2. **Thin upload** — real, if measured. Ask for: ISP contract line (carrier, plan, up/down, DIA vs best-effort),
   30-day WAN interface graph from the firewall, nightly backup job history (GB/night). Measure from the
   server: speedtest upload off-hours (screen), then `azcopy bench` to the real region (proof). See skill
   `azure-storage-migration` for the hours formula.
3. **Non-technical** — revenue, support exposure, or a competing quote. Diagnose from the services agreement:
   per-user billing means a storage mirror moves nothing on their invoice; per-server/per-GB lines mean it does.
   An MSP mid-hardware-quote has a reason to keep the architecture simple and theirs.
A fast, complete answer to the three requests says the concern is real; "we recommend on-prem" without numbers says it is #1 or #3.

## 4. Vendor homework (delegate, then verify)
Delegate a cited profile: entity/rebrand chain, ownership, exec roster and the counterpart's *actual* title and
reporting line (division VP ≠ company head), size (MSP 501/CRN tiers), other business lines that overlap the
customer's projects (a software-dev arm will quote the app you are building), cloud-migration case studies
(proves "we don't do cloud" is not their position), reviews/BBB, and the public MSA. Verify the MSA clauses
from the PDF yourself before repeating them. Never put the customer's name in the delegated prompt.

## 5. Notice vs meeting
File a ticket for the record (what, footprint on managed hosts, owner = in-house IT as co-managed provider,
accepted out-of-scope remediation, ask = exclusions/allow-lists/concerns in writing by date, rollback), then
offer the counterpart a short call. A meeting yields "had concerns"; a ticket yields quotable objections. Keep
the renewal out of operational tickets — the tickets become its exhibits.

## 6. Why a competent MSP quotes under-specced hardware
Price-fitting: the number is set first and the config worked backwards — expensive, visible lines (chassis,
Datacenter, ProSupport) are right; quiet lines (RAM, drives, caddies, 16-core licence on 24-core CPUs) are
cut. Plus: load never measured (no vCenter utilisation), consumer-drive + aftermarket-caddy trick, configurator
defaults, and hourly labour that profits from later fixes. None of these needs to be argued; a change request
("quote 512 GB/host, certified PLP drives, 24-core volume + SA, name the replaced hosts") makes the why moot.
