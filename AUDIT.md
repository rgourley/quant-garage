# Audit status

Active scorecard for the 2026-06-26 deep audit. Closed items live in
[`AUDIT-LOG.md`](./AUDIT-LOG.md); the original deep review is preserved
verbatim in `AUDIT-ORIGINAL.md`.

**Tally (as of 2026-06-26):** 25 closed, 0 foundation-addressed, 15
open. All 12 original Critical items closed.

**Legend:**

- `[ ]` Open. Not yet addressed in code.
- `[~]` Foundation-addressed. The fix exists in `lib/quant_garage/` but
  hasn't propagated to every script that needs it.

---

## Open items

### High

| ID | Affects | Resolution path |
|---|---|---|
| H4 | pitch-comps | Min-n enforcement; SE/t-stat/CI on OLS; drop endogenous regressor |
| H5 | valuation, pitch-comps | Consistent D&A and operating-income annualization in shared lib |
| H6 | options-flow | Cap or flag zero-OI separately in vol_oi_ratio |
| H7 | crypto-vol-scanner | Fixed 24h lookback, min-sample guard, exclude partial day, emit n |
| H8 | news-scanner | Anchor reaction to first bar at/after publish; fetch wide enough window for 5-day baseline |
| H9 | best-ex-check | Rename to "slippage cost" or compute true arrival-price IS |
| H10 | portfolio-mark | 30-day ADV from reference, not today's `day.v` |

### Medium

| ID | Affects | Resolution path |
|---|---|---|
| M1 | best-ex-check | Make off_nbbo exclusive of crossed_spread bucket |
| M2 | best-ex-check | Use quotes endpoint for NBBO proxy; doc the bias direction correctly |
| M3 | corp-actions-reconciler | One consolidated break with final state per ticker |
| M4 | corp-actions-reconciler | Tolerance compare on fractional shares |
| M7 | portfolio-mark | Use streamed quote in live mode; skip REST round-trip |
| M9 | options-flow, crypto-vol-scanner, news-scanner | Add percentile/base-rate context on composite scores |
| M10 | earnings-drilldown, event-study (single mode) | Universe base rate for single-name skills |

### Doc / code drift

| ID | Affects | Resolution path |
|---|---|---|
| D1 | massive-flat-files | Document separate S3 access key + secret |
| D2 | massive-websockets | Align docs to actual WS status enum |
| D4 `[~]` | massive-api-patterns | Foundation doc still claims wrong snapshot paths; lib + scripts are correct |
| D6 `[~]` | earnings-drilldown, pitch-comps, options-flow, portfolio-mark, corp-actions | Several scripts still claim documented behavior they don't deliver |

### Findings from the migration (still open)

| ID | Severity | Notes |
|---|---|---|
| N2 | Medium | `run-aapl-tier-b.py` uses `lastQuote.p` for spot, bypassing `lastTrade`. `resolve_price()` doesn't cover lastQuote. Decide whether to extend the chain or keep the inline read |
| N3 | Cosmetic | `utcnow_iso()` emits `+00:00`; some scripts normalized to `Z`. JSON consumers regex-matching `Z` would now miss |
| N4 | Cosmetic | Migration to `resolve_price()` silently drops the dead `ticker.fmv` step in pitch-comps and valuation-sanity-check. No behavior change |
| N5 | Doc | `/v1/marketstatus/upcoming` returns a bare array, not a `{results: [...]}` envelope. Note this in `paginate()` docstring so it isn't silently mishandled |

---

## See also

- [`AUDIT-LOG.md`](./AUDIT-LOG.md) — closure history with commit refs and methodology notes per item
- `AUDIT-ORIGINAL.md` — verbatim deep review from 2026-06-26
