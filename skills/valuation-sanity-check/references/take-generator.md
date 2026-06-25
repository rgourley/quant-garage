# Take generator

Two emissions per run: the **bold take** at the top of the note, and
the **closing read** at the bottom. Both are generated from the
structured sanity-check results, not free-form. Both use banker tone:
direct, action-relevant, no hedge words, no "may potentially."

## The bold take

One paragraph at the top of the note. Three lines, soft-wrapped.

### Line 1: translate the thesis

State what the target requires in growth/margin/horizon terms:

```
Target requires {ticker} growing {growth_pp}% CAGR for {horizon}
years at {margin_pp}% EBITDA margin.
```

This is the analyst's own thesis, restated. No data lookup; just
echoes the inputs in plain English. The point is to make the
assumption concrete before showing the gap.

### Line 2: the peer-median anchor

State where peers sit on the same two dimensions:

```
Peer median is {peer_growth_pp}% / {peer_margin_pp}%.
The assumption is {growth_delta_pp_signed}pp ahead/behind on
growth and {margin_delta_pp_signed}pp ahead/behind on margin.
```

Use signed pp deltas (+8pp, −3pp). The reader knows the magnitude
without doing arithmetic.

### Line 3: the verdict

Direction matters more than count. A multiple sanity check firing
`below` is **good news for the target** (the target works at sub-cohort
multiples). Firing `above` is **bad news for the target** (the target
demands premium multiples). A growth or margin check firing `above` is
**ambitious** (the analyst is more bullish than the cohort, which has
to be defended).

The verdict line is keyed off two counts:

```
stretched    = (mult ABOVE band) + (growth ABOVE) + (margin ABOVE)
conservative = (mult BELOW band) + (growth BELOW) + (margin BELOW)
```

| Pattern                                  | Verdict template                                                                                                 |
|------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| `stretched >= 3, conservative == 0`      | "Defensible only if you believe the structural moat plus the growth/margin gap vs the cohort both persist."     |
| `stretched >= 2, conservative <= 1`      | "Defensible if you accept the {dim1 premium} and {dim2 premium}."                                                |
| `conservative >= 3, stretched == 0`      | "Target sits at or below the peer cohort on every valuation lens; the math is conservative against the comp set."|
| `conservative >= 2, stretched <= 1`      | "Target's implied multiples sit at or below the cohort; the thesis is undemanding for the growth and margin assumed."|
| `stretched + conservative >= 3`, mixed   | "Mixed read: some assumptions stretch vs the cohort, others sit conservative."                                  |
| `stretched + conservative == 0`          | "Assumptions sit inside the peer cohort on every dimension."                                                    |
| else                                     | "Mostly in line with the cohort; one dimension carries the gap."                                                 |

`{dim premium}` reads as `multiple premium`, `growth premium`, or
`margin premium`. The take generator picks the firing dimensions
and orders them by dimension index.

Why direction matters: a target of $250 that implies EV/Sales of 7x
at the horizon while peers trade at 13-23x today is a **conservative**
target. The model isn't demanding a premium multiple; it's asking the
market to trim the multiple as the growth materializes. Same target
implying 25x EV/Sales while peers trade at 13-23x is a **stretched**
target. Count-only logic confuses these.

## The closing read

One or two sentences at the bottom. Anchored on the `fair_value_at_peer_median`
calculation from the reverse-DCF: if you trim assumptions to peer
median, where does the target land?

```
Read: Model assumes {ticker} outperforms peers by a wide margin on
every defensible dimension. Even if you trim growth to peer-median
and keep the margin premium, fair value drops to ~${fair_value}.
The target is a thesis, not a sanity-check survivor.
```

The bold action language is intentional:

- **"wide margin of safety"**: when 0 out-of-band checks and the
  reverse-DCF implies CAGR < peer median.
- **"stretch"**: when 1-2 out-of-band checks and the target-implied
  multiple is above p75.
- **"defensible"**: when 0-1 out-of-band checks and the
  target-implied multiple is in_line or close to it.
- **"thesis, not a sanity-check survivor"**: when all three checks
  are above band AND the reverse-DCF implies > +5pp of air.

### Read templates by verdict

The closing read is anchored on three comparisons: the
`fair_value_at_peer_median` vs the current price, vs the target
price, and the `stretched / conservative` counts.

**Fair value above target** (cohort math supports more than the
analyst's number):

```
Read: Even at peer-median growth and the assumed margin, fair value
lands at ~${fair_value}, above the target. The target understates
what the cohort math would support; check whether the analyst's
exit-multiple assumption is too conservative or the growth
assumption is too low.
```

**Fair value between current and target** (defensible cushion):

```
Read: At peer-median growth (same margin) fair value is ~${fair_value},
between current and target. The cushion to target is real but rests
on the analyst's growth premium vs the cohort delivering.
```

**Thesis stretched, fair value below current** (target is a thesis):

```
Read: Model assumes {ticker} outperforms peers by a wide margin on
every defensible dimension. Trim growth to peer-median and fair
value drops to ~${fair_value}, below the current price. Target is
a thesis, not a sanity-check survivor.
```

**Conservative target** (`conservative >= 2, stretched == 0`):

```
Read: Assumptions sit conservative against the cohort. Fair value
at peer-median growth and the assumed margin is ~${fair_value}; the
target is defensible.
```

**In-cohort default**:

```
Read: Assumptions cluster around the cohort. Fair value at
peer-median growth (same margin) is ~${fair_value}; the target lives
or dies on whether the standout dimension delivers.
```

## What never appears in the take or read

Banned phrasing (same as `earnings-drilldown`):

- "mispriced upside/downside" — ambiguous
- "potentially", "arguably", "may" — hedge words
- "bullish", "bearish" — directional, not action
- "fair value" appearing as a stale anchor without the actual
  calculation behind it

The take is the trade. If the user can't act on it, the wording is
wrong.
