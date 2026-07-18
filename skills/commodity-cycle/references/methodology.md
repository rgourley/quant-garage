# commodity-cycle methodology

The IP is the driver set and the way their directional effects are scored
into a single setup label. Every driver is a rolling correlation or a
relative return between the target commodity and an ETF proxy for a macro
variable.

## The pull

For every run: the target commodity ETF plus the macro context it needs.

| Ticker | Role |
|--------|------|
| target | the commodity being read (default GLD) |
| UUP | US dollar index (dollar direction) |
| TIP | inflation-protected Treasuries (real-yield proxy leg) |
| IEF | 7-10 year nominal Treasuries (real-yield proxy leg) |
| GDX | gold miners (gold only) |
| SLV | silver (gold only) |
| DBC | broad commodities (non-gold reference) |

## Correlation windows

All rolling correlations use the last `window` aligned daily simple returns
(default 60 trading days). Returns are aligned on common dates via the
shared `aligned_returns` helper, so a missing print in one series does not
smear the pairing. A correlation needs at least 3 aligned returns; below
that it is null. When fewer than `window` returns exist, the correlation
uses what is available and reports `n_obs`.

## DXY correlation

Rolling `window`-day Pearson correlation of the commodity's daily returns
vs UUP's daily returns. For gold a negative value is normal and healthy:
gold rises when the dollar falls. A value near zero means the dollar is not
currently the driver. Labels: `<= -0.3` inverse to the dollar, `>= +0.3`
moves with the dollar, else dollar-neutral.

## Real-yield proxy definition

The real-yield proxy is the **daily return spread TIP minus IEF**. TIP is
inflation-protected, IEF is nominal at a similar duration, so the spread of
their returns isolates the real-rate / breakeven component:

- Spread positive (TIP outperforming IEF): real yields falling or
  breakevens widening. Constructive for gold.
- Spread negative (IEF outperforming TIP): real yields rising. The classic
  gold headwind.

The real-yield correlation is the rolling `window`-day correlation of the
commodity's daily returns against this daily spread series. The
`real_yield_direction` field reports the sign of the spread's total return
over the window (rising / falling / flat).

## Miner-divergence rationale (gold only)

Gold miners (GDX) are a levered play on gold: their margins expand faster
than the metal moves, so in a healthy gold advance miners lead. When miners
lag the metal it is a warning that the market does not believe the move.
The read is the simple relative return over the window,
`GDX_return - GLD_return`:

- `> +1%`: miners leading, constructive.
- `< -1%`: miners lagging, a warning that confirms weakness.
- in between: miners in line.

Silver co-movement is the rolling `window`-day correlation GLD vs SLV. High
co-movement (`>= 0.6`) means a broad precious-metals move (both driven by
the same macro force); low co-movement (`< 0.3`) means the gold move is
gold-specific. Non-gold commodities skip both reads and instead get a
rolling correlation to DBC (broad commodities) so the co-move slot is never
empty.

## Quintile method

Momentum is the commodity's own total return over the window, ranked
against its trailing-year distribution of overlapping `window`-bar returns.
The series of overlapping window returns is built across all available
history, the trailing 252 samples are kept, and the latest window return is
percentile-ranked within them (shared `percentile_rank`, mean rule). The
percentile maps to a quintile: `quintile = min(5, floor(pct / 20) + 1)`, so
quintile 1 is the bottom 20% of the trailing year and quintile 5 the top.

## Constructive / neutral / headwind rule

Each driver casts a vote in `{+1, 0, -1}` with small deadbands:

- **Dollar**: directional effect = `dxy_corr * uup_return`. This is the
  correlation times the dollar's own move, so it is negative (a headwind)
  when an inversely-correlated commodity faces a rising dollar, and
  positive when the dollar is falling. `> +0.002` votes constructive,
  `< -0.002` headwind.
- **Real yields**: directional effect = `real_yield_corr * (TIP - IEF)
  return`, same construction. Positive when falling real yields help a
  positively-correlated commodity.
- **Momentum**: quintile `>= 4` votes constructive, `<= 2` headwind.
- **Miner divergence** (gold only): `> +1%` constructive, `< -1%` headwind.

Sum the votes: positive is `constructive`, negative is `headwind`, zero is
`neutral`. The **dominant driver** named in the take is the macro variable
(dollar vs real yields) with the larger absolute directional effect, so the
take reads "the dominant driver is a strong dollar (60d corr -0.71) plus
rising real yields" with the bigger effect stated first. Using the effect
(correlation times the driver's own move) rather than the correlation alone
means a strong correlation to a driver that has not moved does not
masquerade as the dominant force.

## Honest caveats

- Every driver is an ETF-return proxy, not the cash market. UUP proxies the
  dollar index, TIP minus IEF proxies real yields, GDX proxies miners. ETFs
  carry expense ratios, roll cost (DBC especially), and tracking error.
  Directionally right, not a cash-market substitute.
- Correlations are unstable. A 60-day window can flip sign around a macro
  regime change, and a single outlier day can move it. Always read a
  correlation alongside its driver's own move (the effect), never in
  isolation.
- The vote deadbands and thresholds are judgment calls tuned to the 60-day
  window, not estimated. A different window wants different thresholds.
- A rate-limited pull can drop a context ETF and flip the setup. The
  runtime flags rate-limited series loudly; never read the take without
  checking the caveats.
