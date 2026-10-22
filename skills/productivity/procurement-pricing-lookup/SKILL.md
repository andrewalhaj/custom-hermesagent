---
name: procurement-pricing-lookup
description: "Use when pricing cloud services or picking hardware to buy"
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [pricing, procurement, hardware, azure, amazon, cloud-cost]
    created_by: agent
load_when:
  - "what would pricing look like for <cloud service>"
  - "best <laptop/Mac/server/GPU> for our work"
  - "provide amazon link / is this listing fine"
  - "compare tiers / reserved vs pay-as-you-go"
---

# Procurement & pricing lookup

## When to Use
Andrew asks what a cloud service costs, which hardware to buy, or whether a specific retail
listing is the right one. Covers cloud list prices (Azure/AWS/etc.), vendor hardware specs,
and retail (Amazon) listing verification.

## Always-on rules
- **Live source, cited, dated.** Pull the vendor's canonical page with `web_extract` (pricing
  page, tech-specs page) and say "checked today" with the link. Never quote prices from memory;
  list prices move and his decisions are dollar-weighted.
- **Canonical URL beats search for prices.** Go straight to the vendor's pricing/spec page with
  `web_extract` (you know the URL); use `browser_exec` for retail. If `web_search` ever returns
  empty, don't retry it — fall back as above and triage later via hermes-maintenance
  `references/web-stack-troubleshooting.md`.
- **A recalled host spec is not a spec.** Before recommending a fleet host for a workload ("run it
  on the VPS, it has 32 GB"), `ssh <host> 'free -g; nproc; df -h /'` — recalled memory and even
  the topology golden file have carried wrong RAM figures; the user will ask "are you sure?".
- **Workload first, then the part.** State the workload you're sizing for in one line (what
  he actually runs), then the pick. He has off-box compute (Studio for inference); a laptop is
  a terminal + VM + dev box unless he says otherwise.
- **Shape: numbers → one pick → skip list → one question.** Bulleted price/spec lines, a
  bold single recommendation with its price, what to skip and why, then at most ONE
  clarifying question that would change the answer (personal vs work purchase, etc.). No tables
  (Telegram).
- **Memory is the non-upgradeable axis on Apple/laptops** — prefer more RAM over faster
  silicon at equal price when the workload is VMs + containers.
- **"Is this fine?" on a listing = verify, then yes/no with the one listing to compare.** Pull
  the ASIN's title/price/seller live; answer whether it matches the recommended spec, name the
  concrete tradeoff vs the pick, and give exactly one alternative link. Don't re-litigate the
  whole lineup.

## Procedure — cloud service pricing
1. `web_extract` the vendor pricing page (raise `char_limit` ~20k; the extract flattens region/
   redundancy filter tabs into consecutive tables — the first table is the default filter,
   usually LRS/first region; label which one you're quoting).
2. Decompose into the billing meters (storage/capacity, operations, egress, retrieval) —
   customers get surprised by the non-capacity meters.
3. Call out lock-ins: minimum retention / early-deletion penalties, rehydration latency,
   reserved-capacity commit sizes and break-even.
4. Give a per-unit rule of thumb ("$/TB/month, LRS, capacity only") and a one-line
   reserved-vs-PAYG break-even.
5. Offer to size it against his actual volume/churn.

## Procedure — hardware recommendation
1. `web_extract` the vendor spec page (e.g. `apple.com/<product>/specs/`) for chip tiers,
   memory bandwidth, max RAM, ports/display limits, and base prices.
2. Tier the lineup in 3–4 bullets (bandwidth, RAM ceiling, I/O, price band).
3. Pick one config for the stated workload; justify each axis (RAM, bandwidth, ports) in one
   clause tied to what he runs.
4. Skip list with the mechanism ("153 GB/s halves local-LLM tok/s", "TB4 caps at 2 displays").
5. One question that changes the answer (who pays / which stack it touches).

## Procedure — retail listing (Amazon)
See `references/amazon-listing-check.md` for the browser recipe. Key rules:
- Enter via the homepage search box, not a hand-built `/s?k=` URL (bot wall).
- Read seller: Amazon-stocked variants are usually base configs; **custom (CTO) configs are
  third-party sellers only** — compare to the vendor-direct price before calling it a deal.
- Product pages can bot-wall on repeat visits; the search-results card already carries
  title, price and ASIN — capture it once and answer from that.
