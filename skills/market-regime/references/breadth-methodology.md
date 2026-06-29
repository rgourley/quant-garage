# Breadth methodology

The market-regime skill computes breadth from the 11 GICS sector SPDR
ETFs. This is a **proxy**, not the full advance/decline line. The
methodology, the trade-offs, and when to suspect the proxy are
documented here so consumers of the output know what they're getting.

## What the skill computes

For each of the 11 sector ETFs (XLK, XLF, XLE, XLV, XLY, XLP, XLI,
XLB, XLU, XLRE, XLC):

1. Pull daily aggregates over the lookback window
2. Compute the 50-day SMA and 200-day SMA from those closes
3. Check whether the latest close is above each SMA

Then aggregate:

- `n_above_sma_50` and `pct_above_sma_50`
- `n_above_sma_200` and `pct_above_sma_200`
- A `read` label: broad / mixed / narrow / thin

## Why a proxy

The "correct" breadth metric is the advance/decline line on a real
US equity universe: for every name in (say) the S&P 500 or the
Russell 3000, count how many are above their own 50-day SMA. That's
2000+ daily-aggs calls per run, plus universe maintenance.

The sector-ETF proxy collapses that into 11 ETFs (already pulled for
the sector-leadership block). The marginal cost is zero — the data
is already in memory. The output captures the same risk-on / risk-off
story 90% of the time because the 11 sectors span the whole market
cap-weighted.

## What the proxy misses

The proxy is good enough for a regime read but it does miss things:

- **Breadth divergence at index tops.** Classic late-cycle pattern:
  the S&P keeps making highs on a narrow set of mega-caps, while the
  median name has already broken its 50-day. The 11-ETF proxy doesn't
  catch this if all 11 sectors are still above their 50-day on a
  market-cap-weighted basis. The full A/D line would show breadth
  rolling over.

- **Idiosyncratic single-name moves.** If three mega-caps in XLK
  break down but XLK still holds its 50-day, the proxy reports
  "sector above 50-day". The full A/D line on XLK constituents would
  show the divergence.

- **Equal-weight vs cap-weight divergence.** The sector ETFs are
  cap-weighted. When the equal-weight S&P (RSP) underperforms the
  cap-weighted S&P (SPY), the proxy understates breadth thinning.

## When to suspect the proxy

Override the proxy with a real A/D line if any of these are true:

- The skill returns `risk_on` but a few mega-caps (the cap-weight
  drivers) appear to be solely responsible for the index level. Pull
  RSP vs SPY 20-day return to sanity-check.
- The skill returns `risk_on` but new highs are running far below new
  lows on the broader tape. Check `/v3/snapshot/locale/us/markets/stocks/gainers`
  and `/losers` count.
- You're late-cycle and the question is whether the rally is
  narrowing. Run a real A/D from `universe-builder` for that pass.

## The upgrade path

The cleanest extension is to swap the sector loop for a universe
scan. Pseudocode:

```python
from lib.quant_garage import build_universe

universe = build_universe(size=500)  # top-500 by market cap
# Pull daily aggs per name (already a flat-files workflow in
# universe-builder; ~1 file per trading day, parallelized)
above_50 = 0
above_200 = 0
for name in universe:
    arr = closes(get_aggs(name.ticker))
    if arr[-1] > sma(arr, 50)[-1]: above_50 += 1
    if arr[-1] > sma(arr, 200)[-1]: above_200 += 1
```

The output schema already accommodates this — `breadth.method` would
flip from `sector_etf_proxy` to `universe_advance_decline` and
`n_sector_etfs` becomes `n_constituents`. Downstream consumers can
key off `method` to know which they're reading.

## Why the proxy is good enough for v1

The composite regime label is a daily morning-briefing summary, not a
trade signal. The full A/D refinement matters for late-cycle
divergence analysis and for technical-analyst-grade breadth work, not
for the question "is today's tape risk-on or risk-off." For the v1
question the proxy holds up.

The caveat is surfaced in every run so a reader who needs the tighter
read knows they're getting the proxy version. That's the contract.
