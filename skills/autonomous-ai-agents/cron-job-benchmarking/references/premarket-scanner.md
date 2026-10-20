# Worked example: Pre-market Day-Trade Scanner (Sep–Oct 2026)

The cron job this skill was derived from. Job id `f915ca253747`, "Pre-market Day-Trade Scanner", weekdays 8:40 ET, delivers to Andrew's Telegram DM, `enabled_toolsets:["web"]`. PAUSED 2026-10-11 at Andrew's request (pause, not delete — rules preserved).

Running scorecard (the durable grading record): `~/.hermes/references/premarket-scanner-benchmark.md` — per-round grades, aggregate stats, prompt v1→v7 history.

## Job design that worked

- Data gathering from named extract targets (stockanalysis.com premarket pages, openinsider.com cluster buys) + news cross-check; "only report numbers actually found in extracted pages — never estimate"; "if a source fails, say which and move on".
- Per-setup output = 2 lines: the mover + catalyst + source name, then "Plan:" with entry trigger / stop / exit using real printed price levels ("no reliable level printed — size down or skip" when missing).
- Delivery `origin` + `attach_to_session:true` so Andrew can reply to any briefing.

## Final ruleset (v7) with evidence — the template for "rules with receipts in the prompt"

- A. Confirmed + FRESH catalyst only (named news source; flow/volume never qualifies; stale news fails — BIRK ran on 2-week-old news and faded). Saves: PDSB −39%, RDHL −34%, GYGY −26% from open.
- B. Gap-down shorts never at open — only on break of premarket low, level stated. 8/8 gap-downs bounced/stalled from open; the one triggered break (CRDO) paid −12%.
- C. Sub-$1 tickers excluded; verify ticker exists before listing.
- D. Prioritize gap-UP longs with hard catalysts (clinical wins, contracts, M&A, dilution removal, beat-and-raise) — every outsized winner: TNON +117%, CHPT +31% from open, SSM +27%.
- E. Gap-size rule: earnings gaps ≤~10% = continuation (4/4 then 7 more); >~12% = already priced, fade risk (7/8 in-sample, 5/7 out-of-sample). Exceptions: shorted-up turnarounds (CHPT); liquid large-caps on risk-on tape (CRM +9.6% post-NVDA-blowout) → "scalp with stop at open".
- F. M&A geometry: at/below all-cash deal value = arb-pin scalp (ACVA pinned $10.42 vs $10.50); ABOVE deal value = never a long (GPRO opened 58% over its $1.14 deal, −22% from open).
- G. Weak-tape gate (2 occurrences: Sep 4 NFP 0/4, Sep 14 risk-off 1/4): both futures red or macro-print morning → scalp-size only, prefer pm-high-breakout entries, zero setups is a valid output.
- H. Stop-width: default stop = open price; never >~5% on thin names (ARMP −12.8% on a wide prior-close stop; stop-at-open would have scratched).

## Grading method

- From the OPEN (the user's realistic fill), via stockanalysis.com/stocks/<t>/history/ rows parsed in an execute_code loop: `| date | O | H | L | C | adj | chg% |`.
- Categories: WIN / WASH (±1–2% from open) / FAIL; report win/total AND win/decided.
- Fade/avoid lists grade as money-saved, not short wins (they went ~26/30 — the scanner's most reliable edge).

## Aggregate results (14 backtest days + live day 1)

- Setups raw 13W/8washes/15F → with final rules ~21W/12W/8F. Fade list ~87%. Rule B avoidance 100% (8/8).
- Live day 1 (Sep 14): ELMT scalp win (+11.4% available, breakout level called exactly), 2 stop-at-open scratches, 1 loss from the wide-stop defect that became Rule H.
