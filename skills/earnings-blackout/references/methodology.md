# Methodology: earnings-blackout

The skill mirrors the `event-study` wave-13 resolver chain so the two
skills agree on what counts as an "earnings event". Treat this as the
canonical methodology spec for the scanner; the resolver code lives in
`examples/run-earnings-blackout.py` and is an inline copy of the same
helpers used by `examples/run-event-study.py`.

## Two-tier resolver

### Tier A: Benzinga earnings calendar

`GET /benzinga/v1/earnings?ticker={T}&date.gte={from}&date.lte={to}`

Benzinga is the only source that publishes a *forward* earnings
calendar. For each ticker we pull rows within
`[today - include_past_days, today + window_days]`. The endpoint
returns confirmed and projected prints; we keep both because the
scanner cares about the date itself, not the confirmation state.

Per-row fields surfaced:
- `date` (confirmed publication date, ET)
- `time` (HH:MM:SS ET, mapped to BMO/AMC/DMH)
- `eps_est` → `consensus_eps`
- `revenue_est` → `consensus_revenue`

If the API key lacks the Benzinga add-on, the call returns 401/403 and
we fall through to Tier B. The 5-tier `try/except` pattern from
event-study is preserved verbatim.

### Tier B: SEC EDGAR 8-K filings

`GET https://data.sec.gov/submissions/CIK{cik_padded}.json`

EDGAR is the free fallback. We pull every 8-K filing for the company
and filter on the `items` column, accepting any 8-K tagged with at
least one of:

- `2.02` — Results of Operations and Financial Condition (the classical
  earnings release item). Tagged `signal_strength: "strong"`.
- `7.01` — Regulation FD Disclosure. Tagged `signal_strength: "soft"`.
- `8.01` — Other Events. Tagged `signal_strength: "soft"`.

#### Why all three items, not just 2.02?

Wave 13 (commit history in event-study) expanded the matcher from
strict 2.02 to the broader set after observing that biotechs and small
caps disclose earnings-equivalent results under 7.01 (Reg FD) or 8.01
(catch-all). A strict 2.02 filter dropped ~12% of legitimate prints in
the small-cap audit. The trade-off: 7.01 and 8.01 also carry non-
earnings disclosures (guidance updates, corporate actions, etc.), so
we tag them `soft` and let downstream consumers weight conservatively.

#### EDGAR is past-only

EDGAR is a filings database. It has no forward earnings calendar by
construction — a company can't file an 8-K for a print that hasn't
happened. That means Tier B tickers will surface as `unresolved` for
their next print until the company actually files. This is a
fundamental data-source limitation, not a bug. The `tier_caveats[]`
output makes this explicit when the scanner is operating in Tier B.

## CIK lookup chain

1. **Primary**: Massive `/v3/reference/tickers/{T}` returns the company's
   CIK on `.results.cik`. Fast, single round-trip.
2. **Fallback**: SEC's free canonical `company_tickers.json` (~17k
   entries, cached in-process for the run). Used when Massive's
   response is missing the CIK field — common for smaller and newer
   listings.

If both fail, `get_cik(ticker)` returns `None` and that ticker's EDGAR
pull short-circuits. The ticker surfaces as `unresolved` in the output.

## Status classification

Once the resolver returns `next_date` and `past_date` (either may be
`None`), the classifier picks the dominant status:

| Status               | Trigger                                |
| -------------------- | -------------------------------------- |
| `blackout_imminent`  | next_date is 0-3 days forward          |
| `blackout_soon`      | next_date is 4-7 days forward          |
| `blackout_extended`  | next_date is 8+ days forward           |
| `just_printed`       | past_date is 0-3 days past             |
| `recent_print`       | past_date is 4-include_past_days past  |
| `clear`              | resolver hit but no event in window    |
| `unresolved`         | resolver returned nothing              |

Forward earnings beat past earnings: if a ticker has both a recent
print and an upcoming one within the windows, the upcoming one
determines the status. The user cares more about what's coming.

`clear` vs `unresolved` is intentional: `clear` means the resolver
worked and found no earnings; `unresolved` means we couldn't tell
either way. They mean different things for risk management. Treating
them the same is the silent-omission bug from earlier waves.

## Provenance

Every API call is logged into `sources[]` with the endpoint, the
`fetched_at` timestamp, and a one-line context string. This matches
the wave-8 provenance pattern used across the quant-garage skills.
