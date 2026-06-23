# Multiples methodology

How each multiple is defined, when it's useful, when to skip it, and
the specific Massive-data simplifications the skill ships with. Read
this before adding a new multiple or changing how EV is calculated.

## The multiples in v1

| Multiple   | Numerator           | Denominator         | Best for                                                |
|------------|---------------------|---------------------|---------------------------------------------------------|
| EV/Sales   | Enterprise value    | TTM revenue         | High-growth / pre-profit names where earnings are noisy |
| EV/EBITDA  | Enterprise value    | TTM EBITDA          | Mature operating businesses, cross-capital-structure    |
| P/E        | Price (per share)   | TTM diluted EPS     | The default sell-side benchmark; falls over with losses |

These three cover the standard banker comp page. The matrix extends
cleanly to P/B (book value), P/FCF (free cash flow), and PEG
(P/E divided by growth), all of which are queued for v2 (see SKILL.md
"doesn't handle (yet)").

## Enterprise value (EV)

The textbook definition:

```
EV = market_cap
   + total_debt              (short-term + long-term)
   - cash_and_equivalents
   - short_term_investments
   + minority_interest        (book value)
   + preferred_stock          (book value)
```

The v1 implementation uses a simplification driven by what Massive's
financials endpoint exposes today:

```
EV (v1) = market_cap + long_term_debt        (if long_term_debt is reported)
EV (v1) = market_cap                          (if long_term_debt is null)
```

What's skipped, and why:

- **Short-term debt** (`current_debt`): consistently null in the
  endpoint for software comps. Including only LTD biases EV slightly
  low for companies with revolver draws; for the software cohort the
  effect is < 2% of EV and doesn't change the comp ranking.
- **Cash and short-term investments**: not exposed as named fields.
  `current_assets` includes cash but also receivables and inventory,
  so subtracting it would overstate net debt. The skill leaves cash
  in, which biases EV slightly **high** for cash-rich names. For the
  software cohort (CRM, ORCL, ADBE all hold $10B+ cash), this is a
  real bias of 5-10% of EV. **Documented and acknowledged.** True
  net-debt EV requires parsing the raw XBRL filing referenced in
  `source_filing_file_url`; left as a v2 extension.
- **Minority interest and preferred stock**: not exposed as named
  balance-sheet fields. Effect is < 1% of EV for most large-caps.

The simplification is consistent across the subject and the peers,
so the relative comparison is still defensible: every name in the table
is on the same EV basis. The absolute EV for a single name will differ
from CapIQ / Bloomberg by 5-10%; the **ratio** between names will not.

The skill records `enterprise_value` in the JSON and the rendered
table; documenting the simplification in the SKILL.md "doesn't handle
yet" section is the contract with the consumer.

## EV/Sales

```
ev_sales = enterprise_value / revenue_ttm
```

Best for high-growth or pre-profit names where earnings-based multiples
are unstable. Software comps trade in the 5-15x range; consumer-staples
trade in 1-3x; financials don't use EV multiples at all (use P/B
instead).

**Skip when**: `revenue_ttm` is null. Should never be the case for any
US large-cap with reported financials.

## EV/EBITDA

```
ebitda_ttm = operating_income_ttm + depreciation_amortization_ttm
ev_ebitda  = enterprise_value / ebitda_ttm
```

The de facto industry-standard multiple. Capital-structure-neutral
(EV is in the numerator), removes the noise of D&A and tax policy
(EBITDA is pre-both).

**The D&A gotcha.** Massive's financials endpoint returns
`depreciation_and_amortization` as a named income-statement field
inconsistently. Verified on 2026-06-23:

- CRM Q1 2027: D&A = $985M ✓
- NOW Q1 2026, all 8 trailing quarters: D&A = `null`
- CRWD all 8 quarters: D&A = `null`
- ADBE all 8 quarters: D&A = `null`
- INTU all 8 quarters: D&A = `null`

For most software comps, D&A is small (5-10% of revenue, mostly
amortization of capitalized contract acquisition costs and acquired
intangibles). Operating income + null D&A is a reasonable EBITDA
proxy: it's closer to EV/EBIT than true EV/EBITDA, and the resulting
multiple sits slightly higher than CapIQ's EV/EBITDA (because the
denominator is smaller without D&A added back).

The skill's implementation:

```python
ebitda_ttm = operating_income_ttm + (depreciation_amortization_ttm or 0)
```

When D&A is null for the entire peer set (common for software),
EV/EBITDA across the table is consistent in being "EV/EBIT proxy"
rather than true EBITDA. The relative comparison remains valid.
The schema's `metrics.depreciation_amortization_ttm` records whether
D&A was found, so the consumer knows.

**Skip when**: `ebitda_ttm <= 0` (negative operating income; the
multiple is meaningless). Verified on the 2026-06-23 CRM comp run:
CRWD's TTM operating income is negative, so its `ev_ebitda` is
`null` in the table.

## P/E

```
p_e = price / diluted_eps_ttm
```

The most widely-quoted multiple in retail and sell-side coverage.
EPS-based, equity-only (price, not EV).

**Skip when**: `diluted_eps_ttm <= 0`. Same as EBITDA: a negative
denominator produces a nonsense ratio. The skill emits `null` so the
table renderer shows `n/a` and the summary stats drop it.

**TTM EPS** is the sum of the most recent 4 quarters' diluted EPS,
not just the most recent quarter annualized. When any of the 4 quarters
has `null` EPS (e.g. a 10-K filing without per-quarter EPS broken out),
TTM EPS is null and the multiple is null. Verified for CRM: the
fiscal-year-end Q4 row has `null` EPS on the income statement (10-K
summary row), so the skill walks the quarterly view and uses Q1
2027 + Q4 2026 (when present) + Q3 2026 + Q2 2026. When a quarter is
genuinely missing (NOW skipped Q3 2026 in our run), the skill
substitutes the next available quarter and flags it.

## Multiples NOT in v1

### P/B (price to book)

```
p_b = price / (equity / diluted_shares_outstanding)
```

Standard for banks, insurers, REITs. Less useful for software (book
value is mostly goodwill). Queued for the v2 financials-extension PR
that adds bank-friendly multiples; the equity and shares fields are
already on the endpoint.

### P/FCF (price to free cash flow)

True FCF requires parsing CapEx separately. Massive's financials
endpoint exposes `net_cash_flow_from_investing_activities` which
lumps CapEx with securities purchases, acquisitions, and divestitures.
Subtracting it from operating CF produces noise.

Documented in `universe-builder`'s [`filtering-methodology.md`](../../universe-builder/references/filtering-methodology.md);
the path forward (parse raw XBRL filings) is the same for both skills.

### PEG (P/E divided by growth)

```
peg = p_e / (revenue_growth_ttm * 100)
```

Useful for high-growth names where P/E alone makes them look
expensive. The skill's regression-adjusted multiples view (see
[`regression-adjustment.md`](./regression-adjustment.md)) accomplishes
the same intent more rigorously: it controls for growth as a
regressor across the peer set, which is what PEG approximates with a
single divisor.

PEG is queued for v2 as a familiar-to-bankers alternative when the
peer set is too small for the regression to be meaningful (n_peers < 6).

### Forward multiples (NTM EV/Sales, NTM P/E)

Requires consensus estimates. Benzinga has analyst ratings but not
consensus estimates in the bundle currently subscribed. Tier A
extension when consensus is available.

## Sector-specific defaults

The default v1 set (EV/Sales, EV/EBITDA, P/E) fits software, hardware,
industrials, consumer, healthcare. It does not fit:

| Sector       | Use instead          | Why                                          |
|--------------|----------------------|----------------------------------------------|
| Banks        | P/B, P/TBV, ROE      | EV undefined for banks; earnings cyclical    |
| Insurers     | P/B, P/E             | Book value drives the business               |
| REITs        | P/FFO, P/AFFO        | EBITDA inflated by depreciation              |
| Oil & gas    | EV/EBITDAX, P/CF     | Exploration costs distort EBITDA             |
| Biotech      | EV/Pipeline, P/S only | Pre-revenue / pre-profit by definition       |

The skill's curated override map handles the **peer set** for these
sectors. The **multiple set** is still v1's three; a banker pitching
bank comps would replace EV/EBITDA with P/B in their deck, but the
underlying data (equity, shares, price) is in the JSON and the
swap is one column edit.

Documented gap; a sector-aware multiple selector is queued as a v2
PR. The schema reserves `multiples.p_b`, `multiples.p_fcf`,
`multiples.peg` for the additions.
