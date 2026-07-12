# Methodology: insider-flow

The compute layer sits in `quant_garage/skills/insider_flow.py`. This
file explains the choices, not the code.

## Signal versus noise

Not every Form 4 row means anything. A CEO exercising vested options
and immediately selling to cover taxes is not a bearish signal; it's
mechanical. A director's Rule 10b5-1 sale scheduled six months ago
is not a bearish signal; it's a plan. A grant of RSUs is not a
bullish signal; it's compensation.

This skill's core design choice is to filter aggressively for signal
and label the noise so the reader sees what got filtered and why.
See [`transaction-codes.md`](./transaction-codes.md) for the full
mapping.

## The 10b5-1 filter

Rule 10b5-1 was introduced in 2000 to give insiders an affirmative
defense against insider-trading charges when they trade on a
pre-committed schedule. In practice, most insider sales at large
public companies happen under 10b5-1 plans. The `aff_10b5_one` boolean
from Massive is exactly the piece of information needed to isolate
those:

- `aff_10b5_one=true` on a sale: pre-scheduled, near-neutral signal.
  Filter from the sentiment computation.
- `aff_10b5_one=false` on a sale: discretionary decision. Includes
  in the sentiment computation.

A cottage industry of skepticism exists around 10b5-1 plans. Some
plans are adopted immediately before a material corporate event
(e.g., a plan adopted 30 days before a disappointing earnings print
that then sells into the aftermath). This skill doesn't detect that
pattern; plan-adoption date is in filing footnotes as unstructured
text. A future extension could parse it out.

## Cluster buy detection

**Window: 14 days. Minimum insiders: 2. Minimum dollars: $100k.**

Cluster buys (multiple insiders buying open-market within a short
window) are one of the highest-informativeness signals in Form 4
data. Empirical literature (Cohen, Malloy, Pomorski 2012, *Decoding
Inside Information*; Jeng, Metrick, Zeckhauser 2003, *Estimating the
Returns to Insider Trading*) finds that clustered non-routine buys
outperform the market meaningfully, while individual buys and any
sales have much weaker predictive power.

The window and thresholds are judgment calls, not statistical
optimums:
- 14 days matches the typical settlement window for a "coordinated"
  buy decision. Officers rarely coordinate explicitly (illegal); they
  react to the same signal (a stock decline, a positive internal
  data point) within a similar timeframe.
- 2 insiders is the minimum count that constitutes a cluster.
  Increasing to 3 loses too many real signals for small companies
  where only 2-3 insiders would ever buy at once.
- $100k dollar floor filters out cluster patterns where insiders each
  bought $500 as a token gesture. Real conviction buying starts at
  five-figure sums.

The walk is greedy: after detecting a cluster, the pointer advances
past the last row of that cluster before starting a new window. This
avoids reporting the same cluster twice under overlapping windows.

## Sentiment buckets

On net conviction dollars (buys minus discretionary sales, 10b5-1
sales excluded):

| Bucket | Condition |
|---|---|
| `strong_bullish` | cluster detected AND net > 0, OR net >= +$250k |
| `bullish` | net >= +$50k |
| `neutral` | -$250k <= net <= +$50k |
| `bearish` | net <= -$250k |
| `strong_bearish` | net <= -$1M |

Asymmetric on purpose. The threshold for "bullish" is much lower
than "bearish" because open-market buys are rarer and each one
carries more information. A $50k buy from a director is a bullish
signal; a $50k sale is noise.

## What this doesn't measure

- **Base rate.** No per-name "typical 6-month discretionary sale
  volume." A $2M sale is different for JPM than for MU. Queued for
  a follow-up: normalize dollars against 30-day ADV × close.
- **Price context.** No overlay of transaction date vs stock price.
  Insider buys near 52-week lows are stronger; sales at highs are
  weaker. Queued as a chain with `technical-briefing`.
- **10b5-1 plan adoption date.** Adopted-right-before-a-print plans
  are themselves a red flag; not detected here.
- **13-D / 13-G triggers.** Activist accumulation happens on a
  different form; not covered.
- **Cross-ticker roll-up.** A watchlist mode ("cluster buys across
  30 names this week") composes this skill; queued.

## Reading list

- Cohen, Malloy, Pomorski (2012), *Decoding Inside Information*.
- Jeng, Metrick, Zeckhauser (2003), *Estimating the Returns to
  Insider Trading*.
- Ke, Huddart, Petroni (2003), *What Insiders Know About Future
  Earnings and How They Use It*.
- SEC Rule 16a-3 (transaction codes).
- SEC Rule 10b5-1 (safe harbor for pre-committed trading plans).
