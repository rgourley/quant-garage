# Choosing the exit multiple

Reverse-DCF and the MC fan-of-outcomes both need an exit multiple to
discount back. Picking the right one matters more than the precision of
the math. A 15x EV/EBITDA assumption on a company with negative EBITDA
is not "slightly wrong" — it produces nothing.

## The default rule

`--multiple auto` (the default) reads the subject's TTM EBITDA:

- **EBITDA > 0** → use **EV/EBITDA**. Standard for mature, profitable
  businesses where earnings are the cleanest summary of intrinsic value.
- **EBITDA ≤ 0 (or missing)** → use **EV/Sales**. Standard for biotechs,
  early-stage SaaS, hardware companies still scaling, and any name whose
  current GAAP profitability doesn't reflect long-term economics.

This is the industry convention, not a heuristic. Sell-side analysts
covering Vertex Pharmaceuticals quote EV/EBITDA. The same analysts
covering Allogene Therapeutics quote EV/Sales. The choice tracks the
company's stage, not analyst preference.

## The formula difference

EV/EBITDA mode (3 drivers in MC):

```
fair_ev_horizon = revenue_ttm × (1 + g)^h × margin × exit_multiple
```

The margin term is what makes EV/EBITDA an earnings multiple — it
converts the revenue trajectory into the EBITDA the multiple applies to.

EV/Sales mode (2 drivers in MC):

```
fair_ev_horizon = revenue_ttm × (1 + g)^h × exit_multiple
```

Margin drops out entirely. The exit multiple is applied directly to
horizon revenue. Sensitivity analysis in MC mode reflects this: the
ranked drivers are `growth` and `exit_multiple` only.

## Forcing the choice

`--multiple ev_ebitda` or `--multiple ev_sales` overrides the
auto-selection. Useful when:

- A mature profitable company's reported EBITDA is temporarily depressed
  (legal settlement, write-down, one-time impairment) and you want the
  EV/Sales path to bypass the noise. `--multiple ev_sales` works.
- A pre-profitability company is about to inflect (e.g., late-stage
  oncology biotech with PDUFA in 90 days) and you want to model the
  forward EBITDA explicitly. `--multiple ev_ebitda` with peer median
  margin from comparable approved drugs.

The override surfaces in JSON as `reverse_dcf.exit_multiple_source =
"user_override"` so consumers see that the choice was intentional, not
automatic.

## What the output tells you

The reverse_dcf JSON block carries:

- `exit_multiple_kind`: `"ev_ebitda"` or `"ev_sales"` (which formula ran)
- `exit_multiple_source`: `"auto_ebitda_positive"`,
  `"auto_ebitda_nonpositive"`, or `"user_override"` (why that path was
  chosen)
- `exit_multiple_label`: `"EV/EBITDA"` or `"EV/Sales"` (for rendering)
- `exit_multiple_assumption`: the actual peer-median multiple value used

In MC mode the same fields appear on the `monte_carlo` block plus a
`drivers_used` map showing which drivers were sampled. EV/Sales mode
omits `margin` from `drivers_used` because it isn't in the formula.

## Caveats

- The auto rule is binary. It doesn't model the gray-zone case of a
  company with positive but tiny EBITDA (e.g., 1% margin on $1B
  revenue). For those, EV/Sales is usually still the right pick;
  consider `--multiple ev_sales` explicitly.
- Cash-rich early-stage companies that have meaningful interest income
  inflating reported "EBITDA" (which technically excludes interest, but
  some standardized feeds include it) will tip the auto rule wrong.
  Check the raw subject EBITDA value in the output if the choice looks
  off.
- EV/Sales assumes peer growth × peer exit-multiple is a reasonable
  representation of the subject. For pre-revenue companies (truly
  zero-revenue clinical-stage biotechs), neither multiple works; the
  valuation is option-theoretic and outside this script's scope.
