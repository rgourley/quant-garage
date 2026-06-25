# Reverse-DCF

The killer feature of the skill. The analyst's three-number thesis
(`target_price`, `assumed_growth`, `assumed_margin`) lands the target
in dollars. The reverse-DCF translates the **current stock price**
into the same units: at today's price, given the assumed margin and a
peer-median exit multiple, what 5-year revenue CAGR is the market
already implying? Compare to peer-median CAGR. The gap is "air in the
current price."

This is what turns "the target is $250" into "you're betting NVDA
grows 26% CAGR for 5 years, the peer median is 18%; here's how much
air sits in the current $202 price before the target even matters."

## The model

Single-stage, terminal-only DCF. Documented as a simplification.

```
PV(horizon EBITDA × exit multiple, discounted at WACC) = current EV
```

Specifically, solve for `g` (the implied revenue CAGR) such that:

```
current_EV = (revenue_TTM × (1 + g)^horizon × assumed_margin × exit_multiple)
             / (1 + WACC)^horizon
```

Rearranging:

```
(1 + g)^horizon = current_EV × (1 + WACC)^horizon
                  / (revenue_TTM × assumed_margin × exit_multiple)

g = ( ... )^(1/horizon) - 1
```

This is solved analytically (no root-finder needed) because the form
is a single power equation.

## Inputs

- `revenue_TTM`: subject's TTM revenue from the financials endpoint.
- `assumed_margin`: analyst's input.
- `exit_multiple`: peer-median **current** EV/EBITDA. The implicit
  assumption is the market keeps paying the peer-median multiple at
  the horizon. This is a strong assumption for high-multiple names
  (where mean reversion is more likely than persistence) and a weak
  assumption for low-multiple names (where peers' multiples may
  re-rate up). Documented as a simplification.
- `WACC`: hardcoded at `0.09` (9%). See "Why 9%" below.
- `horizon`: analyst's input.
- `current_EV`: subject's current EV = current market cap + long-term
  debt.

## Why 9% WACC (hardcoded)

A proper bottom-up cost of capital requires:

- **Equity beta** (5y monthly or 2y daily, against the right index).
  Computable from `/v2/aggs` but adds 250 trading days of fanout
  per name.
- **Marginal cost of debt** (yield on recent bond issuance or the
  spread on the existing debt). Not exposed by the current API set;
  would require credit-side data.
- **Equity risk premium** (implied from index DCF or historical
  average; market-dependent input).
- **Effective tax rate** (subject-specific from income statement; the
  multiples-methodology references uses a 21% statutory proxy).

A more careful WACC for a mega-cap US tech name lands in the 8.5-10%
range under current conditions. 9% is the rough cross-cap-structure
midpoint and is consistent enough that the **relative** comparison
to peer-median CAGR (which uses the same WACC implicitly) is
defensible. A clean v2 PR adds bottom-up WACC inference per name; for
v1 the hardcoded rate is documented in the JSON
(`reverse_dcf.wacc_assumption: 0.09`) so the consumer knows what was
assumed.

The implied CAGR moves with WACC: every +1pp of WACC adds roughly
+0.7-1.0pp to the implied CAGR (the price has to deliver more growth
to clear the higher hurdle rate). For NVDA at the test inputs, a
6% → 12% WACC range translates to a roughly 18% → 25% implied CAGR.
The peer-relative gap is more stable than the absolute number.

## Air in the current price

```
air_pp = (implied_cagr - peer_median_cagr) × 100
```

Positive `air_pp` means the current price requires the subject to
out-grow the peer cohort by that many percentage points of CAGR for
the price to clear the hurdle rate. Negative `air_pp` means the
current price is **discounting** the subject below peer-median growth
expectations.

For NVDA in the test case, current EV implies ~22% CAGR vs peer-
median 18%, so the air is +4pp. The analyst's $250 target adds another
4pp of CAGR on top of that (target-implied is ~26%). The skill
surfaces both gaps in the rendered note.

## fair_value_at_peer_median

The skill computes what the stock would be worth if you keep the
assumed margin but trim revenue growth to the peer median CAGR:

```
fair_revenue_horizon  = revenue_TTM × (1 + peer_median_cagr)^horizon
fair_ebitda_horizon   = fair_revenue_horizon × assumed_margin
fair_EV_horizon       = fair_ebitda_horizon × exit_multiple
fair_PV               = fair_EV_horizon / (1 + WACC)^horizon
fair_mcap             = fair_PV - long_term_debt
fair_value_per_share  = fair_mcap / shares_outstanding
```

This is the number the closing read quotes. "Trim growth to peer
median: fair value drops to $155." It anchors the discussion: even if
you defend the margin assumption, the growth assumption is what's
loading the target.

## What a full DCF would add

A more careful DCF model would:

- Project free cash flow per period (not just terminal EBITDA × exit
  multiple). Captures the timing of cash generation.
- Use a multi-stage growth model (explicit period at high growth,
  transition period, terminal stage at steady state).
- Compute terminal value as `FCF / (WACC - g_terminal)`, not as an
  exit multiple. Avoids the "peers' multiples persist" assumption.
- Net out cash and short-term investments to get true equity value.

These all add precision; none of them change the **direction** of the
gap between implied CAGR and peer-median CAGR. The skill's
simplification produces a slightly lower implied CAGR than a full DCF
for high-growth names (because all growth has to fit inside the
explicit horizon, rather than continuing in a terminal stage). The
direction of the air remains the same.

The schema records `reverse_dcf.wacc_assumption` and
`reverse_dcf.exit_multiple_assumption` explicitly so the consumer can
see what was used and either accept the simplification or recompute
with their own assumptions.

## Edge cases

- **revenue_TTM is null or zero.** No reverse-DCF possible; the
  block emits null values and the renderer skips the section.
- **Peer-median EV/EBITDA is null** (every peer has negative EBITDA,
  unlikely for a mega-cap cohort but possible for a biotech cohort).
  Fall back to peer-median EV/Sales as the exit multiple, applied to
  `target_revenue_horizon`. The renderer notes the fallback.
- **WACC × horizon is large.** The discount factor `(1+WACC)^horizon`
  can dominate; for horizon=10 at 9% it's 2.37×. The implied CAGR
  reads correctly but the absolute number can look unintuitive. The
  schema records `horizon_years` so the consumer knows what they're
  reading.
- **implied_cagr comes out negative.** The market is pricing the name
  for revenue declines. Renderer reports the number honestly; the
  take generator switches phrasing to "the current price already
  discounts revenue contraction."
