## Flag categories

Five flag types. Each carries a definition, a threshold, and a one-line
read on what it tells the operator about execution quality. A fill
that hits zero categories is dropped from the output. A fill that hits
one or more becomes a BREAK in the exception report, with all
triggered reasons listed.

Multiple flags on one fill is normal: a fill that crossed the spread,
showed high VWAP slippage, and saw 30s of adverse drift is one BREAK
with three reasons. The output groups by fill, not by reason.

## crossed_spread

**Definition.** A BUY filled above the inside ask, or a SELL filled
below the inside bid. The trader paid through the spread (or accepted
worse than the spread) instead of taking the inside.

**Threshold.** `signed_slippage_bps > 0` versus the inside reference
price (ask on BUY, bid on SELL). Any positive value counts.

**What it tells you.** The execution paid the implementation cost
beyond the spread. Sometimes a deliberate choice (taker fees waived,
or the order had to fill within a time window). Sometimes a routing
mistake (the order went to a venue that didn't have the inside).
Investigate when the bps is double-digit; below 5bps it's often
microstructure noise (the inside moved between quote print and trade
print) and not actionable.

**Tier B note.** Tier B uses the bar high as the BUY reference and
bar low as the SELL reference. A fill marginally above the bar high
is a confident crossed-spread call; a fill near the bar high (within
2bps) might be inside the actual NBBO but outside the bar. Tier B
flags conservatively, which means some marginal calls are
under-reported.

## wide_spread_at_fill

**Definition.** The bid-ask spread at the fill timestamp was wider
than 50bps. The market was uncertain about the price; the inside
quote alone doesn't tell you whether the fill was good or bad.

**Threshold.** `spread_bps_at_fill > 50`.

**What it tells you.** Less of a violation, more of a context flag.
The trader took available liquidity in a thin moment. The exception
report includes these so the analyst doesn't burn time investigating
fills that looked off-NBBO but were just inside a wide spread. A
fill with `wide_spread_at_fill` only (no other reasons) usually
resolves to "no clear fill-vs-NBBO violation; thin tape" in the
suggested-next-action field.

**Tier B note.** Bar high-low range is the proxy. A 50bp range on a
1-second bar is genuinely wide; this flag is reliable on Tier B.

## off_nbbo_buy / off_nbbo_sell

**Definition.** The fill price was outside the NBBO at the trade time.
A BUY at $100.12 when the inside ask was $100.08 (and stayed at
$100.08 across the second) is `off_nbbo_buy`. A SELL at $379.20 when
the inside bid was $379.58 is `off_nbbo_sell`.

**Threshold.** Tier A: `fill_price > reference_ask` (BUY) or
`fill_price < reference_bid` (SELL). Tier B: same comparison against
the 1-second bar high/low; the proxy is conservative so this catches
the worst cases only.

**What it tells you.** A potential Reg NMS violation. Either the
fill timestamp is wrong (broker reporting delay), the trade is a
block or dark print that's allowed to print outside the NBBO under
specific exceptions, or the routing system failed. Always
investigate. Block and dark prints can be normalized once identified
(see the `block_carveout` note in SKILL.md "Doesn't handle yet").

**Tier B note.** Strong false-negative risk on Tier B. A fill 2bps
outside the inside but inside the bar gets missed. Tier B should
be treated as a lower-bound count.

## high_vwap_slippage

**Definition.** Fill price diverged from session VWAP by more than
25bps in the unfavorable direction (BUY above VWAP, SELL below VWAP).

**Threshold.** `vwap_slippage_bps > 25` after sign flip.

**What it tells you.** The fill happened at a price meaningfully
worse than the day's volume-weighted average. Sometimes appropriate
(the trader was filling against a specific catalyst); sometimes a
signal of poor timing or aggressive execution. Useful in combination
with other flags. A fill that's both `crossed_spread` and
`high_vwap_slippage` is consistent with "trader paid up in a
disadvantaged direction"; a fill with only `high_vwap_slippage` and a
normal spread is consistent with "trader took available liquidity at
a sub-optimal moment."

## adverse_selection

**Definition.** Price moved against the trader in the 30 seconds
after the fill. A BUY followed by price decline, or a SELL followed
by price rise, suggests the counterparty had information the trader
didn't.

**Threshold.** `adverse_selection_bps > 5`. Set deliberately low
because adverse selection is a probabilistic signal; you want to
flag the fills where price drifted, not require a large move. Five
bps in 30 seconds on a large-cap is well above microstructure noise.

**What it tells you.** A signature of being on the wrong side of an
informed flow. Single instances are noise; patterns across many
fills (especially from one venue or one counterparty) suggest a
routing problem or a leakage problem. The skill flags individual
fills; the operator looks for clusters.

See [`adverse-selection.md`](./adverse-selection.md) for the
measurement window and the signature pattern.

## How flags combine

A fill can hit any combination. The output lists all triggered
reasons in the `reasons` array, in the order:
`crossed_spread, wide_spread_at_fill, off_nbbo_buy, off_nbbo_sell,
high_vwap_slippage, adverse_selection`. The suggested-next-action
prose prioritizes the most actionable reason for that fill.

| Combo | Suggested next action |
|---|---|
| Just `wide_spread_at_fill` | No clear fill-vs-NBBO violation; trader took available liquidity in thin tape |
| `crossed_spread` only | Investigate venue routing; price improvement opportunity missed |
| `crossed_spread` + `high_vwap_slippage` | Investigate execution timing and venue choice; cost was material to portfolio |
| `off_nbbo_buy` or `off_nbbo_sell` | Trade printed outside NBBO; verify timestamp accuracy and check for block/dark print carveout |
| `adverse_selection` only | Counterparty likely had information; track venue and counterparty for pattern |
| `adverse_selection` + `crossed_spread` | Paid up into adverse flow; classic toxic-fill pattern; track venue |

The rendering rules in `references/rendering.md` use these to
generate the `Suggest:` line per BREAK.
