## NBBO proxy via 1-second aggregates

When historical quote data isn't entitled on the operator's key,
`/v3/quotes/{ticker}` returns 403 and the skill can't get the
microsecond-precise inside quote at the fill timestamp. The Tier B
fallback uses 1-second aggregate bars as a quote-data proxy. Defensible
but lossy: you lose intra-second precision, you lose the actual quote
prints (you see only the trade-derived bar), and you lose the
exchange-level inside (you see the consolidated tape's range).

This file documents the proxy construction, the loss of precision, and
the consequences for each flag category.

## The proxy

For a fill at timestamp T, pull
`GET /v2/aggs/ticker/{ticker}/range/1/second/{T-1}/{T+1}`. The response
returns one bar per second with `o`, `h`, `l`, `c`, `v`, `vw`, `n`. The
second bar straddling T has `t = floor(T) * 1000` in milliseconds.

The reference NBBO at T becomes:

- `reference_bid = bar.l` (low of the [T, T+1s] window)
- `reference_ask = bar.h` (high of the [T, T+1s] window)
- `reference_mid = (bar.l + bar.h) / 2`

Sign-adjusted for slippage:

- BUY fill compares to `reference_ask` (paying the offer is normal)
- SELL fill compares to `reference_bid` (hitting the bid is normal)

If the fill price is outside the [bar.l, bar.h] range, the proxy says
the fill traded outside the second-bar's trading range, which is a
strong signal of an off-NBBO print or a timestamp mismatch. This is
not as precise as Tier A (which would catch a fill that landed inside
the bar range but outside the actual NBBO at that microsecond) but it
catches the worst violations.

## What you lose vs Tier A

| Capability | Tier A (v3/quotes) | Tier B (1s aggs) |
|---|---|---|
| Resolution | microsecond | one-second bar |
| Inside bid/ask | actual NBBO | bar low/high (proxy) |
| Crossed-spread detection | exact | approximate |
| Off-NBBO detection | exact | only catches fills outside the bar's trading range |
| Wide-spread context | inside-quote spread in bps | bar high-low range in bps |
| Adverse-selection signal | quote-driven | trade-driven (still works; the 30s post-fill window aggregates the same way) |
| VWAP slippage | unaffected (uses minute aggregates either way) | unaffected |

The biggest practical loss is **off-NBBO detection**. Tier A catches a
$100.10 buy when the inside ask was $100.08; Tier B sees the same fill
inside the [$100.05, $100.12] one-second bar and doesn't flag it. The
operator should treat Tier B off-NBBO counts as a lower bound, not an
exhaustive list.

## When the proxy is fine

For most TCA workflows, Tier B is sufficient:

- **VWAP slippage** is computed from minute aggregates, independent of
  the quote source. Tier B is identical to Tier A here.
- **Crossed-spread** detection catches the egregious cases (fill 20+bps
  outside the bar) cleanly. The subtle cases (fill 2bps outside the
  inside but inside the bar) get missed.
- **Adverse selection** uses post-fill aggregates regardless of tier.
  Tier B is identical here too.

If the operator's compliance review specifically asks "did any fill
print outside the NBBO," upgrade to Tier A or pull flat-files
`quotes_v1` for the day. Tier B can't answer that question with
microsecond precision.

## Assumptions

- The fill timestamp in the input CSV is accurate to one second or
  better. A fill timestamped to the minute (no seconds) collapses to
  the first second of that minute, which biases the proxy bar.
- Tape A/B/C consolidation: the 1-second aggregate is the
  consolidated print across all exchanges. The inside quote at a
  specific exchange may differ from the consolidated range. The proxy
  treats the consolidated range as the NBBO proxy, which is the
  conservative choice (wider range = harder to flag a violation).
- The bar is sealed after the second ends. A fill at T = 14:30:00.500
  uses the bar at t = 14:30:00.000 (which covers T = 14:30:00 to
  14:30:00.999). If the fill is at the very edge of a second, the
  proxy can miss inside-bar movement from the prior second. This is a
  known limitation; for fills exactly on second boundaries, pull both
  the prior and current bar and use the wider range.

## Implementation note

Always emit `quote_source: "1s_aggs_proxy"` in the JSON when Tier B is
active so downstream consumers see the precision level. Tier A emits
`quote_source: "v3_quotes"`. The two values drive different UI
treatment (Tier B flagged fills carry a "proxy" badge so the analyst
can decide whether to escalate to a flat-files pull).
