# Data source tiers

Earnings-drilldown can run at three fidelity tiers depending on the
user's Massive plan plus the SEC EDGAR public API. The skill reads the
available data and picks the highest tier it can serve, then flags the
choice in the output JSON as `tier`.

## Tier A: Premium (full fidelity)

**Requires:** Stocks Starter or higher + **Benzinga Earnings** add-on
(~$130/m combined).

**Data sources:**

- `/benzinga/v1/earnings` for press release date + time, consensus EPS,
  consensus revenue, actuals, surprise %, GAAP/adjusted method tag.
- `/v3/snapshot/options/{ticker}` for implied move (full mode only).
- `/v2/aggs/ticker/{ticker}/range/...` for realized moves, PEAD, reactions.

**Available analyses:**

| Analysis | Status |
|---|---|
| Implied vs realized | Full |
| Print history (beat rate, avg surprise) | Full |
| Best/worst reaction | Full |
| PEAD bucketed by beat/miss | Full |
| Peer reaction with surprise-sign bucketing | Full |

**Caveats:** Benzinga is a separate $99/m purchase on top of any
Massive asset-class plan. Consensus revisions during the quarter are
not tracked; the consensus shown is what Benzinga snapshotted at the
print.

## Tier B: Stocks-only + SEC EDGAR (degraded but high-fidelity dates)

**Requires:** Stocks Starter or higher ($29/m). No Benzinga. Combines
Massive Stocks endpoints with the free SEC EDGAR submissions API.

**Data sources:**

- **`https://data.sec.gov/submissions/CIK{cik}.json`** for 8-K filing
  acceptance times. Free, public, no auth (just send a `User-Agent`
  header with your email per SEC fair-use policy). Filter for 8-K
  filings where the `items` field contains `"2.02"` (Results of
  Operations and Financial Condition); these are the earnings releases.
  Acceptance time is the press release time within minutes, not a
  24-48hr lag like a 10-Q would be.
- `/v3/reference/tickers/{ticker}` for `cik` (needed to query EDGAR).
- `/vX/reference/financials` for EPS and revenue actuals.
- `/v3/snapshot/options/{ticker}` for implied move (full mode only).
- `/v2/aggs/ticker/{ticker}/range/...` for realized moves, PEAD, reactions.

**Available analyses:**

| Analysis | Status |
|---|---|
| Implied vs realized | Full (options-side unchanged) |
| Print history (beat rate, avg surprise) | **Degraded**: no consensus available, can't compute beat/miss. Reports "reaction distribution" only. |
| Best/worst reaction | Full |
| PEAD bucketed by **reaction sign** | **Substitute**: bucket by next-day return sign instead of surprise sign. Arguably what a trader cares about anyway (a beat with a sell-off counts as a "miss" for drift purposes). |
| Peer reaction bucketed by **reaction sign** | Substitute (same reasoning) |

**Print date fidelity in Tier B:**

Verified 2026-06-23 against AAPL's last 8 prints: SEC 8-K acceptance
times match the Benzinga press release times to the day in every case.
Acceptance is ~20:30 UTC (16:30 ET) in EDT months, ~21:30 UTC in EST
months, which is literally when the AMC press release crosses the wire.

**Caveats and what's lost vs Tier A:**

- **No "beat rate" number.** Print history shows "reactions: 5 positive
  / 3 negative" instead of "beats: 7/8."
- **No surprise size.** Can't tell users "Q1 2025 was a +8.9% beat."
  Can only say "Q1 2025 reaction was +6.3%."
- **EPS actuals are GAAP, not adjusted.** Massive's `/vX/reference/financials`
  returns GAAP EPS from the 10-Q/10-K. Tier A's Benzinga returns the
  non-GAAP adjusted number that management and analysts focus on. For
  names with frequent one-time charges these diverge meaningfully.
  Example: AAPL Q4 2024 reports as $0.97 GAAP (Tier B) and $1.64
  non-GAAP adjusted (Tier A), a $0.67 per-share gap that's entirely
  the $10B EU State Aid tax charge. Neither is wrong, but a user
  comparing the two side-by-side will assume one is broken. Flag this
  in the rendered note when Tier B is active.
- **Q4 has no quarterly record in `/vX/reference/financials`.** It's
  reported as the `annual` 10-K. To get Q4 EPS: back-calculate
  `Q4 = annual - (Q1 + Q2 + Q3)`. Every annual-reporting US filer
  hits this; document it in the financials helper.
- **Financials `filing_date` is the 10-Q date, NOT the 8-K date.** The
  10-Q is typically filed 1-2 days after the earnings 8-K. When matching
  financials records to 8-K filings, allow a window rather than equality.
  The default window is `0 ≤ gap_days ≤ 3`, which works for AAPL and most
  large-cap regular reporters. NVDA's CY-Q3 2024 10-Q was filed >3 days
  after the 8-K, so a tight window drops that record. Treat the window
  as a tunable constant `FINANCIALS_MATCH_WINDOW_DAYS` (default 3, widen
  to 7 for less-disciplined reporters). When a match fails, the print
  still appears in output with `eps_actual: null`, not dropped.
- **Reaction-sign bucketing has bias.** When markets misjudge a print
  (beat sold off on guidance, miss rallied on cost cuts), reaction-sign
  bucketing puts those in the "wrong" PEAD bucket. The classical
  surprise-sign bucketing avoids this; reaction-sign is the pragmatic
  fallback when consensus is unavailable.
- **Next-print date is projected, not confirmed.** Tier A pulls the
  upcoming print date from Benzinga's earnings calendar. Tier B has
  to project (~91 trading days after the last 8-K). For regular
  quarterly reporters this lands close; for off-cycle reporters
  (some banks, foreign filers) it's sloppy. Render "projected" in the
  output for any future date sourced this way.
- **SEC EDGAR fair-use rules.** Set a real `User-Agent` (per SEC
  guidance: include your name + email). Cap requests at 10/sec. Do
  not abuse it. The skill caches per-ticker EDGAR responses.

**8-K filter detail:**

```python
def is_earnings_8k(filing):
    """Returns True if a filing is an earnings release 8-K."""
    if filing["form"] != "8-K":
        return False
    items = (filing.get("items") or "").split(",")
    return "2.02" in [i.strip() for i in items]
```

Item code reference (relevant ones):
- `2.02` = Results of Operations and Financial Condition (the earnings)
- `9.01` = Financial Statements and Exhibits (always paired with 2.02)
- `5.02` = Executive officer / director changes (not earnings)
- `5.07` = Submission of matters to a vote of security holders
- `8.01` = Other events

Only `2.02` matters for this skill.

The output JSON flags Tier B clearly:

```json
{
  "tier": "B",
  "tier_caveats": [
    "No consensus EPS available; print history shows reaction distribution only.",
    "PEAD bucketed by reaction sign, not surprise sign."
  ],
  ...
}
```

## Tier C: Free Basic (not actively supported)

**Requires:** Free Basic plan ($0). Documented for completeness.

**Why we don't actively support it:** 5 calls/min is too slow. A
single run of this skill needs roughly 10-15 API calls (ticker meta,
financials, aggs, options chain, SPY aggs, peer aggs). On a 5/min
budget, that's a 3-minute floor for the easy case and 10+ minutes if
any peer set requires correlation-based selection.

SEC EDGAR is unlimited regardless, so the date-fetching part stays
free. The bottleneck is the Massive price aggregates and options
snapshot calls. Upgrading to Stocks Starter ($29/m) unlocks unlimited
calls and turns a 10-minute wait into 10 seconds.

## Recommendation

Most users should run Tier B on Stocks Starter ($29/m). The skill is
fully functional, the print dates are accurate, and the reaction-based
bucketing is arguably more honest about what the market actually did.

Upgrade to Tier A ($99/m extra for Benzinga Earnings) when you need:

- Surprise % numbers for catalysts or post-mortems
- Classical beat/miss PEAD bucketing for academic-style research
- Forward earnings calendar (Benzinga has the projected dates;
  EDGAR only has filed-already events)
- True consensus EPS for valuation work

For pure trading workflows, Tier B is enough.
