# Multiple sanity

How to compute target-implied multiples and compare them to the peer
25-75 percentile band. The target-implied multiple is the multiple the
analyst's thesis would print at the horizon, given the assumed growth
and margin. The 25-75 band is the same cohort-statistics summary as
`pitch-comps`; see
[`../../pitch-comps/references/cohort-statistics.md`](../../pitch-comps/references/cohort-statistics.md)
for the percentile methodology.

## Target-implied multiples

Three multiples are checked (matching `pitch-comps`):

### EV/Sales (at horizon)

```
target_mcap          = target_price × shares_outstanding
target_EV            = target_mcap + long_term_debt
target_revenue_horiz = subject.revenue_ttm × (1 + assumed_growth)^horizon_years
implied_ev_sales     = target_EV / target_revenue_horiz
```

The peer band is the **current** EV/Sales distribution. This is the
right comparison because the analyst's target is a today's-dollars
present value, and the peer multiples reflect today's market view of
each peer's growth and margin profile. A target that prints "31x
EV/Sales at the horizon" while the peer band is 5-9x is asking the
market to keep paying premium multiples after the growth has already
materialized.

### EV/EBITDA (at horizon)

```
target_ebitda_horiz = target_revenue_horiz × assumed_margin
implied_ev_ebitda   = target_EV / target_ebitda_horiz
```

Same peer-band comparison logic. Watch for two edge cases:

- **Subject TTM EBITDA is negative but the analyst's assumed margin is
  positive.** This is normal (the thesis is the company turns
  profitable over the horizon). The target-implied EV/EBITDA is still
  computed from `target_EV / target_ebitda_horiz`; only the **subject's
  current EV/EBITDA** (which the JSON also records for context) is
  null in that case.
- **Assumed margin is so low that target_ebitda_horiz approaches
  zero.** The implied multiple goes to infinity; the renderer caps the
  display at `>1000x` and the status flips to `above`.

### P/E (at horizon)

```
target_net_income_horiz = target_revenue_horiz × assumed_margin × (1 - tax_proxy)
target_eps_horiz        = target_net_income_horiz / shares_outstanding
implied_p_e             = target_price / target_eps_horiz
```

`tax_proxy` is hardcoded at `0.21` (US federal corporate rate). This
is a simplification: actual effective tax rates vary by sector and
geography. For US-domiciled large-caps the rate is close enough that
the relative comparison to the peer P/E band holds; the schema
documents the assumption so the consumer knows. A clean v2 PR could
infer effective tax rate from the subject's TTM income statement.

The "P/E" implied here is a forward P/E at the horizon. The peer
distribution is current TTM P/E. The comparison still works because
the question is "is the analyst pricing the horizon profitability at
a multiple this cohort actually pays" — but the renderer flags this
explicitly in the rendered note ("Implied P/E shown at horizon vs
peer current TTM").

## Comparison status

Per multiple:

| `target_implied`    | Peer band       | Status     |
|---------------------|-----------------|------------|
| inside [p25, p75]   | any             | `in_line`  |
| > p75               | any             | `above`    |
| < p25               | any             | `below`    |
| null                | null or any     | `n_a`      |

The 25-75 band is intentionally the middle 50% of the peer
distribution, not the full range. Multiples have fat tails; using
min-max would call almost every assumption "in line." The
interquartile range is the analyst-defensible "is this normal"
threshold.

## When status should be qualified

`above` is not automatically bad. Three legitimate reasons for a
mega-cap to trade above peer band:

1. **Structural margin floor.** NVDA's 60%+ EBITDA margin is
   ratio-driven by software-like data center economics; AMD and INTC
   are at 25-35%. A premium multiple is the cohort's only way to
   express the margin gap, even after the regression-adjusted view in
   `pitch-comps` controls for it.
2. **Growth profile mismatch.** Mega-cap that's still growing 30%+
   while peer cohort is at 8-15% deserves an EV/Sales premium because
   the present value of the next two years of revenue is materially
   larger.
3. **Index demand / float scarcity.** Not in the cohort distribution
   data but real. A $4T name with limited float trades at multiples
   smaller-cap peers don't access. The skill's job is to surface the
   premium, not to defend or attack it.

The take generator (see [`take-generator.md`](./take-generator.md))
phrases `above` as "stretch" only when **two or more of growth, margin,
and the multiple itself** are simultaneously above peer band. One
out-of-band reading is the cohort being wrong; two is the model being
wrong.

## Enterprise value: same simplification as pitch-comps

`EV = market_cap + long_term_debt`. Cash not subtracted (not exposed
as a named field on the financials endpoint). For target-implied EV,
the long-term debt is the subject's **current** LTD, not a projected
horizon LTD: the analyst's thesis usually doesn't change capital
structure, and even when it does, the multiple comparison is to peers
on their current EV definition.

See
[`../../pitch-comps/references/multiples-methodology.md`](../../pitch-comps/references/multiples-methodology.md)
for the full EV simplification discussion. The bias is consistent
across the subject and the peers, so the relative comparison is still
defensible.

## What's recorded in the JSON

```json
"multiple_sanity": [
  {
    "name": "ev_sales",
    "implied_value": 21.2,
    "peer_p25": 9.5,
    "peer_p50": 11.8,
    "peer_p75": 14.6,
    "status": "above",
    "n_peers_in_distribution": 6
  },
  ...
]
```

The renderer reads the entries in order (`ev_sales`, `ev_ebitda`,
`p_e`) and emits one bullet each, formatted per
[`rendering.md`](./rendering.md).
