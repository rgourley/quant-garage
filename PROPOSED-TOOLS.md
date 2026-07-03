# Proposed Tools

A design backlog for extending Quant Garage. Two tiers: nine deeper
quant tools that fill real gaps in the current 20, and a new eight-tool
retail-trader tier that trades analyst depth for clarity and decision
support.

Nothing here is built yet. Each entry is a spec: the gap it fills, what
it takes in, what it returns, the methodology that would live in its
`references/` folder, and the plan tier it needs. When one gets built it
follows the standard skill shape (`SKILL.md`, `requires.yml`,
`output-schema.json`, `references/rendering.md` + methodology, one
working example) and must pass `npm run audit:requires`.

No em-dashes in shipped output. Take plus evidence, not data dumps.
Same methodology bar as the existing skills: percentile, z-score,
sample size, base rate, honest caveat.

---

## Part 1: More quant

The existing 20 lean toward earnings, valuation, technicals, screening,
risk, and execution. The gaps below are signals and structures a
serious desk expects that the set doesn't cover yet.

### `pairs-cointegration`

**Gap:** no mean-reversion / statistical-arb tool anywhere in the set.

**Does:** takes a basket (or a sector), tests every pair for
cointegration, and ranks the tradeable ones by spread z-score with a
mean-reversion half-life. The output is "these two names are
statistically tethered, the spread is 2.3 sigma wide, and it has
historically closed half the gap in 6 days."

**Inputs:** `--basket` (comma-separated tickers) or `--sector`,
`--lookback-days` (default 252), `--min-halflife` / `--max-halflife`
filters.

**Returns:** per-pair cointegration test statistic and p-value, current
spread z-score, hedge ratio (beta), half-life of mean reversion,
in-sample vs out-of-sample stability flag, and a ranked table of the
widest tradeable spreads.

**Methodology (references/):** Engle-Granger two-step and/or Johansen
test, ADF on the residual, Ornstein-Uhlenbeck half-life estimate,
rolling-window stability check to catch spurious cointegration, and an
honest caveat that cointegration breaks in regime shifts.

**Output mode:** table. **Plan:** Stocks Starter.

### `short-interest-monitor`

**Gap:** zero coverage of the short side. The set has no view on
positioning against a name.

**Does:** for a ticker or watchlist, reports short interest as a
percent of float, days-to-cover, the trend across the last several
settlement dates, and a squeeze-risk flag when high short interest
meets rising price and thinning liquidity.

**Inputs:** `--ticker` or `--watchlist`, `--settlements` (how many
back to trend, default 6).

**Returns:** short interest %, float, days-to-cover, settlement-over-
settlement delta, borrow-rate context where available, and a
squeeze-risk label (low / building / elevated) with the reasons that
drove it.

**Methodology (references/):** days-to-cover math, percentile of short
interest vs the name's own trailing history, the interaction rule for
the squeeze flag (short % high AND price rising AND ADV falling), and a
caveat that bi-monthly settlement data lags reality.

**Output mode:** exception-report (surface only the crowded names).
**Plan:** Stocks Starter, plus a short-interest data entitlement.

### `insider-flow`

**Gap:** no fundamental / insider signal at all.

**Does:** aggregates Form 4 filings for a name, separates routine
option-exercise sales from open-market conviction buys, detects
cluster buying (multiple insiders buying in a short window), and scores
net insider sentiment against the price reaction.

**Inputs:** `--ticker`, `--lookback-days` (default 180).

**Returns:** per-filing rows (insider, role, transaction code, shares,
value), aggregated net buy/sell, cluster-buy detection, and a sentiment
read with the price-vs-signal divergence flag.

**Methodology (references/):** Form 4 transaction-code taxonomy (P vs S
vs A/M exercises), why cluster buys by multiple officers matter more
than one director, dollar-weighting vs share-weighting, and the base
rate caveat that insider sells are noisy (diversification, taxes) while
buys are the cleaner signal.

**Output mode:** stream (per-filing blocks with a sentiment tag).
**Plan:** Stocks Starter, plus an SEC/insider data source.

### `institutional-ownership-delta`

**Gap:** no institutional-flow lens (13F quarter-over-quarter changes).

**Does:** compares the two most recent 13F snapshots for a name and
surfaces who added, who trimmed, who initiated, and who exited, with
the position sizes and the concentration of the holder base.

**Inputs:** `--ticker`, optional `--top-n` holders to show (default 20).

**Returns:** new positions, exits, top adds/trims by share delta and by
dollar value, holder-base Herfindahl (concentrated vs broad), and a
one-line read on whether smart money is rotating in or out.

**Methodology (references/):** the 45-day 13F reporting lag and what it
does and doesn't tell you, share-delta vs dollar-delta, why an exit
from a concentrated holder matters more than a trim from an index fund,
and the survivorship/stale-data caveats.

**Output mode:** table. **Plan:** Stocks Starter, plus a 13F data
source.

### `vol-surface`

**Gap:** `options-flow` is flow-only. Nothing describes the options
market's structure (term structure and skew).

**Does:** for a name, builds the implied-vol term structure and the
skew at each expiry, compares implied to realized (richness), and flags
when the surface is pricing an unusual event (steep front-month, fat
put skew).

**Inputs:** `--ticker`, `--expiries` (how many to include).

**Returns:** ATM IV per expiry (term structure), 25-delta put/call skew
per expiry, IV vs trailing realized (rich/cheap), a front-month event
premium flag, and the ATM straddle-implied move for the nearest expiry.

**Methodology (references/):** IV interpolation to constant delta,
term-structure slope interpretation (backwardation = event risk),
put-skew as crash pricing, the realized-vol estimator used for the
richness comparison, and a caveat on thin/illiquid option chains.

**Output mode:** note. **Plan:** Options Developer add-on.

### `earnings-quality`

**Gap:** `valuation-sanity-check` prices a name but nothing screens the
quality of the earnings underneath it.

**Does:** runs a forensic pass on reported financials: accruals ratio,
cash-flow-vs-net-income divergence, days-sales-outstanding creep,
margin sustainability, and other classic red flags, rolled into a
quality score.

**Inputs:** `--ticker`, optional `--peers` for a relative read.

**Returns:** accruals ratio and its percentile vs peers, CFO-vs-NI gap,
receivables/inventory growth vs revenue growth, a red-flag list, and a
composite earnings-quality score with the drivers spelled out.

**Methodology (references/):** Sloan accruals, the Beneish M-score
inputs (or a subset), why CFO persistently below NI is a warning, the
peer-relative framing, and the honest caveat that quality screens flag
risk, not fraud, and misfire on legitimately high-growth names.

**Output mode:** note. **Plan:** Stocks Starter (fundamentals endpoints)
or SEC EDGAR fallback.

### `performance-attribution`

**Gap:** `risk-report` measures portfolio risk but never attributes
where the return came from.

**Does:** takes a filled book and a benchmark and runs Brinson-style
attribution: how much of the active return came from sector allocation,
how much from security selection, and how much from the interaction.

**Inputs:** a positions/returns file (same shape `risk-report` and
`portfolio-mark` accept), `--benchmark`.

**Returns:** total active return, allocation effect, selection effect,
interaction effect, a per-sector breakdown of each, and the top
contributors and detractors by name.

**Methodology (references/):** the Brinson-Hood-Beebower decomposition,
single-period vs linked multi-period attribution, the choice of
benchmark weights, and the caveat that attribution is descriptive
accounting, not a skill test at small n.

**Output mode:** table. **Plan:** Stocks Starter.

### `liquidity-stress`

**Gap:** the set reads price and volatility but has no view on whether a
name is tradeable at size right now. `technical-briefing` reports
current spread and ADV bucket but doesn't trend them or flag
compression.

**Does:** for a ticker or watchlist, trends the bid-ask spread (bps),
30-day ADV, and turnover ratio against the name's own trailing history,
and flags when liquidity is compressing (spread widening + ADV falling
together). The output is "ALLO's spread is at the 92nd percentile of
its 6-month history while ADV is at the 18th; treat this as a
liquidity-stress signal."

**Inputs:** `--ticker` or `--watchlist`, `--lookback-days` (default 120).

**Returns:** current spread bps + percentile vs trailing, current ADV +
percentile vs trailing, turnover ratio, a compression flag with the
reasons, and a suggested max-participation cap given ADV.

**Methodology (references/):** spread bps from NBBO snapshot, ADV
percentile via rolling window, why simultaneous spread-widening AND
ADV-compression is a warning (execution slippage + gap risk), the
correlation to earnings/event windows, and a caveat that intraday
spread varies with time of day.

**Output mode:** exception-report. **Plan:** Stocks Starter.

### `short-vol-postprint-pnl`

**Gap:** `earnings-drilldown` reports "implied X% rich vs realized Y%"
but stops at the number. It doesn't tell you what happened to a short
straddle sold on each prior print.

**Does:** for the last N earnings, simulates a hypothetical short-
straddle (or short-strangle) opened at the pre-print ATM strike and
closed at T+1, T+3, T+5, and reports the P&L distribution. Turns the
"straddle is rich" claim into an actual back-tested edge check.

**Inputs:** `--ticker`, `--strategy` (straddle / strangle), `--exit`
(T+1 / T+3 / T+5), `--strikes-otm` (for the strangle variant).

**Returns:** per-print premium collected, close price and realized P&L
at each exit, win rate, avg win / avg loss, max loss, expectancy, and
a comparison of the current implied vs the historical median realized
absolute move.

**Methodology (references/):** ATM strike interpolation for prints
where an ATM didn't exist, the pre-print snapshot vs day-of-print
close as the entry proxy, why 8 prints is barely a signal (base-rate
caveat), and why realized-vol-was-low does not mean short-vol-was-
profitable (skew, gap risk, one bad print).

**Output mode:** table + take. **Plan:** Options Developer add-on.

---

## Part 2: A retail-trader tier

A new audience. The current 20 are analyst-grade: dense, statistical,
built for someone who already speaks the language. A retail tier keeps
the same data grounding and honesty but trades depth for clarity and
action. Plain-English reads, key levels people actually watch, and
simple sizing/risk framing. Every tool still cites its data and still
refuses to fabricate.

Design rule for this tier: a first-time trader should understand the
output without a glossary, and it should never imply certainty the data
doesn't support.

### `watchlist-brief`

**Does:** a one-paragraph morning digest per watchlist name. What moved
overnight, why (tied to news or sector), and what's on deck today
(earnings, ex-div, events). The retail version of a desk's morning run.

**Inputs:** `--watchlist`.

**Returns:** per-name: overnight/pre-market move, a plain-English
reason, next catalyst with date, and a one-line "what to watch." Sorted
by absolute move so the movers are on top.

**Output mode:** stream. **Plan:** Stocks Starter (news add-on improves
the "why").

### `stock-one-pager`

**Does:** a beginner-friendly snapshot card for a single name. Price and
recent trend in words, valuation translated out of jargon ("priced for
strong growth" instead of a raw multiple), key levels, and the next
catalyst. The thing to read before you buy something you saw on social.

**Inputs:** `--ticker`.

**Returns:** plain-language trend read, valuation-in-english, 52-week
range position, key support/resistance levels, next earnings/event
date, and a short honest "what could go wrong" section.

**Output mode:** note. **Plan:** Stocks Starter.

### `explain-the-move`

**Does:** answers "why is my stock up/down today?" It ties the day's
move to the market, the sector, and any name-specific news, and flags
when the move diverges from an obvious cause (moved on no news, or
faded good news). Kills the urge to invent a narrative.

**Inputs:** `--ticker`.

**Returns:** the day's move decomposed into market beta, sector, and
idiosyncratic components; the most likely news driver; and a
divergence flag ("up on no news" / "sold off on a beat").

**Methodology (references/):** simple market/sector beta decomposition,
the news cross-reference, and the caveat that attribution on a single
day is suggestive, not proof.

**Output mode:** note. **Plan:** Stocks Starter (news add-on improves
attribution).

### `key-levels`

**Does:** the support/resistance, round-number magnets, and 52-week-high
proximity that retail actually trades around, stated as concrete price
levels with why each matters.

**Inputs:** `--ticker`.

**Returns:** nearest support and resistance with the basis (prior
swing, moving average, round number), distance to 52-week high/low,
and a plain read on where the name sits in its range.

**Methodology (references/):** swing-high/low detection, moving-average
confluence, round-number psychology, and a caveat that levels are
reference points, not guarantees.

**Output mode:** note. **Plan:** Stocks Starter.

### `income-strategist`

**Does:** a covered-call and cash-secured-put screener framed for income
seekers. For a name (or a holding you already own), it surfaces
reasonable strikes with the yield, the annualized return if unassigned,
and the assignment risk, all in plain English.

**Inputs:** `--ticker`, `--strategy` (covered-call / cash-secured-put),
optional shares owned or cash available.

**Returns:** candidate strikes with premium, static yield, annualized
return, breakeven, probability-of-assignment proxy, and a plain-English
tradeoff line per strike.

**Methodology (references/):** static vs if-assigned return math,
delta as an assignment-probability proxy, why selling premium caps
upside, and the caveat that options carry assignment and tax
consequences.

**Output mode:** table. **Plan:** Options Developer add-on.

### `risk-per-trade`

**Does:** answers "how many shares should I buy?" The retail-simple
version of `position-sizer`. You give it your account size, the percent
you're willing to risk, your entry, and your stop; it returns the share
count and the dollar risk, and warns when the position is too big for
the account.

**Inputs:** `--account-size`, `--risk-pct` (default 1%), `--entry`,
`--stop`.

**Returns:** share count, dollar risk, position value, position as a
percent of account, and a warning flag when the position exceeds a
sane concentration limit.

**Methodology (references/):** fixed-fractional risk sizing, why risk
is defined by stop distance and not by conviction, the difference
between position size and risk size, and a caveat that stops can gap.

**Output mode:** note. **Plan:** none (pure math, no market data beyond
the current price).

### `trade-journal-analyzer`

**Does:** ingests a trade log and reflects it back: win rate, average
win vs average loss, expectancy, and behavioral leaks (revenge trades,
oversizing after a loss, cutting winners early). A mirror, not a coach.

**Inputs:** a trades CSV (same spirit as `examples/sample-trades.csv`).

**Returns:** win rate, avg win / avg loss, profit factor, expectancy
per trade, largest drawdown in the log, and a flagged list of
behavioral patterns with the trades that triggered each flag.

**Methodology (references/):** expectancy and profit-factor math, the
behavioral-pattern detection rules (sizing variance, hold-time
asymmetry between winners and losers), and the caveat that small
samples make win-rate stats noisy.

**Output mode:** exception-report. **Plan:** none (analyzes the user's
own file).

### `dividend-tracker`

**Does:** an ex-dividend calendar and income projection for a holdings
list. What pays when, how much, and the projected income over a
horizon.

**Inputs:** a holdings file (ticker + shares), `--horizon-months`.

**Returns:** per-holding upcoming ex-div dates, per-payment and
projected-period income, current yield, and a total projected income
line for the book.

**Methodology (references/):** ex-date vs record vs pay date, trailing
vs forward yield, the caveat that declared dividends can be cut, and
that special dividends are not recurring.

**Output mode:** table. **Plan:** Stocks Starter.

---

## Build order (suggested)

Highest leverage first, based on filling a real gap with data that's
reachable:

1. **`short-interest-monitor`** and **`insider-flow`** on the quant side.
   Both are genuine signals the set completely lacks today.
2. **`liquidity-stress`** next. Uses data already in the pipeline
   (snapshot spread + daily aggs), extends `technical-briefing` into a
   real execution signal, and slots naturally into the exception-report
   family alongside `slippage-cost`.
3. **`explain-the-move`** and **`stock-one-pager`** to anchor the retail
   tier. They are the two most-asked retail questions and lean on data
   the framework already pulls.
4. **`risk-per-trade`** and **`trade-journal-analyzer`** next: pure-math,
   no data entitlement, so they ship fast and give the retail tier
   immediate utility.
5. **`short-vol-postprint-pnl`** once options entitlements are in play.
   Pairs directly with `earnings-drilldown` and turns the rich-straddle
   claim into a defensible back-test.
6. The rest as demand and data entitlements allow.

Every one of these is a clean addition. The framework's dual-layer
contract and the audit gate mean adding them doesn't touch the existing
20.

---

## Part 3: Portfolio decision-support and macro context

Added 2026-07-02 after a live portfolio review missed an 87.5M-share ALLO
public offering that was the single largest driver of the position's
recent price action. The gaps that surfaced: no dedicated corporate-
actions surface, no rebalance-recommendation layer on top of the risk
report, no macro-event calendar to sit alongside the earnings one, and
no fixed-income context to anchor equity valuations.

Every entry here follows the Part 1/Part 2 shape.

### `corporate-actions-scanner`

**Gap:** `news-scanner` catches everything, `earnings-blackout` catches
forward earnings, but nothing specifically surfaces material corporate
actions (offerings, at-the-market programs, splits, spin-offs,
buybacks, M&A, going-private). These are the news items that
mechanically re-rate a stock and none of the current tools flag them
with the specificity they deserve.

**Does:** for a ticker or watchlist, hits SEC EDGAR for 8-K filings
tagged Items 1.01, 3.02, 8.01 and cross-references Massive news for
offering-specific keywords ("prices offering", "at-the-market", "S-3",
"repurchase authorization", "special dividend", "acquires"). Returns
an ordered stream of material corporate actions with the mechanic
explained ("87.5M share offering at $2, 34% dilution, $175M raise").

**Inputs:** `--ticker` or `--watchlist`, `--lookback-days` (default 180,
this is deliberately wider than news-scanner's 24h default because
material corporate actions can be months old and still be the dominant
explainer), `--material-only` (filter out routine 8-Ks).

**Returns:** per-event: date, action type (offering / split / spin /
buyback / M&A / arb outcome), the mechanical impact (dilution
percentage, cash raised, ratio, deal value), the price reaction at
T+1/T+5, and a one-line English read.

**Methodology (references/):** the 8-K item taxonomy and which items
actually matter for retail-relevant corporate actions, the keyword
list for the news cross-reference (offering, ATM, S-3, buyback,
authorization, definitive agreement, arbitration), the base rate that
routine 8-Ks (Item 5.02 officer changes, Item 5.07 vote results)
outnumber material ones ~10:1 so the material-only filter is not
optional, and the honest caveat that some material actions (especially
private placements) reach 8-K after the fact.

**Output mode:** stream (per-event blocks). **Plan:** Stocks Starter
(EDGAR is free, Massive news is included).

### `portfolio-rebalancer`

**Gap:** `risk-report` tells you ALLO carries 66% of portfolio
variance at 18% weight. `performance-attribution` tells you where
returns came from. Neither tool answers "so what should I change?"
The decision-support layer is missing.

**Does:** takes current positions with weights and outputs a specific
rebalance recommendation to hit a variance-share cap ("trim ALLO from
18.3% weight to 10% to move variance share from 66% to 30%"). Not
tax-aware in v1, not liquidity-aware in v1, but honest about both.

**Inputs:** a positions/weights file (same shape as risk-report),
`--max-variance-share` (default 25%), `--max-weight-per-name`
(default 15%), `--min-trade-size-dollar` (avoid dust trades),
optional `--target-vol` for whole-portfolio scaling.

**Returns:** current variance shares per name, proposed weight changes
(delta and post-trade weight), post-trade projected variance share
per name, dollar amounts to buy/sell per name, and a "before/after"
summary of portfolio vol, beta, and top-3 variance share. Refuses to
recommend more than a preset max churn per rebalance (default 10% of
book) so the tool doesn't blow up a portfolio in one call.

**Methodology (references/):** variance-share attribution (already in
risk-report), constrained quadratic optimization to hit the caps,
why the tool is not a full Markowitz mean-variance optimizer (needs
covariance shrinkage assumptions and forward-return estimates it
does not have), and the caveat that this is descriptive rebalancing
against a risk cap, not a return-maximizing optimizer.

**Output mode:** table + take. **Plan:** Stocks Starter.

### `macro-event-calendar`

**Gap:** `earnings-blackout` is single-name and forward-looking.
Nothing covers the macro calendar (Fed meetings, FOMC minutes, CPI,
PPI, NFP, ISM manufacturing/services, GDP, PCE, JOLTS, jobless
claims, retail sales). These events reprice the whole book and
belong in every portfolio review.

**Does:** for a date range, returns the scheduled macro events with
the release time, prior print, consensus (where available), and the
historical average absolute SPY reaction to the release. Flags
"crowded" windows where multiple events cluster.

**Inputs:** `--window-days` (default 30), optional `--events`
(filter to a subset, e.g. "FOMC,CPI,NFP"), `--benchmark`
(reaction target, default SPY).

**Returns:** per-event: date, release time (ET), event name, prior,
consensus, historical average |SPY 1-day move| on that release,
percentile of that move vs SPY's own history. Sorted by date.

**Methodology (references/):** the release schedule sources (BLS, BEA,
Fed, ISM, Conference Board, Census, Department of Labor), why
consensus is stale by the time a release lands (revisions and
whisper), the historical reaction estimator (rolling 24 releases),
and the caveat that macro reactions are regime-dependent (CPI hits
harder in inflation regimes than in disinflation).

**Output mode:** table. **Plan:** none for the calendar itself
(release schedules are public); Stocks Starter for the SPY reaction
estimator.

### `fixed-income-context`

**Gap:** every equity valuation in the set uses implicit assumptions
about WACC and equity risk premium, but nothing in the toolchain
actually looks at the rates side of the world. Rates and credit
spreads drive equity multiples more than most equity-only screens
admit.

**Does:** pulls the Treasury yield curve (2s5s10s30s), IG and HY
credit spreads, TIPS breakevens, and the SOFR term structure.
Reports the current shape, its percentile vs trailing history, and
the direction of change over the last 30 days. Flags the classic
regime signals (curve inversion, HY spread widening, real yield
inflection).

**Inputs:** `--lookback-days` (default 252 for percentile ranking).

**Returns:** current yield curve levels and slopes (2s10s, 5s30s, and
the inversion flag), IG and HY OAS spreads with percentile, 5y/10y
TIPS breakevens, one-line reads on each ("2s10s at -35bp, deepest
inversion since 2000", "HY spread at 62nd percentile, no stress
signal"), and a "regime read" that flags divergences (equity
uptrending while credit spreads widen is the classic warning).

**Methodology (references/):** which curve slopes matter and why
(2s10s vs 3m10y for recession signal, real yield for growth
expectations), OAS vs spread-to-worst for HY, TIPS breakevens as
implied inflation vs actual inflation, and the caveat that the yield
curve's recession-predictive power weakened post-2020 QE-era.

**Output mode:** note. **Plan:** requires a fixed-income data source
(FRED for Treasuries + spreads is free; Massive doesn't cover rates
natively).

### `historical-analog-finder`

**Gap:** `market-regime` tells you the current state (uptrend, breadth
73%, VIX unavailable, sector leadership XLV/XLF/XLI). Nothing takes
that state and says "these are the prior periods with a similar
setup and here's what SPY did next."

**Does:** given the current market regime vector (SPY trend, 20-day
breadth, VIX percentile, yield-curve shape, sector leadership),
finds the K nearest prior periods using cosine similarity or a
regime-classifier, and reports the forward 30/60/90/252-day SPY
distribution across those analogs. Regime-conditional forecasting.

**Inputs:** `--k` (nearest-analog count, default 20), `--horizon-days`
(default 90), `--features` (which regime dimensions to weight),
optional `--asof` for backtesting.

**Returns:** the K matched historical dates, the forward-return
distribution (mean, median, IQR, worst/best), the hit rate above
zero, and a specific "similar to 2019 late-Q1, 2016 early-Q3, ..."
analog list. A caveat block on how many analogs are non-overlapping
(if K=20 but 15 of them are the same 2016 window, the effective
sample is much smaller).

**Methodology (references/):** feature normalization (z-scores across
each regime dimension), the similarity function (Euclidean on
z-scored features vs cosine, with the choice explained), overlap
detection to prevent one historical window from dominating the
distribution, and the honest caveat that regime-conditional
forecasting works until the world changes structurally.

**Output mode:** table + note. **Plan:** Stocks Starter.

### `options-structure-analyzer`

**Gap:** `income-strategist` covers covered-calls and cash-secured-
puts (income), `options-flow` covers positioning, `vol-surface` (Part
1 proposal) covers pricing. Nothing helps you pick the right
structure for a directional or volatility view.

**Does:** given a thesis (`--view` = "direction", "vol", "hedge") and
a horizon, ranks the appropriate options structures (long call, put,
vertical, straddle, strangle, calendar, collar) by expected P&L,
capital required, breakevens, and the Greek exposure. Not a
recommendation, a structured comparison.

**Inputs:** `--ticker`, `--view` (direction bullish/bearish, vol
long/short, hedge), `--horizon-days`, `--target-move-pct` (your
thesis on how much and how fast).

**Returns:** per-structure: max profit, max loss, breakeven levels,
capital required, delta/vega/theta at inception, and a one-line
plain-English read ("straddle costs $8.40 for a break of $8.40 in
either direction; you need at least a 4% move by expiry to break
even"). Sorted by risk/reward given the specified view.

**Methodology (references/):** Black-Scholes for pricing sanity
(rather than trusting exchange quotes for illiquid strikes), why
verticals cap risk but also cap reward, calendar structures as a
theta bet, and the caveat that options carry gap risk, dividend risk
on calls, and assignment risk on shorts.

**Output mode:** table. **Plan:** Options Developer add-on.

### `sector-rotation-signal`

**Gap:** `market-regime` reports current sector leadership
(XLV/XLF/XLI up, XLE/XLK down) but treats it as a snapshot. Nothing
flags the *change* — when leadership is rotating, that's the
tradeable signal, not the current standings.

**Does:** tracks 20-day and 60-day RS for the 11 SPDR sector ETFs
against SPY, computes the trailing 30-day change in rank, and flags
sectors that are rotating up or down through the leadership order.
Complements `market-regime` (state) with a change-detection layer.

**Inputs:** `--lookback-days` (default 252), `--rotation-window`
(days over which to compute rank change, default 30).

**Returns:** current rank of each sector by 20-day RS, the delta in
rank over the rotation window, a "rotating in" and "rotating out"
list, and a one-line macro read ("defensive rotation: XLU/XLP moving
up, XLK/XLY moving down; consistent with late-cycle").

**Methodology (references/):** sector-ETF proxy (11 SPDRs) vs the
full sector universe, why rank change matters more than absolute RS
(the market prices absolute strength; rank change is the leading
signal), the caveat that sector rotation is noisy at short windows
and clear at longer ones, and why this tool does not fire
recommendations (leave the "so what" to a portfolio-level tool).

**Output mode:** table + take. **Plan:** Stocks Starter.

---

## Revised build order (all three parts)

The Part 1 ordering still stands for its own scope. Adding Part 3 the
priority becomes:

1. **`corporate-actions-scanner`**. Fills the specific gap that missed
   the ALLO offering on 2026-07-02. Zero data-entitlement lift (EDGAR +
   existing Massive news). Highest signal-to-noise in the whole
   proposal set for retail-and-analyst use cases alike.
2. **`macro-event-calendar`**. Free schedule data, complements the
   existing earnings surface, and belongs in every portfolio review.
3. **`portfolio-rebalancer`**. Requires `risk-report` output (already
   available) and a solver library (SciPy already a dependency).
   Decision layer that promotes the framework from measurement to
   action.
4. **`fixed-income-context`**. Free via FRED. Small skill, disproportionate
   context add for every equity valuation and regime read.
5. Part 1 items in original order: `short-interest-monitor`,
   `insider-flow`, `liquidity-stress`.
6. `sector-rotation-signal` and `historical-analog-finder` once the
   Part 3 core is in — they lean on `market-regime` output shape.
7. Part 2 retail items.
8. `options-structure-analyzer` and Part 1 `vol-surface` and
   `short-vol-postprint-pnl` after options entitlements.
