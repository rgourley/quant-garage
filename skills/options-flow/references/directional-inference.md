# Directional inference

How to read whether a print is a buy or a sell, then translate that
into bullish/bearish per option type. The skill emits both the raw
microstructure tag (`price_vs_nbbo`) and the higher-level read
(`inferred_direction`) so a UI can show either.

## The microstructure rule

Compare the trade price to the NBBO (national best bid/offer) at the
time of print:

| Trade price relative to NBBO | Tag | Read |
|---|---|---|
| > ask | `above_ask` | Aggressive buy, lifted the offer |
| = ask | `at_ask` | Standard buy at offer |
| between bid and ask, near mid | `at_mid` | Negotiated, both sides agreed |
| = bid | `at_bid` | Standard sell at bid |
| < bid | `below_bid` | Aggressive sell, hit the bid |

The "at_mid" tolerance is ±2¢ from the mid for liquid contracts (wide
spread $0.50+), tightened to ±1¢ for tight-spread contracts (under
$0.10 spread). Outside that band, the print is tagged at_ask, at_bid,
or one of the aggressive variants.

## Why this works

Options trade where the trader is willing to transact. A buyer who
lifts the offer is paying a premium for immediacy: they value being
filled now more than the few pennies of price improvement they'd get at
the mid. A seller who hits the bid is taking what's available: they
value getting out now over a few pennies. Aggressive prints carry
information; passive prints carry less.

At-mid prints are usually negotiated upstairs. The market maker
quoted both sides a fair price and the trade filled at the midpoint.
Less directionally informative; both parties agreed on fair value.

## Translating to bullish / bearish

The `inferred_direction` field combines `price_vs_nbbo` with the
contract's `type` (call vs put):

| Type | NBBO tag | Direction |
|---|---|---|
| call | above_ask | bullish |
| call | at_ask | bullish |
| call | at_mid | neutral |
| call | at_bid | bearish |
| call | below_bid | bearish |
| put | above_ask | bearish |
| put | at_ask | bearish |
| put | at_mid | neutral |
| put | at_bid | bullish |
| put | below_bid | bullish |

The intuition: an aggressive call buyer wants the stock to go up; an
aggressive put buyer wants the stock to go down. The aggressive seller
in either case has the opposite view (or is closing a position; the
skill doesn't distinguish).

## Caveats

### Hedging muddles the signal

A market maker selling calls might hedge by shorting stock. They're
not bullish or bearish on the underlying; they're delta-neutral. The
flow looks bearish on stock (the hedge) and bearish on calls (the
sell), but neither reflects a directional view.

The skill can't tell hedge from speculation without seeing the full
trader portfolio. The `inferred_direction` tag is a per-print read,
not a "smart money is short" claim. Aggregated reads across many prints
on the same name are more reliable than single-print reads.

### Spreads change the read

A trader buying a $300/$310 call spread is buying the $300 (aggressive
buy on calls) and selling the $310 (aggressive sell on calls). Looked
at independently, the two legs cancel out. The skill in v1 surfaces
both legs as separate prints; users have to know that the call sell
isn't necessarily bearish.

Multi-leg conditions (232-240) flag spread fills, but v1 doesn't link
the legs into a single strategic position. v2 candidate.

### At-mid prints with a wide spread

For a contract quoted $4.50 / $5.00 (50¢ wide), an at-mid print at
$4.75 is genuinely neutral. For a contract quoted $4.50 / $4.51 (1¢
wide), the band is so tight that "at_mid" is meaningless; treat as
at_ask or at_bid based on which side the trade is closer to. Already
handled in the tolerance logic above.

### Late-day prints

The last 30 minutes of the session carry expiring-week noise: dealers
unwinding, retail closing positions, end-of-day rebalances. A 3:55 PM
sweep is less informative than a 10:30 AM sweep because the late-day
flow often reflects mechanical positioning rather than informed trading.

The skill doesn't filter out late-day prints in v1, but the rendered
output includes the trade timestamp so the user can downweight late
flow themselves.

## What the take should never say

Per the rendering guide, the skill's aggregate take does NOT use the
words "bullish" or "bearish." Reasons:

- The flow shows direction at the per-print level; aggregating across
  many prints frequently averages out
- "Bullish flow" is ambiguous: bullish for the trader who took the
  position, or bullish for the underlying? Different reads.
- Action-oriented language ("buyers stepping in on calls", "puts being
  bid up") is more concrete and reads cleaner to a senior trader

But at the per-print level (the second line of each stream block), the
`BULLISH` / `BEARISH` / `NEUTRAL` tags are appropriate because they're
how traders read flow on a Cheddar Flow screen. The format the user
already consumes drives the choice.

## Sample logic in code

```python
def price_vs_nbbo(price, bid, ask):
    if bid is None or ask is None or bid <= 0 or ask <= bid:
        return "unknown"
    mid = (bid + ask) / 2
    spread = ask - bid
    mid_band = max(0.01, spread * 0.10)
    if price > ask:
        return "above_ask"
    if price >= ask - 0.01:
        return "at_ask"
    if abs(price - mid) <= mid_band:
        return "at_mid"
    if price <= bid + 0.01:
        return "at_bid"
    if price < bid:
        return "below_bid"
    # Between mid and ask, not at_ask: closer to ask than mid
    return "at_ask" if (price - mid) > 0 else "at_bid"


def inferred_direction(contract_type, nbbo_tag):
    bullish = {("call", "above_ask"), ("call", "at_ask"),
               ("put", "at_bid"),     ("put", "below_bid")}
    bearish = {("call", "at_bid"),    ("call", "below_bid"),
               ("put", "above_ask"),  ("put", "at_ask")}
    if (contract_type, nbbo_tag) in bullish:
        return "bullish"
    if (contract_type, nbbo_tag) in bearish:
        return "bearish"
    if nbbo_tag == "at_mid":
        return "neutral"
    return "unknown"
```

The skill's reference implementation uses these helpers verbatim.
