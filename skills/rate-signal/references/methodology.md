# rate-signal methodology

The IP is the pair choices, the label thresholds, and the confidence rule.
Each read is a relative return between two Treasury ETFs that isolates one
curve variable, or a single-ETF direction. All are proxies for the cash
curve, chosen for liquidity and clean duration exposure.

## The basket

| Ticker | Exposure | Role |
|--------|----------|------|
| SHY | 1-3 year Treasuries | short end (the "2s" leg) |
| IEF | 7-10 year Treasuries | belly |
| TLT | 20+ year Treasuries | long end (the "10s/30s" leg) |
| TIP | inflation-protected Treasuries | real yields / breakevens |

Bond price moves inversely with yield, so a rising ETF means the yield at
that point on the curve fell.

## Window

One window (default 60 trading days) for the curve, real-yield, and
break-even reads. The momentum read also uses a short sub-window that
scales from it: `short_window = max(5, window // 4)` (15 days at the
default). History pulled is `max(window, 252) * 1.6` calendar days so the
window is always covered with margin.

## The four reads

**1. Curve slope (SHY vs TLT): the 2s10s proxy.** Relative return of the
short end versus the long end over the window. If SHY outperforms TLT, long
yields rose more than short: steepening. If TLT outperforms SHY: flattening.
Combine with the rate direction from TLT (TLT up = long rates falling =
"bull"; TLT down = long rates rising = "bear") for the four-way taxonomy:

- rates falling + flattening: bull flattening
- rates falling + steepening: bull steepening
- rates rising + flattening: bear flattening
- rates rising + steepening: bear steepening

The label uses the strict sign of each leg, so it always resolves to one of
the four (or `unknown` when a leg has insufficient history). `slope` and
`rate_direction` are exposed separately for downstream use.

**2. Real yields (TIP alone).** TIP is indexed to real yields: its price
moves inversely with the real rate. TIP up means real yields falling, TIP
down means real yields rising. Reading TIP by itself isolates the real leg
cleanly, so it is genuinely distinct from the breakeven read below rather
than the mirror image of the same spread. Threshold: +/-50bp of TIP total
return (a wider band than the breakeven pair because a single-leg absolute
return is noisier than a relative return); inside the band, stable.

**3. Break-evens (TIP vs IEF): inflation expectations.** Breakeven inflation
is the nominal yield (IEF) minus the real yield (TIP). In return terms the
change in breakevens is proportional to (TIP return - IEF return): when TIP
outperforms IEF, the real leg rallied relative to the nominal leg, meaning
breakevens widened and inflation expectations rose. IEF outperforming TIP
means expectations fell. Real yields (2) and breakevens (3) are the two
independent components of the nominal yield, not two views of one spread.
Threshold: +/-25bp of relative return.

**4. Momentum divergence (TLT vs IEF): long end vs belly.** Compares the
short sub-window direction of the long end (TLT) and the belly (IEF).
Aligned when both move the same way (the curve is shifting roughly in
parallel); divergent when the long end and the belly disagree in sign. A
divergence means the curve read from the window is being pulled two ways at
the short horizon, so the slope label deserves less weight. Signs are taken
strictly (any nonzero move counts); the `tlt_minus_ief_short_bps` field
carries the magnitude.

## Confidence rule

The four-way curve label is the headline. Confidence measures whether the
other reads corroborate it. Two agreement checks:

1. **Real yields vs rate direction.** In a bull regime (rates falling) real
   yields typically fall; in a bear regime (rates rising) they rise. If the
   real-yield direction matches the expected direction for the regime, that
   is an agreement; if it points the other way, a conflict. A stable
   real-yield read is neither.
2. **Momentum.** Aligned long-end/belly momentum is an agreement; divergent
   momentum is a conflict.

Resolution:

- **high**: no conflicts and both checks agree.
- **low**: two conflicts, or one conflict with no agreements.
- **medium**: everything in between (one agreement, or one conflict offset
  by an agreement, or neutral reads).
- **unknown**: the curve label itself is unknown (insufficient history).

The take line states the curve label, the confidence level, and the
component phrases, e.g. "Bear flattening (high confidence): long rates
rising, curve flattening, real yields rising, TLT and IEF aligned."

## Honest caveats

- Every read is an ETF-return proxy, not the cash curve. SHY/IEF/TLT/TIP
  returns move inversely with yields but carry duration convexity, roll,
  expense drag, and tracking error. Directionally right, not a substitute
  for the actual 2s10s in basis points or the real 10-year TIPS yield.
- The curve is read at four points (short, belly, long, real), not the full
  term structure. It captures the headline slope, not 2s5s, 5s30s, or
  butterflies.
- Thresholds are judgment calls, not estimated. They filter noise at the
  60-day window; a different window wants different thresholds.
- The four-way label uses relative-return sign, which conflates a steepener
  driven by the front end with one driven by the long end. The momentum
  read exists partly to flag when that conflation is doing real work.
- A rate-limited pull can drop an ETF and flip the label or the confidence.
  The runtime flags rate-limited series loudly; never read the take without
  checking the caveats.
