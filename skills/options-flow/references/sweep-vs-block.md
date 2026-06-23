# Sweep vs block

The two flavors of large options prints carry different reads. A sweep
is urgent; a block is negotiated. Both worth surfacing, both with their
own bias.

## Definitions

### Sweep

A sweep is a market order that hits multiple exchanges simultaneously to
fill a target size. The trader took whatever liquidity existed across
venues rather than waiting for one venue to fill them. The hallmark is
multiple fills, same millisecond, different exchanges, same direction.

In Massive's trade conditions field, condition `219` is `Intermarket
Sweep Order` (ISO). Any trade tagged with 219 is a single leg of a
sweep. To reconstruct the full sweep, group ISO prints on the same
contract within a tight time window (typically <100ms) into one
synthetic sweep print.

**The trader's read:** sweeps signal urgency. The trader needed to be
in NOW and was willing to give up best-execution to get filled fast.
That's information. Sweeps disproportionately precede news, catalyst-
adjacent moves, or hedged exposures someone wants flat by close.

### Block

A block is a single large print, usually negotiated upstairs or filled
through a single venue's auction. The size is unusually large but the
print is one fill on one exchange. In Massive's conditions field, a
block doesn't have a unique tag like `219`; it's identified by size
alone above a per-ticker threshold.

**The trader's read:** blocks signal negotiation. The size was big
enough that the trader couldn't sweep liquidity (would have moved the
market) so they worked with a market maker or broker to find the other
side. The price is often at-mid (split the spread) because both sides
agreed on fair value. Less urgent than a sweep, but the size is the
signal.

### Other

Qualifying prints that aren't sweeps and aren't blocks. Usually
high-volume contracts that built up size through many small trades.
Less directionally informative because the volume doesn't represent any
single trader's conviction.

## Massive trade conditions reference

| Condition | Name | Skill treatment |
|---|---|---|
| 209 | Automatic Execution | Standard fill, neither sweep nor block |
| 219 | Intermarket Sweep Order | **Sweep** |
| 227 | Single Leg Auction Non ISO | Standard, possibly block-eligible by size |
| 228 | Single Leg Auction ISO | Sweep-eligible (ISO marker) |
| 229 | Single Leg Cross Non ISO | Cross, possibly block by size |
| 230 | Single Leg Cross ISO | Sweep-eligible |
| 231 | Single Leg Floor Trade | Floor, possibly block by size |
| 232-240 | Multi Leg / Stock Options | Multi-leg, excluded from headline stream |
| 201, 203, 205, 207 | Canceled variants | Excluded entirely |
| 202, 204, 206 | Late / Out Of Sequence | Excluded entirely |

The skill checks for condition `219`, `228`, or `230` to mark a print as
sweep-eligible. To upgrade from "eligible" to confirmed sweep, the print
needs at least 2 ISO fills on different exchanges within a 500ms window
on the same contract.

Single-fill ISO prints are tagged as `sweep` with a `single_leg: true`
note in the JSON; multi-fill ISO clusters are tagged as `sweep` with
`leg_count: N`.

## Block size thresholds

Block size is per-ticker. Defaults:

| Ticker | Block threshold (contracts) |
|---|---|
| SPY / QQQ | 500 |
| AAPL / TSLA / NVDA / META | 200 |
| AMZN / GOOGL / MSFT | 150 |
| Top-100 mid-cap | 100 |
| Other | 50 |

These are tuned so blocks represent 0.05-0.10% of the contract's average
daily volume rather than the same absolute number on every ticker. For
new tickers, compute the threshold dynamically as `max(50, 0.0005 *
avg_30d_volume)`.

## Why the read differs

Same dollar premium, different reads:

- **$1M sweep on TSLA $310C, 14x avg**: someone wanted exposure NOW.
  Likely news or catalyst the seller doesn't have access to. Bullish at
  the per-print level if filled above ask.
- **$1M block on TSLA $310C, 14x avg, at mid**: someone built a position
  with a market maker. Both sides agreed it's fair. Could be hedging,
  could be an upstairs cross from a fund rebalance, less informative
  about urgency.

The block is louder but less surprising. The sweep is smaller in market
impact but more informative about the trader's view.

## Rendering implication

Both kinds render the same way in the stream output: `SWEEP` or `BLOCK`
tag on line 1. The difference shows up in the trader's read of the
output, not the format. A user scanning the stream notices `SWEEP @
ABOVE ASK` faster than `BLOCK @ MID` because the kind+side pair signals
urgency level at a glance.

## What v1 doesn't do

- Reconstructing multi-leg strategies (spreads, condors). Conditions
  232-240 tag the legs but not the strategy.
- Detecting "stock + options" hedged trades. Conditions 237-245 tag
  these, but linking the equity print to the options print requires
  matching on sequence numbers, which the v1 skill skips.
- Surfacing "after-hours" or pre-open prints. The skill only looks at
  regular session prints.

All three are valid v2 extensions.
