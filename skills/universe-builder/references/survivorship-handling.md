# Survivorship handling

Whether a screen retains delisted names in the lookback window. This
is a backtest-relevance rule, not a cosmetic one. Read this before
shipping any universe to a regression or a Monte Carlo.

## The bias

Survivorship bias is the most common screen error in equity research.
A screen run today against "all current US large-caps" excludes every
firm that was a large-cap historically but went bankrupt, merged, or
delisted. The remaining cohort outperforms the true historical
population because the losers are gone.

A 3M momentum backtest run on a survivorship-biased universe overstates
returns by roughly 2-4% per year on US large-caps, more on small-caps,
catastrophically more on biotech.

## The Massive reference flag

`/v3/reference/tickers` accepts an `active` query parameter:

- `active=true` (default in most clients): only currently-listed
  tickers
- `active=false`: only delisted tickers
- Omit the parameter: both

For a clean universe, the skill walks both populations and keeps the
delisted names that were active at the lookback window's start.

## The rule

When the chain includes a lookback predicate (momentum, multi-day
volume, financials over a TTM window), the skill must:

1. Pull the candidate pool with both `active=true` and `active=false`
   (two paginated calls). Tag each name with its `delisted_utc`
   timestamp when present.
2. For each delisted name, retain it in the candidate pool only if
   `delisted_utc > lookback_start`. Names delisted before the
   lookback window contribute no signal and are dropped.
3. Compute momentum, financials, and other lookback metrics against
   the price history that existed during the window, including any
   final pre-delisting prices.
4. Emit `survivorship.mode = "clean"` in the JSON with
   `delisted_in_window` populated.

When the chain is current-only (no lookback predicate), the skill:

1. Pulls `active=true` only (single paginated call).
2. Emits `survivorship.mode = "clean"` because there's no lookback
   to bias against. `delisted_in_window = 0`.

When the user explicitly opts into a current-snapshot screen
(`--ignore-survivorship` or equivalent), the skill:

1. Pulls `active=true` only.
2. Emits `survivorship.mode = "biased"` with the explanatory note
   `"Current-snapshot only; backtests over this set will overstate
   returns."` The rendering layer prints this verbatim.

## The reference implementation default

The reference CLI defaults to `mode = "clean"`. When no lookback
predicate is in the chain, the `clean` label still applies because
nothing needs to be biased; the bookkeeping cost of pulling delisted
names is wasted but the output is still correct.

When a lookback predicate is in the chain (`--mom-3m-top-quartile`,
`--ocf-yield-min`), the implementation pulls `active=false` and walks
the delisted set as described.

## Performance notes

The delisted set is small (a few thousand names per year of
delistings) compared to the active set (~12,000). On a paid key, the
extra paginated call is sub-second. On free Basic, it's one of the
5/min budget; the implementation pulls the active set first and the
delisted set second.

If the chain uses a curated seed list (Tier B free-tier mode), the
delisted set isn't reachable; the skill emits `mode = "biased"` with
the explanatory note `"Curated seed list is current-only; re-run on
the full reference pool to handle survivorship."` This is one of
several reasons Tier B is the demo, not the production configuration.

## Spinoffs, mergers, ticker changes

A name that changed ticker (e.g. FB → META) is technically not a
delisting; the underlying entity persists. Massive's reference
endpoint reflects the change but doesn't link the old to the new.
For most screens this doesn't matter because the lookback uses the
current ticker's price history (which Massive backfills with the
spliced data on the new symbol).

For exact historical accuracy across ticker changes, the
corp-actions-reconciler skill handles the bookkeeping; this skill
doesn't attempt to link old/new tickers in v1. Document the gap if
the user is running a multi-year screen.

## The output line

The rendered table prints exactly one survivorship line at the
bottom:

```
Survivorship: clean. Delisted names retained for the lookback window.
```

Or, when `mode == "biased"`:

```
Survivorship: biased. Current-snapshot only; backtests over this set will overstate returns.
```

Or, when `mode == "unknown"`:

```
Survivorship: unknown. Candidate source did not carry an active flag.
```

The line is short by design. A reader running a daily screen doesn't
need a paragraph; one line is enough to know whether to trust the
output for backtest purposes.

## Why this gets its own reference

Most retail screeners and dashboards (Finviz, Stock Rover, even
Bloomberg's basic screener) don't expose survivorship handling. The
skill flags it because:

1. The use case here is "starting universe for a backtest or factor
   regression," which is the case where survivorship matters most.
2. The Massive endpoint supports `active=false`, so getting it right
   costs nothing once you know the pattern.
3. A senior quant reading the rendered output expects to see the
   survivorship status spelled out. Without it, they have to ask;
   asking erodes trust in the screen.

If the skill grows into screens that don't need a lookback (e.g.
"current portfolio by dividend yield"), the line still renders but
the mode is trivially `clean`. The bookkeeping pays for itself by
making the audit explicit.
