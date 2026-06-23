# Unusual activity detection

## What "unusual" means

A contract's volume today is meaningful in context. 1,000 contracts on a
TSLA weekly that averages 20,000 is noise. 1,000 contracts on a
back-month strike that averages 30 is a story. The skill ranks every
contract by how anomalous today's activity is, not by raw volume.

## The four metrics

Every qualifying print is scored on four axes. Each axis has a default
threshold for "unusual." A print needs to clear at least two thresholds
to surface in the stream.

### 1. Volume / 30-day average

```
ratio = today_volume / avg_30d_volume
```

Compute `avg_30d_volume` from the contract's own daily aggregates over
the last 30 trading days. If the contract has fewer than 5 days of
history (new weekly, deep OTM that just got attention), use the median
volume of contracts at adjacent strikes on the same expiry as the
benchmark. Note the substitution in the contract's
`avg_source` field if you want to expose it for debugging.

**Default threshold: 3.0x.** Below 3x is normal; 3-10x is interesting;
10x+ is rare and worth attention. Cheddar Flow's default surface
threshold is 5x; FlowAlgo uses 3x. The skill defaults to 3x and lets
the operator tighten.

### 2. Volume / open interest

```
ratio = today_volume / oi_at_session_start
```

OI represents existing positions. Volume above OI means today's trading
is opening new interest (not just opens; could also be a wave of new
opens against existing closes, but on net the position count is growing).
Volume well below OI means the trading is mostly recycling existing
positions (closing, exercising, rolling).

**Default thresholds:**

- `vol/OI > 1.0` → opening (new interest, the more actionable read)
- `vol/OI > 10.0` → clearly new interest, often a coordinated buy
- `vol/OI < 0.5` → closing (positions being unwound)
- between 0.5 and 1.0 → mixed

Why this matters: a 10,000-contract day on a strike with 50,000 OI is
mostly turnover. The same 10,000-contract day on a strike with 200 OI
is a fresh story.

### 3. Premium dollar value

```
premium = volume * avg_trade_price * 100
```

The `* 100` is the OCC contract multiplier (one contract = 100 shares).
Premium is the actual capital at stake. A $50K position on a $5M cap
account is noise; the same $50K is a story on a $50K cap account. The
skill doesn't know the cap, so it surfaces premium in absolute terms and
lets the operator interpret.

**Default threshold: $100,000 minimum.** Below $100K is too small to be
smart money; the skill suppresses these by default. Operators tuning for
small-cap names can drop it to $25K.

### 4. Percentage of chain volume

```
share = contract_volume / sum(volume across all contracts on this underlying)
```

A single contract carrying >5% of the underlying's chain volume is
unusual concentration; it suggests one trader (or a group) is pushing
size into a specific strike rather than spreading across the chain.

**Default threshold: 5%.** Above 5% concentration is the soft signal;
above 15% is the hard signal that one trade is dominating.

## Scoring

Combine the four into a single rank:

```
score = (vol_avg_ratio / 3.0) * 0.40
      + (vol_oi_ratio / 5.0)  * 0.30
      + (premium_usd / 1_000_000) * 0.20
      + (chain_share / 0.05) * 0.10
```

Each component is normalized by its default threshold so a 1.0 in each
means "passing the threshold." The weights are tuned to favor the
volume-vs-avg signal (most predictive in published studies) with OI as a
strong secondary signal and premium as a sanity-check on size.

Rank descending. Emit the top N (default 20) after dedup.

## Filters before scoring

Before scoring, drop contracts where:

- Expiry has already passed (day-of expiry traded after 4 PM ET)
- Implied volatility is null or negative (data error)
- Spot is null (couldn't price the underlying)
- The contract's OCC ticker doesn't parse cleanly

These are data-hygiene drops, not analytical filters.

## Dedup

If two prints on the same `(ticker, expiry, strike, type)` qualify with
different scores, keep the higher score and add a `related_prints` entry
on it pointing to the lower-score print. This usually happens when both
a sweep and a block hit the same contract in the same session; the
higher-magnitude print is the headline and the other becomes context.

## Per-ticker volume thresholds

Some tickers carry orders of magnitude more options volume than others.
SPY's typical day is 4-10M contracts; TSLA is 1-3M; a mid-cap like SOFI
might be 50K. A single 500-contract block is huge on SOFI and invisible
on SPY.

To handle this, the volume thresholds (3x avg, $100K premium) are tuned
per ticker as multiples of a baseline computed from the chain's
median 5-day volume. The defaults work for AAPL/NVDA/TSLA/AMD/SPY and
similar mega-caps; for small-caps or thinly-traded names, raise the
ratio threshold and lower the premium threshold.

This is documented as a v1 simplification: the skill uses one set of
thresholds for the whole watchlist. Adapter logic per ticker is a clean
v2 extension.

## What gets stripped from the surface

Print qualifies but isn't actionable in v1:

- Multi-leg trades (conditions 232-240). The skill flags these but
  excludes from the headline stream because the call+put pair tells a
  different story than either leg alone.
- Late or out-of-sequence prints (conditions 202, 204, 206). These are
  exchange corrections, not flow.
- Canceled trades (conditions 201, 203, 205, 207). Exclude entirely.

Render the stripped count in the JSON's `skipped_tickers`-style field
as `excluded_prints` for transparency. Don't surface them in the rendered
output.
