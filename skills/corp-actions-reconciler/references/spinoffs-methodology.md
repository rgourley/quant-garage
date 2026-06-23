# Spinoffs methodology

When company X spins off subsidiary Y, the original holder receives Y
shares per X share at the spin ratio. The original position keeps its
shares; a new position is created in Y. Cost basis is split between
the two using the relative fair-market-value rule.

This is the messiest action category. The math is easy; the data
plumbing is not. Massive's reference endpoints don't have a dedicated
spinoffs feed, so the reconciler stitches them together from the
splits endpoint (which catches some), the dividends endpoint with
`dividend_type: "SO"`, and an optional manual `spinoffs.json` override
for events the API misses. Operators with active spin desks supply the
override file; everyone else accepts that mid-market spins from 2018-
2020 may be incomplete.

## The mechanic

Parent ticker X spins subsidiary Y at ratio `Y_shares_per_X_share`:

```
parent_position:
  shares      unchanged
  cost_basis  reduced to: original_basis * (parent_fmv / (parent_fmv + sub_fmv))

new spinoff_position:
  ticker      Y
  shares      X_shares * Y_shares_per_X_share
  cost_basis  original_basis * (sub_fmv / (parent_fmv + sub_fmv))
```

Two positions, two cost bases, sum equals the original basis.

## Cost basis allocation rule

The reconciler uses the relative-market-cap rule (IRS-acceptable, what
most US brokers default to):

```python
parent_alloc_pct = parent_close_first_session / (
    parent_close_first_session + sub_close_first_session * y_per_x
)
sub_alloc_pct = 1 - parent_alloc_pct
```

Where `parent_close_first_session` is the parent's regular-way close on
the spin date (post-distribution), and `sub_close_first_session` is the
subsidiary's first-day regular-way close. The reconciler pulls both
from `/v2/aggs/ticker/{ticker}/range/1/day/{spin_date}/{spin_date}`.

This isn't the only legal method. Some operators use:

- **Pre-announcement-period average:** average closes for the 10 sessions
  before the spin announcement. More stable but less defensible to the
  IRS.
- **Form-8937 issuer-specified ratio:** when the company files a
  Form 8937, it specifies the cost basis allocation. This is the
  authoritative number when available. The reconciler looks up the
  8937 via a manual overrides file if `spinoffs.json` includes a
  `form_8937_alloc_pct` field.

The default (first-session relative market cap) is the most common.
When the issuer files an 8937, prefer that. The reconciler outputs
`alloc_method` in the break so the audit trail shows which method was
used.

## Fractional shares

Spinoff ratios are rarely round. AT&T's WBD spin in 2022 distributed
0.241917 WBD shares per T share. 100 T shares result in 24.1917 WBD
shares. The broker pays CIL on the 0.1917 and the operator keeps 24
whole shares.

The reconciler floors `expected_shares` for the new spin position and
flags `cash_in_lieu_expected = true`. As with splits, it does NOT
attempt to value the CIL: that needs broker-specific rounding rules.

## Foreign-domiciled spinoffs

When a US-listed parent spins a foreign-domiciled subsidiary, the
subsidiary trades as an ADR or as an ordinary (sometimes pink sheets).
This matters for the reconciler in two ways:

1. The `Y` ticker in the position file might be the ADR symbol while
   Massive's record uses the ordinary, or vice versa. The overrides
   file should map both.
2. Foreign spins sometimes have a holding-period election for
   long-term capital gains treatment. The reconciler does not handle
   tax treatment.

See [`edge-cases.md`](./edge-cases.md) for the ADR/ordinary mapping
pattern.

## Reverse spinoffs and Morris Trusts

A Morris Trust spinoff is a tax-advantaged structure where the parent
spins a subsidiary to shareholders and then the subsidiary merges with
a third party (or vice versa). The reconciler treats the spin step
identically to a standard spinoff: the merge step is a separate
corporate action handled by the merger logic. Don't try to model the
two as a single event: brokers settle them as two confirms, and the
reconciler should match.

## Endpoint situation

There's no `/v3/reference/spinoffs` endpoint as of June 2026. The
reconciler reads spinoffs from three sources, in order of preference:

1. `spinoffs.json` in the run directory (operator overrides). This is
   the authoritative source when supplied. Format:
   ```json
   [
     {
       "parent_ticker": "T",
       "spinoff_ticker": "WBD",
       "ex_date": "2022-04-11",
       "ratio_y_per_x": 0.241917,
       "form_8937_alloc_pct": 0.71
     }
   ]
   ```
2. Massive `/v3/reference/dividends?ticker={ticker}&dividend_type=SO`
   (some operators tag spinoffs with the SO dividend code). Sparse
   coverage.
3. Massive `/v3/reference/splits?ticker={ticker}` filtered to records
   where `split_to == split_from` and the response includes the
   subsidiary ticker. Even sparser coverage.

When the operator only uses sources 2-3, the reconciler emits a
warning in the footer: `Spinoff coverage incomplete; supply
spinoffs.json for full reconciliation.` Don't silently miss spins.

## Worked example

T (AT&T) spins WBD (Warner Bros Discovery), ex-date 2022-04-11,
distribution ratio 0.241917 WBD shares per T share. Operator holds
100 T shares with $30 average cost basis, as_of_date 2022-01-01.

First-day closes (per Massive aggs):
- T: $19.63 (post-distribution)
- WBD: $24.78

Allocation:
```
parent_alloc_pct = 19.63 / (19.63 + 24.78 * 0.241917)
                 = 19.63 / (19.63 + 5.996)
                 = 19.63 / 25.626
                 = 0.766
sub_alloc_pct    = 0.234
```

Reconciler output:
- T position: 100 shares, expected cost basis = $30 * 0.766 = $22.98
- WBD position (new): 24 shares (24.1917 floored), expected cost basis
  per share = ($30 * 0.234 * 100) / 24.1917 = $29.02. Flag: CIL on
  0.1917 share.

If the input position file shows the T basis unchanged at $30 and no
WBD position, the reconciler emits two breaks: one for the parent
cost-basis adjustment, one for the missing new position.
