# Quant Garage

<img width="1200" height="630" alt="og" src="./assets/og.png" />

Quant and equity research tools that run inside Claude, or behind your
own UI. You ask Claude "preview NVDA earnings" or "screen for momentum
names that pulled back this week" and you get back what a sell-side
analyst would write at 6am, with the supporting numbers and citations
to the API calls underneath.

Or you skip Claude entirely. `pip install quant-garage` and call the
same tool from your own code. Every skill is an importable Python
function that returns JSON, so it drops straight into a Jupyter
notebook, a research dashboard, a Slack alert, a cron job. Both paths
work because every skill ships the same compute as two layers: the
JSON contract for developers and a rendered note, table, stream, or
report for humans.

Twenty-six tools. One framework. Built in the garage, not the trading
floor.

**Needs a [Massive API key](https://massive.com/pricing).** Free Basic
tier runs six of the tools end-to-end; $29/month Stocks Starter opens
twenty-four of the twenty-six.

**Feedback welcome.** Found a bug or have an idea? Open an
[issue](https://github.com/rgourley/quant-garage/issues) or send a
[pull request](https://github.com/rgourley/quant-garage/pulls).

## What the collection does

Each tool is useful on its own. The point of having twenty-six that
share data, methodology, and audit trail is that they chain.

Tuesday morning, you're long NVDA into Thursday's print.
`earnings-drilldown` shows the implied move is rich vs the 8-quarter
realized. `valuation-sanity-check --mc` puts the current price at the
78th percentile of peer-driven fair values. You like the setup but
want to size honestly. `position-sizer` puts NVDA at 8% under
vol-target given the 48% realized vol. You execute. `portfolio-mark`
walks the snapshot fallback chain; `slippage-cost` flags one fill that
crossed the spread. End of day: `risk-report` shows NVDA now drives
35% of portfolio variance.

One repo, one Massive key, one methodology.

The research side has the same shape. `universe-builder` screens for
momentum pullbacks; `factor-research` confirms which factors are
working this regime; `news-scanner` checks for catalysts on the
survivors; `event-study` measures the abnormal return around each
catalyst. You get from "what should I look at?" to "here's the regime
context and the abnormal-return distribution" in a single workflow.

Each tool plugs into the same `quant_garage/` core: same client, same
timezone handling, same audit-trail format, same significance
thresholds.

## The idea

LLMs are confidently wrong about market data. They quote stock prices
from a year ago, hallucinate peer baskets, invent beat rates. The fix
isn't a better LLM. The fix is wrapping the LLM in a workflow that's
grounded in real data with the methodology baked in.

That's what each tool here does. It pulls live market data from the
Massive API, runs the actual analyst workflow,
and returns a result you can trace back to the calls it made and the
timestamps it made them at. No guesses. No hallucinated peer baskets.

The methodology references inside each skill folder are where the IP
lives: statistical methods, sample-size rules, base rates, edge cases,
honest caveats about what the take does and doesn't prove. The Massive
API just provides the inputs.

## Who this is for

Anyone serious about market research who wants tools they can fork
and put their own ideas behind.

**Active traders** building a personal daily workflow that doesn't
depend on a $25k Bloomberg terminal. Run the earnings preview before
the open, the options flow scan during lunch, the post-trade TCA at
the close. Adjust the methodology references to match how you actually
think about a name.

**Indie devs** building finance-adjacent apps. The structured JSON
contract on every tool means you wire the output into your dashboard,
agent, or notebook without parsing markdown. Each skill is a working
data layer you can call from your own code.

**Power users** researching their own positions. You want a sanity
check that doesn't come from a Reddit thread or a chatbot guessing.
You want the citation, the timestamp, the sample size, the honest
caveat when the data is thin.

The obvious users too: **buy-side junior analysts** prototyping
faster, **sell-side analysts** who want their morning notes drafted
by 6am, **PMs** sanity-checking a model before the meeting, **quant
data engineers** forking the framework into something proprietary,
**armchair quants** who want production-shaped tools to learn the
craft on.

**Not for:** people looking for alpha. These tools encode analyst
workflow, not strategy. The takes are pattern-matchers grounded in
methodology; they aren't a trading model. If you want production
alpha, you build on top of this.

![26 skills, one framework](./assets/skills.png)

## The 26 tools, with real use cases

### Earnings work

**[`earnings-drilldown`](skills/earnings-drilldown)**
You're long NVDA into Thursday's print. Trim, hold, or fade the
straddle? Run the tool. You get the implied move vs the 8-quarter
realized average, the post-earnings drift t-stat conditional on the
reaction direction, and which semis trade with NVDA on print days.
Output reads like a sell-side morning note: bold take at the top,
supporting numbers below.

**[`event-study`](skills/event-study)**
You want to measure abnormal returns around any event class:
earnings, dividend changes, large volume spikes. Single event for
one ticker (gets you a note), the same event across many tickers
(cross-section table), or all events in a window (aggregate stats
with t-stats). Last month's run on mega-cap tech Q1 prints surfaced
that the cross-section average is negative despite all five beating
on EPS. Guidance is dominating headlines this regime.

**[`earnings-blackout`](skills/earnings-blackout)**
You're running a watchlist and want a 30-second pre-trade hygiene
check: which names print this week, which printed yesterday, which
are clear. Run the tool. It returns each ticker bucketed into
blackout-imminent (0-3 days forward), blackout-soon (4-7),
just-printed (0-3 past), or clear, with the date, consensus EPS
where Benzinga is wired, and the 8-K item code where it falls back
to SEC EDGAR. Exception-report style: imminent blackouts on top,
clear names at the bottom.

### Equity research and valuation

**[`pitch-comps`](skills/pitch-comps)**
You're a junior banker or an equity research analyst building a CRM
comp set. You need the software peer group with multiples, growth,
EBITDA margin, plus a regression-adjusted view that controls for the
growth differential. Run it, get a table you can drop straight into
the deck or the model. The one-sentence read at the bottom is the
take the MD or PM wants on page two.

**[`valuation-sanity-check`](skills/valuation-sanity-check)**
The analyst on your team has a $250 NVDA target with assumed 28%
growth and 60% margin. Does it survive a peer-distribution sanity
check? The tool runs the reverse-DCF at the current price and tells
you what CAGR is actually priced in. When I ran it, the $250 target
came back understated against the semi peer set, which was the
opposite of what I expected. Pass `--mc` for the Monte Carlo
fan-of-outcomes mode: 10,000 samples across the peer growth × margin
× exit-multiple distribution, with the current price's percentile
within the resulting fair-value distribution and a per-driver
sensitivity ranking. More honest than a single point estimate.

**[`position-sizer`](skills/position-sizer)**
You like the names — NVDA, AMZN, GOOGL, META all going in. How much
of each? Run the tool and get four canonical sizing methods side by
side: vol-target, fractional Kelly, risk parity, equal weight. The
methods usually disagree; that's the point. Vol-target cuts the
high-vol names so they don't dominate. Kelly tilts toward names with
the highest edge per variance (you supply the edges). Risk parity
equalizes each name's contribution to portfolio variance. Pick the
column whose worldview matches your conviction.

### Quant research and screening

**[`technical-briefing`](skills/technical-briefing)**
You're staring at a single name and want the technical read before
you do anything else. Run the tool on NVDA. You get the composite
trend regime (bullish / bearish / neutral, strong or weak, with the
reasons that drove the label), RSI 14 with bucketed momentum read,
MACD line vs signal with cross status, SMAs 20 / 50 / 200, Bollinger
position, ATR as a percent of price, and the ADV-bucketed liquidity
context. Output is a sell-side morning-note block; the Take is
computed from the actual readings, not a hardcoded narrative.

**[`universe-builder`](skills/universe-builder)**
You want every US common stock above $20 with quarterly +10% momentum
that pulled back this week. Run the screen. The tool walks the full
12,000-name universe, applies your filter chain, ranks the survivors
by composite z-score, and flags sector concentration. Last week's run
surfaced a trucking cluster (ARCB, RXO, SNDR, TFII, WERN, SAIA) all
off the same macro freight pullback. Real mean-reversion candidates.

**[`factor-research`](skills/factor-research)**
You want to know what factor is working in the current regime. Run
the tool on the S&P 500 over a 5-year window. You get the per-factor
IC at 1M/3M/6M/12M horizons, t-stats, decile long-short spreads, hit
rates, and the cross-factor correlation matrix. Quality at +3.1
t-stat is the only single factor with statistical significance right
now. Low-vol is negative across every horizon (recent regime rewards
risk-taking).

**[`relative-strength`](skills/relative-strength)**
You have a basket of names and want to know which are leading and
which are bleeding vs SPY (or any benchmark) across 5 / 20 / 60 /
120-day windows. Run the tool. Each name gets RS in basis points per
window, a composite percentile rank within the basket, and a trend
label (stable_leader, improving, deteriorating, stable_laggard,
mixed). Pass --include-sectors to add the 11 SPDR sector ETFs into
the ranking and see whether NVDA's strength is the name or just XLK.

### Market context

**[`market-regime`](skills/market-regime)**
Daily macro briefing before you do anything else. SPY trend (above
or below 20/50/200-day MAs, 5 buckets from uptrend_strong to
downtrend_strong), VIX state with percentile rank vs trailing year,
breadth proxy from the 11 sector ETFs (how many above 50-day MA),
and 20-day RS leaders/laggards across sectors. Composite regime
label (risk_on, risk_off, mixed_risk_on, mixed_risk_off, neutral)
ships with explicit reasons so you see the evidence, not just the
label. Run it at the open; it's the right anchor for everything
else.

**[`sector-rotation-signal`](skills/sector-rotation-signal)**
`market-regime` reports current sector leadership as a snapshot.
This one tells you how the leadership order has changed. Tracks 20-
day RS rank for the 11 SPDR sector ETFs across a rotation window
(default 30 days) and flags sectors that moved two or more positions
up or down. Categorizes moves into growth / value-cyclical /
defensive / rate-sensitive buckets and generates a plain-English
theme read. Last week's run flagged risk-off: XLU up seven ranks and
XLK down nine, defensives taking share from growth. Rank change is
the leading signal; absolute standings are already priced.

**[`macro-event-calendar`](skills/macro-event-calendar)**
Sibling to `earnings-blackout`. Forward calendar of the macro
releases that reprice the whole book: FOMC decisions, CPI, PPI, NFP,
ISM manufacturing and services, GDP, PCE, JOLTS, jobless claims,
retail sales. Every event ships with its expected release date, ET
release time, historical mean absolute one-day SPY move on that
release type, and impact tier. Crowded days (two-plus events on the
same date) get their own callout. FOMC dates are hardcoded from the
official schedule; recurring events are pattern-derived and flagged
as such so you can verify against the official calendar before
sizing around a print.

**[`historical-analog-finder`](skills/historical-analog-finder)**
`market-regime` tells you today's state. This one takes that state
and finds K historical periods with the most similar setup, then
reports the forward SPY return distribution across those analogs.
Features are SPY-only (5/20/60/120-day return, above 50 and 200-day,
RSI 14, realized vol, drawdown from 252-day high) with z-scored
Euclidean distance. Deduplicates overlapping matches so one
historical window can't dominate the K. The mean is not a forecast;
the interquartile range is the honest read. Last run: 20 nearest
analogs to the current tape gave a 75% hit-rate above zero at 60 and
252 days, median +14.3% at 252d, with mid-2024 and late-2021 pulling
opposite directions in the K set.

### Trading and execution

**[`options-flow`](skills/options-flow)**
You're scanning a watchlist for unusual options activity. Premium
size, volume vs open interest, above-ask vs below-bid, repeat-strike
clustering. Output is a tight stream of the top 10-20 prints with a
sentiment tag per block. Yesterday's run surfaced a TSLA bullish read
where someone sold $400 puts on the bid AND bought $385 calls above
the ask. Two trades, same direction.

**[`news-scanner`](skills/news-scanner)**
You want today's notable news cross-referenced against the price
reaction and sentiment. Each event ships with sentiment score,
novelty score (is this a re-run or a new angle), and a price-vs-
sentiment divergence flag. A "positive" article that the stock sold
off on means the market already knew; that's a flag worth surfacing.

**[`slippage-cost`](skills/slippage-cost)**
You hand the tool yesterday's executed fills. It pulls the microsecond
NBBO at each trade time, computes slippage vs the inside, and flags
fills that crossed the spread, printed off-NBBO, hit a wide spread
moment, or showed adverse selection in the 30 seconds after fill.
Measures fill vs NBBO at fill time, not arrival-price Implementation
Shortfall. The exception report is short by design: only the broken
stuff surfaces.

**[`options-structure-analyzer`](skills/options-structure-analyzer)**
You have a view but no structure yet. Tell the tool your thesis
(direction bullish, direction bearish, vol long, vol short, or
hedge), your horizon, and your target move. It enumerates the
candidate options structures (long call or put, bull or bear spread,
straddle, strangle, iron condor, protective put, collar), pulls the
nearest expiry with priceable legs on both sides, computes each
structure's payoff at your target, and ranks by payoff-to-capital.
Not a recommendation. A structured comparison so you can pick the
structure whose tradeoffs match your view, not just the one your
platform happened to surface. On hedges, it reports the P&L improvement
vs unhedged rather than a meaningless "percent of net premium" ratio.

### Risk and operations

**[`portfolio-mark`](skills/portfolio-mark)**
You need end-of-day marks for a position book. Run the tool. It pulls
the snapshot per name, walks the fallback chain (last trade → snapshot
last → minute close → day close → prior close), reports per-position
confidence (high/medium/low), and flags any name where the mark looks
stale or the spread is wide enough to need a manual check. Two modes:
delayed REST for end-of-day reports, live WebSocket for intraday.

**[`risk-report`](skills/risk-report)**
The book is already marked. Now what could happen to it? Run the tool
and get VaR + Expected Shortfall at 95/99, max drawdown over the
lookback window with peak/trough dates, the five worst historical
days with per-name loss attribution, every position's share of the
variance budget, and a Herfindahl-based concentration read. Pairs
with `portfolio-mark`: marks tell you what the book is worth right
now; this skill tells you what could happen to that value.

**[`portfolio-rebalancer`](skills/portfolio-rebalancer)**
`risk-report` tells you which name is driving the risk. This skill
tells you what to do about it. Feed it the same weights + book
value; set a variance-share cap, weight cap, and churn cap; it
returns a specific rebalance ticket list: which names to trim, which
to add, exact dollar amounts, before-and-after weight, before-and-
after variance share. Iterative solver with a proportional
redistribution rule so one name's trim doesn't over-concentrate the
next name. Refuses to churn more than the preset per rebalance so a
single call cannot blow up a portfolio. Not tax-aware, not
liquidity-aware, honest about both in the caveats. Turns "ALLO
carries 66 percent of variance" into "sell $66k of ALLO, redistribute,
portfolio vol drops from 21 to 15 percent."

**[`corporate-actions-scanner`](skills/corporate-actions-scanner)**
Different job from `corp-actions-reconciler`. The reconciler checks
whether your position file has the correct post-split share count.
This one scans forward-looking: for a ticker or watchlist, it pulls
SEC 8-K filings over a lookback window (default 180 days), filters
to material items (offerings, private placements, splits, spin-offs,
buybacks, M&A, restatements), cross-references Massive news for the
headline, and computes the T+1 and T+5 price reactions. Built after
a portfolio review missed an ALLO public offering (87.5 million
shares at $2, 34 percent dilution) because news-scanner defaulted to
a 24-hour window. The offering was 78 days old and dominating the
current price, and no dedicated tool surfaced it.

**[`corp-actions-reconciler`](skills/corp-actions-reconciler)**
An ops desk inherits a position file from 2024. Did the share counts
get adjusted for AAPL's 4-for-1 split? GOOGL's 20-for-1? NVDA's 10-
for-1? Run the tool. The exception report lists every position whose
recorded share count doesn't match the expected post-split share count,
with the source endpoint and verified-at timestamp on every flag.

**[`t+1-settlement-prep`](skills/t+1-settlement-prep)**
You're an ops manager looking at tonight's trades. Which ones have
settlement risk crossing this weekend? Which ones need a short-sale
locate confirmed before tomorrow's cutoff? Which ones cross an ex-
dividend date? The tool walks each trade against the US holiday and
corporate-action calendar and flags six failure modes, with a
suggested next action per flag.

### Backtesting and infrastructure

**[`backtest-data-prep`](skills/backtest-data-prep)**
You're building a momentum backtest. You need a 4-year OHLCV dataset
that's properly split-adjusted, survivorship-clean, and free of
look-ahead bias. The tool emits a parquet file (1,003 trading days x
99 tickers in the standard run), a manifest documenting every
corporate action applied, and an edge-cases log noting any IPOs,
delistings, or symbol changes inside the window. Drop the parquet
into pandas and start backtesting.

### Crypto

**[`crypto-vol-scanner`](skills/crypto-vol-scanner)**
You watch BTC/ETH/SOL plus a handful of alts. The tool surfaces vol
spikes (vs trailing 30d distribution), volume anomalies, cross-
exchange basis (Coinbase vs Bitfinex vs Bitstamp vs Binance vs
Kraken), and 24h move z-scores. Output is a stream of the top events,
with a one-line read at the bottom on the broader regime (this week's
read: "quiet regime, BTC realized vol at 30% sitting in the 25th
percentile of trailing year, setup-watch day not entry day").

## What to sign up for

Use **Massive**. It's the API quant-garage is
built against and the one we recommend you run the tools on. Get a key
at [massive.com/pricing](https://massive.com/pricing).

The free **Basic** tier (5 calls per minute, end-of-day data) runs
six of the tools end to end, including earnings previews on any US
name via the SEC EDGAR fallback. Good place to try the framework.

Most people end up wanting **Stocks Starter at $29 per month**. That
unlocks unlimited rate, 15-minute delayed real-time quotes, options
contract reference data, and the bulk grouped-aggregates endpoint
that powers the universe screeners. Twenty-four of the twenty-six
tools run on this tier (only crypto-vol-scanner and full-fidelity
options-structure-analyzer need separate plans).

Specific tools need specific add-ons:

- **Options data** for `options-flow`, `options-structure-analyzer`,
  and full-mode `earnings-drilldown`: Options Developer at $79/month
- **Benzinga Earnings** (consensus EPS + surprise %) for full-fidelity
  `earnings-drilldown` and `event-study`: ~$99/month add-on. Without it,
  these tools fall back to SEC EDGAR for press release dates, which we
  verified matches the Benzinga date to the day on 8 of 8 of Apple's
  last 8 prints. So the SEC fallback works, you just lose the
  consensus number.
- **Benzinga News** for `news-scanner`: ~$99/month
- **Crypto Starter** for `crypto-vol-scanner`: $29/month
- **Stocks Advanced** for live-mode `portfolio-mark` with real-time
  WebSocket: $199/month. Delayed-mode portfolio-mark runs fine on
  Starter.

Why Massive over the alternatives: broadest US market data coverage
in one account (stocks, options, crypto, FX, indices, futures), REST
+ WebSocket + S3 flat files all included, cheap free tier so anyone
can try, and the SEC EDGAR fallback we built lets you run the earnings
tools without paying for Benzinga at all.

The [PLAN-MATRIX.md](./PLAN-MATRIX.md) file maps every tool to the
exact plan + add-ons it needs.

## Setup

Get a [Massive API key](https://massive.com/pricing). Free Basic runs
six tools end to end; Stocks Starter ($29/month) opens twenty-four.

```bash
export MASSIVE_API_KEY=your_key_here
```

Three ways to use the tools. Same code, three surfaces.

### 1. Python library (Jupyter, scripts, notebooks)

The most direct way. Every skill is an importable function that returns
JSON.

```bash
pip install quant-garage
```

Then in a notebook or a `.py` file:

```python
from quant_garage.skills import (
    technical_briefing, earnings_drilldown, market_regime,
    pitch_comps, valuation_sanity_check, news_scanner,
)

# One name, one call, one dict back
brief = technical_briefing.run("NVDA")
brief["trend"]["regime"]        # 'bearish_weak'
brief["momentum"]["read"]       # 'weak'
brief["take"]                   # 'NVDA looks soft. RSI 43 weak...'

# Compose skills — pass the same client to reuse HTTP connection
from quant_garage import MassiveClient
client = MassiveClient()

results = {}
for ticker in ["NVDA", "AAPL", "MSFT", "GOOGL", "META"]:
    results[ticker] = technical_briefing.run(ticker, client=client)

# Everything is just dicts, so pandas is a one-liner
import pandas as pd
df = pd.DataFrame([
    {
        "ticker": t,
        "regime": r["trend"]["regime"],
        "rsi": r["momentum"]["rsi_14"],
        "atr_pct": r["volatility"]["atr_pct_of_price"],
        "take": r["take"],
    }
    for t, r in results.items()
])
df.sort_values("rsi", ascending=False)
```

When you want the rendered version instead of the JSON:

```python
print(technical_briefing.render(brief))
```

Every skill follows the same contract: `run(...) -> dict` and
`render(payload) -> str`. Nothing writes to disk unless you ask.

**Extras.** The core install is slim. Skills that need pandas/scipy or
S3 declare their own extras:

```bash
pip install quant-garage[research]    # factor-research, backtest-data-prep
pip install quant-garage[flatfiles]   # boto3 + s3fs for bulk daily aggs
pip install quant-garage[live]        # websocket-client for live portfolio-mark
pip install quant-garage[all]         # everything
```

### 2. CLI (shell pipelines, cron jobs)

Every skill also ships as a thin CLI wrapper under `examples/`. Defaults
to JSON on stdout, so you can pipe into `jq` or another tool.

```bash
# JSON out (default)
python3 examples/run-technical-briefing.py --ticker NVDA | jq '.trend.regime'

# Rendered analyst note
python3 examples/run-technical-briefing.py --ticker NVDA --format render

# Universe screen (price + 3M momentum + weekly pullback)
python3 examples/run-universe-builder.py \
  --min-price 20 --min-adv 400000 --min-mom-3m 0.10 --max-week-return 0.0 \
  --format render

# Earnings drilldown, free tier (SEC EDGAR fallback)
python3 examples/run-earnings-drilldown.py --ticker AAPL --format render

# Options flow stream on a watchlist
python3 examples/run-options-flow.py NVDA TSLA AAPL --format render

# Crypto vol scan
python3 examples/run-crypto-vol-scanner.py --format render
```

Pass `--out FILE.md` on any runner to also write a markdown file with
both layers (rendered + JSON).

### 3. Claude Code skills

Clone into your Claude Code skills directory:

```bash
git clone https://github.com/rgourley/quant-garage.git \
  ~/.claude/skills/quant-garage
```

Then invoke any tool with `/<skill-name>` (for example,
`/earnings-drilldown NVDA`), or just describe what you want in plain
English and Claude will pick.

### The typical workflow

Massive customers we've talked to tend to move through this pattern:

1. **Ideate in Claude.** Describe what you're trying to figure out. Get
   a rendered read from one of the skills. Iterate on the framing.
2. **Test in Jupyter.** Import the same skill as a library. Feed it more
   tickers, join to your own data, chart the result, decide whether it
   holds up.
3. **Push to prod.** The library is the production interface. Wire it
   into a cron, a Slack bot, a research dashboard, whatever. The skill's
   JSON is stable across all three surfaces.

We built the framework this way on purpose. Nothing in the middle step
requires you to shell out to a CLI or scrape a rendered note. The same
`payload = run(...)` call that answered the ideation question is the
one your production job runs.

## What this isn't

Not a trading model. The "takes" are sensible pattern-matchers
grounded in methodology; they're not a strategy. n=8 quarterly t-stats
are statistically thin, and the rendered output flags this honestly.

Not a backtest framework with PnL accounting. `backtest-data-prep`
gets you a clean dataset; you write the strategy on top.

Not a production execution system. `portfolio-mark` reports marks;
`slippage-cost` reports fill-vs-NBBO exceptions. Neither places orders.

Not a regulated advisor. None of the outputs are investment advice.

![Built in the garage, not the trading floor.](./assets/closing.png)

## License

MIT. Fork it, ship it, charge for it. Attribution appreciated, not
required.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). Open a PR with a new tool
or an extension. Each tool needs a `SKILL.md`, a `requires.yml`, an
`output-schema.json`, a `references/` folder with the methodology,
and one working example. The audit script (`npm run audit:requires`)
enforces the shape; the methodology references are where the IP lives.
