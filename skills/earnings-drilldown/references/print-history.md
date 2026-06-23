# Print history

## What we measure

The last N quarterly earnings prints, the surprise on each (EPS and
revenue vs consensus), and the stock's next-day reaction. This is the
simplest analysis in the skill but the most relied-on: every analyst
checking a name before the print wants to know how often it beats, how
big the beats run, and how violently the stock reacts.

Default N is 8 quarters (two years). Earlier history is available but
older prints reflect different management, product cycles, and
sometimes different businesses entirely.

## Print date is NOT filing date

Critical distinction the skill must get right. The earnings press
release lands AMC (after market close) or BMO (before market open).
The 10-Q regulatory filing is typically accepted by the SEC days or
weeks AFTER the press release. Reaction windows must use the press
release date, never the 10-Q filing date.

Use Benzinga `/benzinga/v1/earnings` for the press release date+time.
Do not use `/vX/reference/financials` for earnings dates: it returns
10-Q filing dates, which lag the press release by weeks and break
every downstream reaction calculation.

## The numbers

**Surprise**: actual minus consensus, as a percentage of consensus.
Benzinga ships this pre-computed as `eps_surprise_percent`.

```
surprise_eps_pct      = benzinga.eps_surprise_percent / 100
surprise_revenue_pct  = benzinga.revenue_surprise_percent / 100
```

If consensus is unavailable for a print (some smaller names lack
analyst coverage), Benzinga returns null. Report null for that print
and exclude it from averages.

**Beat rate**: count of prints where `surprise_eps_pct > 0`.

```
beats     = sum(1 for print in history if print.surprise_eps_pct > 0)
beat_rate = beats / n_quarters
```

A 7-of-8 beat rate is common for high-quality names (management
sandbags guidance). AAPL has run 8-of-8 most recent windows. A 4-of-8
rate is more typical for companies with genuine forecasting difficulty.

**Average surprise**: mean across all prints with consensus available.

**Largest beat / largest miss**: the extreme prints by surprise size,
useful for context on how violent surprises can be on this name.

**Best / worst stock reaction**: the next-day close-to-close return
following each print, ranked. These are reactions, not abnormal
returns; the SPY adjustment in `post-earnings-drift.md` handles that
properly for the longer window.

```
for each print at date d (from Benzinga):
    if AMC: reaction = (close(d+1) - close(d)) / close(d)
    if BMO: reaction = (close(d) - close(d-1)) / close(d-1)
```

**Beat sign vs reaction sign are NOT the same.** A beat can produce
a negative reaction on soft guidance (AAPL Q3 2025: +10.6% EPS surprise,
−2.5% next-day reaction on services growth deceleration). Always report
both beat rate AND reaction distribution; never substitute reaction
sign for beat sign.

## Caveats

- **Consensus revisions**: the consensus a print was measured against
  can shift between when analysts published and the print date.
  Benzinga reports the consensus as of the print, not the latest
  revision, which is what we want.
- **Stock splits**: split-adjusted prices are standard. Massive's
  daily aggregates are split-adjusted by default; confirm the
  `adjusted=true` flag in the request.
- **GAAP vs adjusted EPS**: Benzinga ships `eps_method` per print
  ("gaap" or "adjusted"). The skill uses adjusted (non-GAAP) where
  available since that's what the market reacts to. If `eps_method`
  switches between prints in the window (mixed-method coverage), flag
  that in the all_prints output so the reader knows comparisons aren't
  perfectly apples-to-apples.

## Edge cases

- **N < 8 prints**: report what's available. Names with <4 prints
  shouldn't run this skill; redirect to a fundamentals-only view.
- **Mid-quarter pre-announces**: if a name pre-announced and the
  print is a confirmation, treat the pre-announce as the "print"
  for reaction-measurement purposes.
- **No revenue surprise**: some names (financials with non-comparable
  rev) report core metrics instead of revenue. Skip the revenue
  surprise field if consensus revenue is null.

## Tier B alternate output

When running at Tier B (no Benzinga, no consensus), beat rate cannot
be computed. Substitute "reaction distribution" instead:

- Replace `beats: 7/8` with `positive_reactions: 3, negative_reactions: 5`
- Drop `avg_surprise_eps_pct` (no consensus to compute against)
- Drop `largest_beat` / `largest_miss` callouts
- Keep `best_reaction` / `worst_reaction` (uses prices only)
- Add `eps_method: "gaap"` to flag that the actuals are GAAP from the
  10-Q/10-K, not adjusted

Tier B `print_history` JSON shape:

```json
{
  "print_history": {
    "tier": "B",
    "n_quarters": 8,
    "positive_reactions": 3,
    "negative_reactions": 5,
    "eps_actuals_method": "gaap",
    "best_reaction": { "period": "Q2 2026", "next_day_return_pct": 0.032 },
    "worst_reaction": { "period": "Q2 2025", "next_day_return_pct": -0.037 },
    "caveats": [
      "EPS actuals are GAAP from 10-Q/10-K; differs from non-GAAP adjusted when one-time charges hit.",
      "No consensus available; beat/miss bucketing unavailable."
    ]
  }
}
```

## Endpoints used

- `GET /benzinga/v1/earnings?ticker={ticker}&limit=20&order=desc&sort=date`:
  canonical source for press release date+time, consensus, actuals,
  surprise %, and `eps_method`. Returns `actual_eps`, `estimated_eps`,
  `eps_surprise_percent`, `actual_revenue`, `estimated_revenue`,
  `revenue_surprise_percent`, `date`, `time`, `fiscal_period`,
  `fiscal_year`, `eps_method`, `importance` per print.
- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`:
  daily closes for reaction calculation.

## What goes in the JSON

```json
{
  "print_history": {
    "n_quarters": 8,
    "beats": 8,
    "avg_surprise_eps_pct": 0.047,
    "avg_surprise_revenue_pct": 0.019,
    "largest_beat": { "period": "Q3 2025", "surprise_pct": 0.106 },
    "largest_miss": { "period": "none in window", "surprise_pct": null },
    "best_reaction": { "period": "Q2 2026", "next_day_return_pct": 0.032 },
    "worst_reaction": { "period": "Q2 2025", "next_day_return_pct": -0.037 },
    "method_mix": { "gaap": 0, "adjusted": 8 }
  }
}
```
