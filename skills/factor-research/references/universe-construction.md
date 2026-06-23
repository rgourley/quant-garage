# Universe construction

The universe is the set of names the factor analysis runs on. The
choice of universe is itself a research decision and has big effects
on IC and decile spreads.

## Default universe

"Top 500 by current market cap, US common stock, with continuous daily
price history across the window."

Built by:

1. Pull `/v3/reference/tickers?market=stocks&active=true&type=CS` with
   `sort=market_cap&order=desc` and `limit=500`. (If the endpoint
   doesn't sort server-side, paginate and sort client-side.)
2. For each name, check that the daily aggregates file contains a
   record on the window's start month and the window's end month. Drop
   names with gaps wider than 21 trading days inside the window
   (suspends, IPOs mid-window, M&A delists).
3. The resulting set is recorded in the JSON as `universe_definition`
   with `survivorship_mode = biased` and a note explaining why.

## Why this is survivorship-biased

The current top-500 by market cap is, by definition, the names that
made it to today. NVDA is in the top 5 today because of a 5-year
+1000% run; it was not a top-100 name in mid-2021. Running a momentum
backtest on the current top-500 implicitly conditions on "names that
later became top-500," which is a form of look-ahead.

The size of the bias depends on the factor:

- **Momentum:** large positive bias. The current top-500 includes
  every name that won, none of the names that lost. Momentum will
  look much stronger than it actually was in real-time.
- **Value:** smaller bias, sometimes negative. Some cheap names rallied
  into the top-500 (financials post-2022); some stayed cheap (energy
  pre-2022). Hard to sign a priori.
- **Quality:** smaller positive bias. High-ROE names are more likely
  to survive but the effect is weaker than for momentum.
- **Low-vol:** ambiguous bias. Low-vol names are less likely to blow
  up but the survivorship effect interacts with the regime.

Documented in the SKILL.md and the rendered take. The fix is to
reconstruct the universe per period using `/v3/reference/tickers`
with a historical `date=` parameter; queued for a future PR.

## Continuous-trading filter

Names that don't have a price on the window start, or have a gap
wider than 21 trading days inside the window, get dropped. This
removes IPOs partway through (DDOG IPO 2019 wouldn't have 5-year
history in a 2021-2026 run), M&A delists, and SPAC un-mergers.

The dropped count is recorded in the JSON
(`universe_definition.size_dropped_for_gaps`) so the consumer knows
the actual size used.

## Alternative universes worth supporting

The skill defaults to top-500 but the implementation reads the
universe from a config so other choices are a one-flag change:

- **Russell 3000 proxy:** "active US common stock with market cap >
  $300M." Much wider universe, slower run, more cross-sectional
  dispersion (good for IC stability), more delists (bad for
  survivorship without point-in-time reconstruction).
- **Sector-restricted:** "top 100 by market cap within Healthcare."
  Useful for sector specialists; the factor IC magnitudes are
  smaller because of less dispersion but the take is sector-relevant.
- **Equal-weight S&P 500:** the actual S&P 500 constituents (point-
  in-time-correct if you have the historical membership). Out of
  scope for v1 because Massive doesn't expose index membership
  history.

The current implementation uses the top-500-by-current-mcap
construction. Re-running on a wider universe is a `--candidate-size`
flag away.

## Why not start from a fixed index?

For a true point-in-time backtest, the right universe is the index
membership as of each rebalance date (the S&P 500 in June 2021 had
different names than the S&P 500 in June 2026). Massive doesn't
expose historical index membership, and reconstructing it from
constituent change announcements is a separate workflow that belongs
to a dedicated `index-membership` skill, not this one.

Best practice for a real research workflow:

1. Use this skill for a directional read (which factor is working
   in the current regime)
2. Validate the winners against a longer, survivorship-clean dataset
   from a vendor that exposes point-in-time index membership
   (CRSP/Compustat, FactSet Symbology) before building a real sleeve

The skill labels its survivorship explicitly so the consumer doesn't
mistake step 1 for step 2.

## Minimum universe size

For monthly cross-sectional ranking and decile cuts, the universe
needs to be at least ~50 names per month. Top 500 is well over.
Smaller universes are supported (`--candidate-size 200`) but the
decile boundaries become unstable (50 names per decile is the
threshold; below that, deciles become quintiles or quartiles in
practice).

## Look-ahead from delisted names

Real backtest hygiene requires retaining delisted names in the
historical universe (otherwise you systematically miss losers). v1
of this skill drops them via the continuous-trading filter, which is
the simpler implementation but understates downside. Documented as
a known limitation.
