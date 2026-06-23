# Growth and profitability

How `revenue_growth_ttm` and `ebitda_margin` are computed. These two
metrics drive the regression-adjusted multiples and they're the most
common reason a regression coefficient looks wrong.

## Revenue growth (TTM)

```
revenue_ttm        = sum of revenues over the most recent 4 quarters
revenue_prior_ttm  = sum of revenues over quarters 5-8
revenue_growth_ttm = (revenue_ttm / revenue_prior_ttm) - 1
```

The 4+4 quarter approach (TTM vs prior TTM) is the standard banker
construction. It washes out seasonality (every quarter appears once in
each window) and is what CapIQ, FactSet, and Bloomberg's RV screen use.

A common alternative — using just the most recent fiscal year vs the
prior fiscal year — is cleaner conceptually but introduces a quarter
of stale data at the start of every calendar year. TTM is faster to
react and what bankers actually pitch.

### Fetching the quarters

```
GET /vX/reference/financials?ticker={tk}&timeframe=quarterly&limit=8&order=desc
```

Returns the 8 most recent quarters in reverse-chronological order. Walk
the list and pull `financials.income_statement.revenues.value` from
each row.

**The Q4 / 10-K row gotcha.** When a company files a 10-K at year-end,
the API often returns a fiscal-year-end row in the quarterly view
that has only balance-sheet data and `null` for the income statement
fields. Verified on CRM 2026-06-23:

- Q1 2027 (end 2026-04-30): full income statement ✓
- Q4 2026 (end 2026-01-31): income statement entirely `null`, balance
  sheet present
- Q3 2026: missing entirely from the quarterly view (the 10-K row
  replaces it)
- Q2 2026 (end 2025-07-31): full income statement ✓

When iterating, **drop rows where `revenues.value` is null** (don't
treat them as zero, which would tank the growth calc). The skill's
implementation collects the most recent 8 quarters that have non-null
revenue, even if the api returns more or fewer than 8 calendar
quarters in the window.

When fewer than 8 non-null quarters are available (recent IPOs,
delisted names, foreign issuers with sparse coverage), `revenue_growth_ttm`
is `null` and that peer drops out of the regression but stays in the
table.

### Calendar vs fiscal year offsets

Fiscal years differ across the comp set:

- CRM (fiscal year ends January): Q1 reports in May
- ORCL (fiscal year ends May): Q1 reports in September
- ADBE (fiscal year ends November): Q1 reports in March

The TTM construction is agnostic to fiscal calendars because it uses
the most recent 4 quarters by `end_date`, not by fiscal quarter
label. The comparison is honest: CRM's TTM ending Apr 2026 vs ORCL's
TTM ending May 2026 vs ADBE's TTM ending Mar 2026 are all "the most
recent year of revenue" within ~60 days of each other. Banker
convention is to compare TTMs as-of the most recent reported quarter
per name, exactly as v1 does.

## EBITDA margin

```
ebitda_margin = ebitda_ttm / revenue_ttm
```

Where `ebitda_ttm` is constructed per
[`multiples-methodology.md`](./multiples-methodology.md):

```
ebitda_ttm = operating_income_ttm + (depreciation_amortization_ttm or 0)
```

EBITDA margin is the single best summary statistic for "how profitable
is this business" because it normalizes by size. Software comps cluster
in the 20-50% range; healthcare in 10-30%; banks don't use EBITDA at
all.

### When D&A is null across the cohort

As verified on the 2026-06-23 CRM run, D&A is `null` for most software
peers in Massive's quarterly financials endpoint. The skill falls back
to `operating_income_ttm / revenue_ttm` (effectively operating margin)
and labels it `ebitda_margin` for table-consistency. The relative
comparison across the peer set remains valid because every peer is on
the same basis.

When the regression uses `ebitda_margin` as a control, it's really
regressing on "operating margin" for the software cohort. The
coefficient interpretation is the same direction (higher margin →
deserves a higher multiple), the magnitude is just slightly tighter
than true EBITDA margin.

### Why margin, not net income margin

Net income margin folds in tax policy, capital structure (interest
expense), and one-time items. EBITDA (or operating) margin is the
"how good is the underlying business" view. Bankers use it.

The regression uses EBITDA margin specifically because EV/EBITDA is
one of the multiples being regressed. Predicting EV/EBITDA from EBITDA
margin is mechanically tight but it's the right control: a peer with
40% EBITDA margin deserves a higher EV/EBITDA multiple than a peer with
20% margin because the cash generation per dollar of revenue is higher.

## Other metrics tracked in the JSON

`metrics` carries every input that fed a multiple:

- `revenue_ttm`, `revenue_prior_ttm`
- `revenue_growth_ttm`
- `operating_income_ttm`
- `depreciation_amortization_ttm` (the raw value, null when absent)
- `ebitda_ttm` (operating income + D&A or 0)
- `ebitda_margin`
- `diluted_eps_ttm`
- `long_term_debt`

The consumer of the JSON has every input needed to reconstruct or
recompute the multiples on a different basis (e.g. recompute EV with
cash subtracted from current_assets, or recompute EBITDA margin with
a sector-floor on D&A).

## Edge cases

- **TTM crosses a fiscal-year-end with a missing 10-K row.** Walk the
  raw quarterly list and collect non-null-revenue quarters; don't trust
  the row count.
- **Revenue restated mid-window.** Massive's endpoint reflects the
  most recent restated values. A name that announced a material
  restatement (rare for the comp universe) will see prior-period
  revenue change between runs. The `run_at` timestamp in the JSON is
  the anchor.
- **Currency.** Massive's financials are reported in the issuer's
  primary currency. For most US-listed names this is USD. Foreign
  issuers (SAP in EUR, ASML in EUR, NVO in DKK) report in their home
  currency. The skill does not convert; for ADRs the multiples are
  mathematically valid as ratios (numerator and denominator both in
  home currency), but the absolute EV doesn't match the US market cap.
  The "EV" for these peers is approximate. Documented gap; FX
  conversion is queued for v2.
- **Recent IPO with < 8 quarters of history.** `revenue_growth_ttm`
  is `null` and the peer drops out of the regression. The current-period
  multiples (EV/Sales, EV/EBITDA, P/E) still render.
- **Acquisition mid-window.** A name that closed a major acquisition
  in the TTM (e.g. ADBE if the Figma deal had closed) shows inflated
  revenue growth that doesn't reflect organic momentum. The skill
  doesn't adjust for this; the analyst is expected to know about
  pending or recent M&A and flag it in the deck.

## Why this gets its own reference

Growth and margin are the two metrics that drive the regression. The
methodology behind each — TTM vs prior TTM, D&A fallback, currency
handling — is small enough to live in the SKILL.md but high-leverage
enough that getting it wrong invalidates the regression. Pulling it
into its own file makes the contract explicit.
