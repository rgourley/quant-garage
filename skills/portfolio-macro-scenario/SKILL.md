---
name: portfolio-macro-scenario
description: Prescriptive macro scenario analysis on a current book. Given a position book (CSV of ticker,shares) and a scenario stated as flags (--rates-bp, --dxy-pct, --oil-pct, --gld-pct), it regresses each position's daily returns on four macro factor ETFs (TLT for rates, UUP for the dollar/DXY, USO for oil, GLD for gold), translates the scenario into factor return shocks, and reports the expected per-position and book-level P&L with a rough 90% band, plus the dominant position and factor contributors. Use when the question is forward-looking and conditional: "what happens to my book if rates keep rising / the dollar keeps rallying / oil spikes / gold falls." This is the prescriptive counterpart to risk-report and portfolio-review, which are descriptive of the past. Runs on any stocks tier (Free Basic works with --sleep 13).
---

# portfolio-macro-scenario

risk-report and portfolio-review tell you what the book's risk has BEEN:
its realized vol, beta, VaR, drawdown. portfolio-macro-scenario tells you
what happens NEXT under a macro scenario the operator names. You hand it a
book and a scenario ("+50bp rates, +2% dollar"), and it returns the
expected P&L on each position and on the book as a whole, with a rough
confidence band and a ranked list of which positions and which factors are
doing the damage (or the lifting).

It is not a forecast of the scenario. It is a conditional read: IF these
factor moves happen, here is what your book does, grounded in each name's
historical sensitivity to real macro ETF prices, so an LLM does not have
to guess how any individual position responds to rates or the dollar.

## When to invoke

- The question is forward-looking and conditional: "what happens to my
  book if rates keep rising", "if the dollar keeps rallying", "if oil
  spikes to $100", "if gold sells off 5%"
- Stress-testing a book against a macro view before putting on or lifting
  a hedge
- The user says "scenario", "what if", "shock my book", "rate sensitivity
  of my portfolio", "dollar exposure of my book", "macro stress test"

For the descriptive, past-looking risk picture (realized vol, beta, VaR,
drawdown, concentration) use [`risk-report`](../risk-report). For what the
cross-asset tape is pricing right now (is the dollar strong, is credit
widening) use [`macro-basket`](../macro-basket). This skill is the bridge:
it takes the macro variables those skills describe and pushes them through
your specific book.

## What you need

- `--book`: a CSV of positions with columns `ticker,shares` (optional
  `cost_basis,as_of_date`), matching
  [`examples/sample-book.csv`](../../examples/sample-book.csv). Position
  value is `shares * latest close`.
- `MASSIVE_API_KEY` exported in the environment.
- Any stocks tier. On Free Basic pass `--sleep 13` so a larger book stays
  under the 5-calls/min cap.

The scenario flags (all default 0, so a flat scenario returns zero P&L):

- `--rates-bp`: parallel rate shock in basis points (`+50` = rates up 50bp)
- `--dxy-pct`: dollar (DXY) shock in percent, applied as the UUP return
- `--oil-pct`: oil shock in percent, applied as the USO return
- `--gld-pct`: gold shock in percent, applied as the GLD return
- `--lookback` (default 252): trading days of returns for the regression

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching
[`output-schema.json`](./output-schema.json). Per-position `betas` (one per
factor), `r_squared`, `residual_std`, `expected_return`, `pnl_usd`, and
per-factor P&L contributions; a `book_pnl` block with the expected P&L, the
expected return, and the `~90%` CI band; `dominant_positions` and
`dominant_factors` ranked by contribution; the composed `take`.

**Layer 2: rendered table** (output mode `table`): a sensitivity table
(position x factor beta with R^2 and per-position P&L), the book P&L with
the CI band, the dominant-contributor lists, and the take. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull daily aggregates** for every position ticker plus the four macro
   factor ETFs (TLT, UUP, USO, GLD) over `max(lookback, 252) * 1.6`
   calendar days, via `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}`.
2. **Align daily returns** by date, per position against the four factors.
3. **Regress** each position's returns on the four factor returns with an
   intercept (numpy `lstsq`), producing four betas, a residual std, and an
   R^2.
4. **Shock the factors**: convert the scenario flags into factor ETF
   returns (the rate shock goes through an assumed TLT effective duration
   of ~17 years). The full conversion and its assumptions live in
   [`references/methodology.md`](./references/methodology.md).
5. **Push through the betas**: expected position return is the sum of
   `beta_factor * factor_shock`; position P&L is `position_value *
   expected_return`; aggregate to the book with a `~90%` band.
6. **Rank contributors** and **compose the take**.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate-limit handling, and the `/v2/aggs` daily endpoint conventions.

## Output mode: table

The core deliverable is a sensitivity grid (position x factor beta) plus
per-position and book P&L. A table carries that comparison cleanly; the
book P&L line, dominant-contributor lists, and take sit above and below it.

## Chains with

- [`risk-report`](../risk-report): the descriptive counterpart. Run
  risk-report for the realized risk picture, then this skill to stress the
  same book against a named macro move. `risk-report` is the declared
  fallback when a scenario cannot be run.
- [`macro-basket`](../macro-basket): reads what the cross-asset tape is
  pricing now (rates, dollar, credit, gold, commodities). Use it to pick a
  realistic scenario, then feed the numbers here.
- [`hedge-suggester`](../hedge-suggester): once this skill surfaces the
  book's dominant factor exposure, hedge-suggester proposes what to put on
  against it.

## Doesn't handle (yet)

- **Non-linear moves.** Shocks are applied linearly through the betas; a
  large move has convexity this does not capture. Queued.
- **Factor collinearity.** The four factors co-move, so individual betas
  can be noisy even when the aggregate fit is good. A ridge or orthogonal
  factor set would firm them up. Queued.
- **Residual correlation.** The CI band assumes independent residuals
  across names, so the true band is wider. Queued.
- **Custom factors.** The factor set is fixed at rates/dollar/oil/gold. A
  configurable factor list (credit, real yields, a sector ETF) is a clean
  extension. Queued.

These are clean PR extensions. The output schema is forward-compatible.
