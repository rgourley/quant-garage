## Confidence scoring

Every emitted mark gets a confidence rating: `high`, `medium`, or
`low`. The rating drives which positions show up in the FLAGGED
exception block and how the operator weights the mark in downstream
decisions.

The model is intentionally simple: three inputs, three buckets, no
tunable knobs in v1. Operators can scan a 100-row book and trust
that anything in the table is high-confidence unless flagged.

## The three inputs

1. **Recency.** How long ago was the mark printed? Compared to the
   run's `reference_time`. Older marks are stale.
2. **Spread (bps).** Bid-ask spread in basis points: `(ask - bid) /
   mid * 10000`. Wide spreads mean the market doesn't know what the
   price is.
3. **Average Daily Volume (ADV) tier.** Top-decile, mid, or thin. Thin
   names print rarely, so even a recent print may not reflect actual
   liquidity.

## The thresholds

| Bucket | Recency | Spread (bps) | ADV tier |
|---|---|---|---|
| **High** | within 60s of reference_time | < 10 bps | top decile (>~10M shares/day for stocks; SPY/AAPL/NVDA territory) |
| **Medium** | within 5 minutes | 10–50 bps | mid (>500k/day) |
| **Low** | 5+ min stale OR no spread data OR spread > 50bps | thin (<500k/day) |

A position must clear ALL three thresholds for a bucket to qualify.
The bucket assigned is the worst of the three. A name with a 5bps
spread (high), 30s recency (high), and 200k ADV (low) is overall
**low**.

## Rationale by threshold

**60-second recency for high:** A second-by-second stream (T or AM
on the WS) should produce sub-minute marks for any actively-traded
name. If the most recent mark is more than 60s old during regular
session, something's off: the symbol is halted, illiquid in this
expiry, or the stream dropped. 60s is forgiving enough to allow a
small subscription gap without flagging every position.

**5-minute recency for medium:** This is the line where "recent
enough for a desk decision" becomes "I want to know this is the most
recent print before I trust it." A 5-minute stale mark on a midcap
in midday is plausible; the same on a megacap is a signal of trouble.
The medium bucket forces the operator to acknowledge the gap.

**10 bps for high spread:** SPY, AAPL, NVDA, and similar megacap
stocks trade with sub-3bps spreads continuously. 10 bps is wide
enough to allow normal microstructure noise (one cent on a $100
stock = 10bps) and tight enough to flag any name where the inside
market is meaningfully uncertain.

**50 bps for the floor:** Above 50bps the bid and ask are far enough
apart that the midpoint mark could be off by 25bps in either
direction. A NAV computed on 25bps mark error is not a NAV anyone
should be signing.

**Top-decile ADV for high:** Roughly the top ~400 US-listed stocks
by 30-day ADV. These names always have inside liquidity; their marks
are trustworthy by construction. Below the top decile, market-maker
attention thins and the mark can be a couple cents stale even when
the print timestamp is recent.

## Implementation note

For v1 the skill approximates ADV from the snapshot's `day.v` (today's
volume). A more rigorous version pulls `/v3/reference/tickers/{ticker}`
and uses the 30-day weighted average; that's a clean follow-up. For
the rendered output the operator cares about the bucket, not the
exact ADV number.

For symbols where the snapshot didn't return a bid/ask (some halted
names, some pre-market windows), spread is null and confidence drops
to medium automatically. If recency is also bad, it drops to low.

## What rendering does with confidence

The marked-positions table shows the bucket inline. Every row with
`confidence < high` also gets a paragraph in the FLAGGED block at
the bottom, listing the specific reason codes that triggered the
downgrade. See [`rendering.md`](./rendering.md) for the format.
