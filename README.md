# finance-playbook

![finance-playbook](./assets/og.png)

Claude Code skills for grounded market data workflows. Ground your Claude
sessions in real numbers. Or build your own UI on top. Same skill, both
surfaces.

```
NVDA: Tier B Preview (run 2026-06-23 17:41 UTC)
Next print (projected): 2026-08-19 AMC · Spot: $202.55

**Take:** Straddle prices 6.6pp above 8q realized (implied ±10.3%, realized ±3.7%).
Premium sellers have a setup.

Implied vs realized
- Implied move (front straddle, 0.85-adj): ±10.3% (raw straddle ±12.1%)
- Realized 8q avg: ±3.7%
- IV30 (proxy from ATM avg): 37.3

Post-earnings drift (T+1 to T+5)
- After negative reactions: −7.5% avg (n=6, significant)

Cross-asset
- Top peer betas: MU β=0.71, AVGO β=0.62, TSM β=0.54
```

That's what `earnings-drilldown` returns for NVDA today. Every number traces
back to a `api.polygon.io` call with a timestamp. Built on the Massive API
(formerly Polygon.io) for the data; the operator value is the same regardless
of provider: verified answers instead of plausible ones.

## Status

This is an early public release. One skill (`earnings-drilldown`) is built
end-to-end, verified against real AAPL and NVDA data, and produces both
JSON (canonical) and rendered (note-mode) outputs. Three foundation skills
(`massive-api-patterns`, `massive-flat-files`, `massive-websockets`) capture
the patterns the rest of the suite will inherit. Twelve more workflow
skills are designed in this repo's matrix; contributions welcome.

| Status | Skill |
|---|---|
| Built | `earnings-drilldown` (Tier A + Tier B), foundations |
| Template stub | `corp-actions-reconciler` (docs + schema, no implementation yet) |
| Designed, not yet implemented | The other 12 (see [PLAN-MATRIX.md](./PLAN-MATRIX.md)) |

## Two ways to use these

**In Claude Code.** Type `/<skill-name>` and read the rendered output
inline. The format matches what the workflow's users already consume:
sell-side morning note for earnings, Cheddar Flow-style stream for
options activity, screener-table for filters, exception report for
reconciliations.

**As a data layer for your own UI.** Call Claude via the API with the
skill loaded, parse the structured JSON payload (every skill ships an
`output-schema.json`), and render it in your dashboard, terminal,
notebook, or agent. The JSON is the contract; the rendering is a default,
not a constraint.

## Three interfaces, not one

Each skill picks the right route for its job:

- **REST** for current state and small lookups. Auth, fallback chain, and
  rate-limit handling live in [`massive-api-patterns`](skills/massive-api-patterns).
- **Flat files (S3)** for bulk historical pulls. Daily files of trades,
  quotes, and aggregates across all asset classes. Included in every paid
  plan at no extra cost. Patterns in [`massive-flat-files`](skills/massive-flat-files).
- **WebSockets** for live streams. OPRA options, NBBO stocks, crypto, FMV.
  Needs a real-time tier. Patterns in [`massive-websockets`](skills/massive-websockets).

You don't need to know which is which. The skill picks. You get the result
plus a citation showing exactly where each number came from.

## The planned skills

See [PLAN-MATRIX.md](./PLAN-MATRIX.md) for the full matrix of which Massive
plan each skill needs.

### Quant research

| Skill | What you can do with it | Status |
|---|---|---|
| [`universe-builder`](skills/universe-builder) | Filter the full US universe by liquidity, market cap, sector, or any combination | Designed |
| [`factor-research`](skills/factor-research) | Run value, momentum, quality screens with the underlying datapoints attached | Designed |
| [`event-study`](skills/event-study) | Pull price and volume behavior around earnings, FDA, M&A windows | Designed |
| [`backtest-data-prep`](skills/backtest-data-prep) | Clean OHLCV with corporate-action adjustments and survivorship handling | Designed |
| [`options-flow`](skills/options-flow) | Surface unusual activity, large prints, IV crush around catalysts | Designed |
| [`crypto-vol-scanner`](skills/crypto-vol-scanner) | Catch cross-exchange volatility and volume anomalies in crypto | Designed |
| [`news-scanner`](skills/news-scanner) | Cross-reference news, sentiment, and price action on the same surface | Designed |

### Banker workflows

| Skill | What you can do with it | Status |
|---|---|---|
| [`earnings-drilldown`](skills/earnings-drilldown) | Brief a print with filings, estimates, price action, and IV crush in one pull | **Built (verified against real AAPL and NVDA data)** |
| [`pitch-comps`](skills/pitch-comps) | Build a comp set with live EV/EBITDA, P/E, and growth, ready to drop in a deck | Designed |
| [`valuation-sanity-check`](skills/valuation-sanity-check) | Cross-check an analyst model against current marks before the meeting | Designed |

### Risk and operations

| Skill | What you can do with it | Status |
|---|---|---|
| [`portfolio-mark`](skills/portfolio-mark) | Mark a book to last trade with a documented fallback chain | Designed |
| [`corp-actions-reconciler`](skills/corp-actions-reconciler) | Catch splits, dividends, and spinoffs against a position file before they break P&L | Template stub |
| [`best-ex-check`](skills/best-ex-check) | TCA an execution against NBBO at trade time | Designed |
| [`t+1-settlement-prep`](skills/t+1-settlement-prep) | Walk a settlement calendar against a position file | Designed |

## Setup

1. Grab a [Massive API key](https://massive.com/pricing). The free Basic
   tier runs five of the skills end to end. Paid plans start at $29.
2. Clone into your Claude Code skills directory:
   ```bash
   git clone https://github.com/rgourley/finance-playbook.git ~/.claude/skills/finance-playbook
   ```
3. Set the key:
   ```bash
   export MASSIVE_API_KEY=your_key_here
   ```
4. In Claude Code, invoke any skill with `/<skill-name>` or describe what
   you want and Claude will pick.

## Trying earnings-drilldown right now

This is the one skill that's actually implemented. To run it directly:

```bash
git clone https://github.com/rgourley/finance-playbook.git
cd finance-playbook
pip install -r requirements.txt
export MASSIVE_API_KEY=your_key_here
python3 examples/run-aapl-tier-b.py    # AAPL preview, Tier B (free SEC EDGAR + Stocks Starter)
```

Or for any ticker:

```bash
python3 examples/run-tier-b.py NVDA
python3 examples/run-tier-b.py TSLA
python3 examples/run-tier-b.py JPM
```

Sample outputs live in `examples/`. The script writes to `examples/{ticker}-tier-b-output.md`
(gitignored) with both the canonical JSON and the rendered note.

## Why this exists

Anthropic shipped ten banker-workflow agents (pitch decks, KYC, month-end
close) without market data grounding. Those agents will confidently quote
a stock price that's a year stale because nothing in the loop hits a live
quote.

This repo is the other half: workflows where every claim cites the
endpoint and timestamp it came from. Use them standalone, wire them into
Anthropic's agents as the data layer, or fork the patterns for your own
suite.

## Plan tiers and cost

The skill matrix in [PLAN-MATRIX.md](./PLAN-MATRIX.md) tells you what
Massive plan each skill needs. Highlights:

- **Free Basic ($0):** Five skills work end to end with 5 calls/min throttle.
- **Stocks Starter ($29/m):** Eleven skills work, plus flat-file S3 access
  (included). Most users should start here.
- **Plus Benzinga Earnings ($99/m):** Unlocks Tier A of `earnings-drilldown`
  (classical beat/miss bucketing with consensus EPS). Tier B works without it.
- **Stocks Advanced ($199/m):** Real-time and WebSocket streaming.

`earnings-drilldown` specifically supports two tiers:

- **Tier A** (Stocks Starter + Benzinga Earnings, ~$130/m): full fidelity
  with consensus EPS, surprise %, classical beat/miss bucketing
- **Tier B** (Stocks Starter alone, $29/m): SEC EDGAR for press release
  dates (free), Massive for prices. Substitutes reaction-sign bucketing
  for beat/miss bucketing. Production-credible for trading workflows.

## License

MIT. Fork it, ship it, charge for it. Attribution appreciated, not required.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). Open a PR with a new skill or an
improvement to an existing one. The audit script enforces the shape; the
methodology references are where the IP lives.
