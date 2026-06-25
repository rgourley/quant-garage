# Event class definitions and resolvers

Three event classes ship in v1. Each has a resolver that converts
a `(ticker, query)` into one or more concrete event tuples
`(ticker, event_date, event_session, event_metadata)`. Adding a new
class is a clean PR: write the resolver, register it in the dispatch
table, and the rendering and abnormal-returns code picks it up.

## Class: `earnings`

**Primary source (Tier A):** Benzinga earnings endpoint.

```
GET /benzinga/v1/earnings?ticker={T}&limit=20&order=desc&sort=date&date.lte={cutoff}
```

The response includes:
- `date`: calendar date of press release
- `time`: HH:MM:SS in ET (`16:30:00` is AMC, `07:30:00` is BMO)
- `fiscal_period`, `fiscal_year`: company fiscal calendar (NVDA's
  Q1 FY2027 prints in calendar May 2026; do not infer fiscal labels
  from calendar)
- `estimated_eps`, `previous_eps`, `previous_revenue`
- `eps_surprise_percent`: surprise % as a DECIMAL (0.05 means +5%)
- `date_status`: `confirmed` for past events, `projected` for upcoming

The resolver filters out `date_status: projected` for historical
event studies and uses `date` + `time` to set `event_session`:

| `time` ET | session |
|---|---|
| before 09:30 | `BMO` |
| 16:00 or later | `AMC` |
| 09:30 to 16:00 | `DMH` |
| missing | `unknown` |

NVDA prints at 16:20 ET (AMC), AAPL at 16:30 ET (AMC), MSFT at 16:05
ET (AMC), GOOGL at 16:03 ET (AMC), META at 16:01-16:05 ET (AMC).
JPM/BAC/GS print at 06:00-07:00 ET (BMO). The skill reads `time` per
event rather than hardcoding.

**Fallback source (Tier B):** SEC EDGAR 8-K filings filtered to
item 2.02.

```
GET https://data.sec.gov/submissions/CIK{cik_padded}.json
```

Filter `recent.form == "8-K"` and `"2.02" in recent.items[i]`. Use
`acceptanceDateTime` (UTC) for date+session, since `filingDate` is the
calendar date the SEC received the filing (usually same as press
release, but can lag).

Tier B has no surprise %, so `event_metadata.surprise_eps_pct` is
null. The cross-section's `surprise_reaction_correlation` block is
omitted.

## Class: `dividend_changes`

**Source:** Massive dividends endpoint.

```
GET /v3/reference/dividends?ticker={T}&limit=20&order=desc&sort=ex_dividend_date
```

The resolver:

1. Pull all `cash_amount` records for the ticker, sorted oldest to
   newest by `ex_dividend_date`.
2. Walk pairwise; flag each ex-date where
   `abs(cash_amount - prior_cash_amount) / prior_cash_amount >= 0.01`
   (1% threshold to filter noise floor).
3. The flagged ex-date is the event date. Session is `BMO` (dividend
   adjustments take effect at the open on ex-date).
4. `event_metadata`: `{prior_amount, new_amount, change_pct,
   change_direction}` where direction is `"hike"` or `"cut"`.

Special-cash dividends (`dividend_type: SC`) are excluded from the
diff calculation; they're one-time and shouldn't reset the comparison
baseline for regular dividends.

**Sample-size caveat:** mature dividend payers change their dividend
4-6 times in a decade. For most names, `prior_n < 8` for the
per-subject t-stat-vs-history. The `underpowered` flag fires often
in this class; that's the honest signal, not a bug.

## Class: `large_volume_spike`

**Source:** the daily aggregates already pulled for the return
computation. Computed in-memory; no extra API call.

The resolver:

1. For each trading day in the window, compute the trailing 30-day
   mean and std of `volume` (excluding the current day).
2. Flag each day where `volume > mean + 3 * std`.
3. Apply a 5-day cooldown: if two flags fall within 5 trading days,
   keep the higher z-score one and drop the other.
4. Session is `DMH` (the spike happens during the trading day; T0 is
   the trading day before so T+1 captures the spike-day return).
5. `event_metadata`: `{volume, trailing_30d_mean, trailing_30d_std,
   z_score}`.

**Why 3σ:** 1σ flags too often (roughly weekly), 2σ catches normal
catalyst days (Fed, CPI, sector ETF rebalance), 3σ isolates the
genuinely anomalous. The skill is intentionally aggressive about the
threshold so the firing rate stays informative.

**Why 5-day cooldown:** a true catalyst often produces a 2-3 day
volume hangover. Treating each consecutive day as a separate event
double-counts the same news.

**Cross-class note:** earnings events and large_volume_spike events
will often overlap (earnings IS a volume spike). The skill doesn't
deduplicate across classes; each class is queried independently. A
quant testing whether volume spikes ex-earnings are predictive
should query both classes and subtract.

## Resolver outputs: the shared tuple

Every resolver returns a list of tuples with this shape:

```python
{
    "ticker": "NVDA",
    "event_date": "2026-05-20",   # calendar date
    "event_session": "AMC",        # BMO | AMC | DMH | unknown
    "event_metadata": {            # class-specific
        # for earnings:
        "fiscal_period": "Q1",
        "fiscal_year": 2027,
        "surprise_eps_pct": 0.0625,
        "estimated_eps": 1.76,
        # for dividend_changes:
        # "prior_amount": 0.04, "new_amount": 0.05, "change_pct": 0.25, "change_direction": "hike",
        # for large_volume_spike:
        # "volume": 980000000, "trailing_30d_mean": 320000000, "z_score": 4.1,
    },
}
```

The downstream pipeline (abnormal returns, t-stats, aggregation)
operates only on this shared shape. The classes are decoupled from
the math.
