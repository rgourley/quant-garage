# Survivorship handling

This skill's survivorship rule is stricter than `universe-builder`'s
because the dataset is consumed by a backtest, not a screen. A
survivorship-biased screen produces a watchlist with too few names; a
survivorship-biased backtest dataset produces a strategy that backtests
brilliantly and lives miserably.

Read this before shipping any dataset to a downstream model.

## The bias, restated

A 4-year backtest run against "today's top 100 by market cap" silently
excludes every name that WAS a top-100 in 2022 but failed before 2026:

- FRC (First Republic): top-100 financial through Q1 2023, collapsed
  May 2023
- SVB (Silicon Valley Bank): top-100 regional bank, collapsed March 2023
- SI (Signature Bank): top-100 crypto-adjacent bank, collapsed
  March 2023
- BBBY (Bed Bath & Beyond): mid-cap, but a typical "current top-1000"
  seed for a small-cap strategy would have excluded the most
  educational delisting of 2023

A mean-reversion or oversold-rebound strategy that includes these
names in 2023 buys their pre-collapse dip. The "current top-N" universe
excludes them entirely, so the backtest never has the chance to lose
money on them. The resulting performance overstates the true historical
result by 2-4% per year on US large-caps, more on small-caps,
catastrophically more on biotech.

## What "survivorship clean" actually means

Three properties simultaneously:

1. **Delisted-in-window names are retained.** Their rows in the
   parquet end on `delisted_utc`, not at the window edge. The backtest
   sees their final pre-delisting trades.
2. **The universe seed is reconstructed per period** OR labeled
   biased. For a true point-in-time backtest, the top-100 is
   reconstructed monthly from `/v3/reference/tickers?date=`. The v1
   skill emits "current top-N" with the bias labeled in the manifest;
   the consumer chooses whether that's acceptable for their window.
3. **No backfilled prices on names that didn't exist.** ARM IPO'd
   2023-09-14; a top-100 backtest from 2022-06 should show 188
   missing days at the start of the window, not extrapolated zero or
   first-print imputation.

The skill emits all three properties as schema fields the consumer
can verify.

## The Massive reference flag

`/v3/reference/tickers` accepts an `active` query parameter:

- `active=true` (default in most clients): only currently-listed
  tickers
- `active=false`: only delisted tickers
- Omit the parameter: both

For a clean universe, the skill walks both populations and keeps any
delisted names that meet the rank threshold AND have `delisted_utc`
inside the window. Names delisted before the window start contribute
no signal and are dropped.

## When this matters more, when less

**Matters more (use clean mode, expand the seed):**
- Window touches 2020-2023 (the most active period for delistings
  this decade: COVID-era SPAC fails, 2022 rate-shock failures,
  2023 regional bank collapses)
- Small-cap or biotech universe (biotech delistings dwarf large-cap)
- Long window (any 3y+ window crossing a recession-equivalent)

**Matters less (current seed is fine, label it biased):**
- Last 12-18 months only (the current top-N is roughly the historical
  top-N over a year)
- Mega-cap only (top-30 has near-zero delistings in any modern window)
- The universe is the strategy itself (e.g. a top-50 index tracker)

## The rule in this skill

The `--survivorship` flag controls the treatment:

- `--survivorship clean` (default): pull active and delisted, retain
  delisted-in-window names. Emits `survivorship_mode = "clean"`.
- `--survivorship biased`: pull `active=true` only. Emits
  `survivorship_mode = "biased"` with the note "Current-snapshot only;
  pre-2024 backtests over this set will overstate returns."

The default is `clean` because the dataset is consumed by a backtest.

## Spinoffs, mergers, ticker changes

A name that changed ticker (FB → META, GOOG/GOOGL splits and merges)
is technically not a delisting; the entity persists. Massive's
reference endpoint reflects the change but doesn't link the old to
the new. The skill flags ticker changes as `edge_cases[type =
"ticker_change"]` when both the old and new ticker appear in the
universe seed or when the corp-actions feed records a ticker change
inside the window.

For exact historical accuracy across ticker changes, the
`corp-actions-reconciler` skill handles the bookkeeping. This skill
detects and flags; it does NOT merge the price histories of the
pre- and post-change tickers. Document the gap in the manifest if the
user is running a multi-year backtest that touches a known change
(FB/META 2022-06-09, GOOG/GOOGL 2014, etc.).

## What gets emitted

The schema carries the bias status explicitly:

```json
"universe_definition": {
  "label": "top 100 by current market cap",
  "seed": "top100",
  "size": 100,
  "type_filter": "CS",
  "survivorship_mode": "biased",
  "survivorship_note": "Current top-N seed; window has 0 delistings so the bias is academic. For pre-2024 windows, re-run with --survivorship clean and a wider seed (top500 or top1000)."
}
```

And `universe_stats.delisted_during_window_count` is the running
audit: a top-100 window with `delisted_during_window_count = 0` is
truly clean OR truly forward-looking biased (the test is whether
the universe seed was reconstructed per period). The note discloses
which.

## Why this gets its own reference

Three reasons:

1. Survivorship is the #1 source of overstated backtest returns in the
   industry and the #1 reason "this strategy looked great on paper"
   strategies blow up live.
2. The Massive `active=false` flag makes the fix essentially free; not
   using it is a self-inflicted wound.
3. A quant reading the manifest expects to see the survivorship status
   spelled out at the top of the take. Without it, they ask; asking
   erodes trust in the dataset.

If the consumer is a paper-trading bot rather than a research
backtest, the bias matters less (the bot trades forward, not against
history). The skill defaults to `clean` anyway because the cost is
minimal and the audit value is meaningful.
