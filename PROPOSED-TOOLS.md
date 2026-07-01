# Proposed Tools

A design backlog for extending Quant Garage. Two tiers: deeper quant
tools that fill real gaps in the current 20, and a new retail-trader
tier that trades analyst depth for clarity and decision support.

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
2. **`explain-the-move`** and **`stock-one-pager`** to anchor the retail
   tier. They are the two most-asked retail questions and lean on data
   the framework already pulls.
3. **`risk-per-trade`** and **`trade-journal-analyzer`** next: pure-math,
   no data entitlement, so they ship fast and give the retail tier
   immediate utility.
4. The rest as demand and data entitlements allow.

Every one of these is a clean addition. The framework's dual-layer
contract and the audit gate mean adding them doesn't touch the existing
20.
