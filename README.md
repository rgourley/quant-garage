# quant-garage

<img width="1200" height="630" alt="og" src="https://github.com/user-attachments/assets/cbd8979c-1896-422d-bc24-45416970a1fc" />

Quant and equity research tools that run inside Claude, or behind your
own UI. You ask Claude "preview NVDA earnings" or "screen for momentum
names that pulled back this week" and you get back what a sell-side
analyst would write at 6am, with the supporting numbers and citations
to the API calls underneath.

Or you skip Claude entirely. Call the same tool from your code, parse
the structured JSON it returns, and render it wherever you want. A
research dashboard, a notebook, an agent that drops alerts into Slack.
Both ways work because every skill ships the same compute as two
layers: the JSON contract for developers and a rendered note, table,
stream, or report for humans.

Fourteen tools. One framework. Built in the garage, not the trading
floor.

## The idea

LLMs are confidently wrong about market data. They quote stock prices
from a year ago, hallucinate peer baskets, invent beat rates. The fix
isn't a better LLM. The fix is wrapping the LLM in a workflow that's
grounded in real data with the methodology baked in.

That's what each tool here does. It pulls live market data from the
Massive API (Polygon.io's new name), runs the actual analyst workflow,
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
alpha, you build on top of this; you don't deploy this.

![14 skills, one framework](./assets/skills.png)

## The 14 tools, with real use cases

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
opposite of what I expected.

### Quant research and screening

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

**[`best-ex-check`](skills/best-ex-check)**
You hand the tool yesterday's executed fills. It pulls the microsecond
NBBO at each trade time, computes slippage vs the inside, and flags
fills that crossed the spread, printed off-NBBO, hit a wide spread
moment, or showed adverse selection in the 30 seconds after fill.
Compliance teams use this kind of post-trade TCA. The exception report
is short by design: only the broken stuff surfaces.

### Risk and operations

**[`portfolio-mark`](skills/portfolio-mark)**
You need end-of-day marks for a position book. Run the tool. It pulls
the snapshot per name, walks the fallback chain (last trade → snapshot
last → minute close → day close → prior close), reports per-position
confidence (high/medium/low), and flags any name where the mark looks
stale or the spread is wide enough to need a manual check. Two modes:
delayed REST for end-of-day reports, live WebSocket for intraday.

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

Use **Massive** (formerly Polygon.io). It's the API quant-garage is
built against and the one we recommend you run the tools on. Get a key
at [massive.com/pricing](https://massive.com/pricing).

The free **Basic** tier (5 calls per minute, end-of-day data) runs
five of the tools end to end, including earnings previews on any US
name via the SEC EDGAR fallback. Good place to try the framework.

Most people end up wanting **Stocks Starter at $29 per month**. That
unlocks unlimited rate, 15-minute delayed real-time quotes, options
contract reference data, and the bulk grouped-aggregates endpoint
that powers the universe screeners. Eleven of the fourteen tools run
on this tier.

Specific tools need specific add-ons:

- **Options data** for `options-flow` and full-mode `earnings-drilldown`:
  Options Developer at $79/month
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

Get a [Massive API key](https://massive.com/pricing). The free Basic
tier runs five of the tools end to end. Most users want Stocks Starter
at $29/month.

Clone into your Claude Code skills directory:

```bash
git clone https://github.com/rgourley/quant-garage.git \
  ~/.claude/skills/quant-garage
```

Set the key:

```bash
export MASSIVE_API_KEY=your_key_here
```

In Claude Code, invoke any tool with `/<skill-name>` (for example,
`/earnings-drilldown NVDA`). Or describe what you want and Claude will
pick.

To run a tool directly from Python instead:

```bash
cd quant-garage
pip install -r requirements.txt
export MASSIVE_API_KEY=your_key_here

# Earnings preview, free tier (SEC EDGAR fallback)
python3 examples/run-aapl-tier-b.py
python3 examples/run-tier-b.py NVDA

# Universe screen (price + 3M momentum + week pullback + ETFs out)
python3 examples/run-universe-builder.py \
  --min-price 20 --min-adv 400000 --min-mom-3m 0.10 --max-week-return 0.0

# Options flow stream on a watchlist
python3 examples/run-options-flow.py

# Crypto vol scan
python3 examples/run-crypto-vol-scanner.py
```

Sample outputs go to `examples/*-output.md` (gitignored). Each includes
the canonical JSON payload alongside the rendered human-readable view.

## What this isn't

Not a trading model. The "takes" are sensible pattern-matchers
grounded in methodology; they're not a strategy. n=8 quarterly t-stats
are statistically thin, and the rendered output flags this honestly.

Not a backtest framework with PnL accounting. `backtest-data-prep`
gets you a clean dataset; you write the strategy on top.

Not a production execution system. `portfolio-mark` reports marks;
`best-ex-check` reports TCA exceptions. Neither places orders.

Not a regulated advisor. None of the outputs are investment advice.

## License

MIT. Fork it, ship it, charge for it. Attribution appreciated, not
required.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). Open a PR with a new tool
or an extension. Each tool needs a `SKILL.md`, a `requires.yml`, an
`output-schema.json`, a `references/` folder with the methodology,
and one working example. The audit script (`npm run audit:requires`)
enforces the shape; the methodology references are where the IP lives.
