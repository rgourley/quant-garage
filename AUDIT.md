# Audit status

Tracking document for the 2026-06-26 deep audit. Updated as items are
addressed. The audit's original prose and evidence is preserved at the
bottom of this file; the table at the top is the running scorecard.

**Legend:**

- `[ ]` Open. Not yet addressed in code.
- `[~]` Foundation-addressed. The fix exists in `lib/quant_garage/` but
  hasn't propagated to every script that needs it. Still affects any
  script that hasn't been migrated.
- `[x]` Closed. Fix landed in every affected script, verified.

**Last updated:** 2026-06-26 (batch 1 migration: 7 of 16 scripts now on lib.quant_garage)

---

## Scorecard

### Live failures

| ID | Severity | Status | Affects | Notes |
|---|---|---|---|---|
| L1 | Crit | `[x]` | event-study | Closed: SEC EDGAR 8-K item 2.02 fallback implemented (was stubbed `return []`); volume-spike mode-detection bug fixed (single ticker + window now resolves to aggregate, not single); script migrated to lib.quant_garage. Verified on AAPL single, mega-cap cross-section, mega-cap aggregate, and NVDA volume-spike. |
| L2 | Crit | `[x]` | backtest-data-prep | Closed by commit adding `pyarrow>=15.0` to `requirements.txt` |
| L3 | High | `[~]` | earnings-drilldown (Tier B) | Retry exists in `MassiveClient`; applied to 7 migrated scripts. Other 9 scripts unchanged |

### Critical (corrupts output numbers)

| ID | Status | Affects | Resolution path |
|---|---|---|---|
| C1 | `[~]` | backtest-data-prep | `lib/quant_garage/universe.py::build_universe()` returns Universe with honest `survivorship_mode`. Migration to backtest-data-prep pending |
| C2 | `[ ]` | factor-research | Point-in-time mcap / share count at each rebalance (deferred — needs financials integration on top of universe.py) |
| C3 | `[~]` | factor-research | `lib/quant_garage/stats.py::newey_west_se()` ready. Migration to factor-research pending |
| C4 | `[ ]` | factor-research | Point-in-time fundamentals per rebalance, or drop quality from decay table (deferred — same financials integration as C2) |
| C5 | `[ ]` | event-study, earnings-drilldown | Separate announcement-excluded drift (T+1 to T+horizon) from announcement reaction |
| C6 | `[~]` | event-study | `lib/quant_garage/stats.py::critical_t()` and `is_significant()` ready. Migration to event-study pending |
| C7 | `[ ]` | valuation-sanity-check | Skip peers missing D&A, or normalize numerator both sides |
| C8 | `[ ]` | options-flow | Real NBBO at print time (copy `best-ex-check`'s `fetch_nbbo_at`); fix limit=200 silent downgrade |
| C9 | `[ ]` | corp-actions-reconciler | Handle SC/SD/ST/LT dividend types, not just RC |
| C10 | `[ ]` | t+1-settlement-prep | Entitlement = bought strictly before ex-date |
| C11 | `[ ]` | valuation, pitch-comps | EV = mcap + total debt − cash (+ leases, minorities) |
| C12 | `[ ]` | valuation | Weighted-diluted shares (not single share class) |

### High

| ID | Status | Affects | Resolution path |
|---|---|---|---|
| H1 | `[~]` | best-ex-check, news-scanner, portfolio-mark, earnings-drilldown | Fixed in `lib/quant_garage/timezones.py`; migrated to 7 scripts so far. Other 9 still use hardcoded UTC-4 |
| H2 | `[~]` | most scripts | Fixed in `lib/quant_garage/as_of.py`; migrated to 7 scripts so far |
| H3 | `[~]` | universe-builder, factor-research | `Universe.survivorship_mode` is derived from how the universe was built, not asserted by caller. Migration pending |
| H4 | `[ ]` | pitch-comps | Min-n enforcement; SE/t-stat/CI on OLS; drop endogenous regressor |
| H5 | `[ ]` | valuation, pitch-comps | Consistent D&A and operating-income annualization in shared lib |
| H6 | `[ ]` | options-flow | Cap or flag zero-OI separately in vol_oi_ratio |
| H7 | `[ ]` | crypto-vol-scanner | Fixed 24h lookback, min-sample guard, exclude partial day, emit n |
| H8 | `[ ]` | news-scanner | Anchor reaction to first bar at/after publish; fetch wide enough window for 5-day baseline |
| H9 | `[ ]` | best-ex-check | Rename to "slippage cost" or compute true arrival-price IS |
| H10 | `[ ]` | portfolio-mark | 30-day ADV from reference, not today's `day.v` |

### Medium

| ID | Status | Affects | Resolution path |
|---|---|---|---|
| M1 | `[ ]` | best-ex-check | Make off_nbbo exclusive of crossed_spread bucket |
| M2 | `[ ]` | best-ex-check | Use quotes endpoint for NBBO proxy; doc the bias direction correctly |
| M3 | `[ ]` | corp-actions-reconciler | One consolidated break with final state per ticker |
| M4 | `[ ]` | corp-actions-reconciler | Tolerance compare on fractional shares |
| M5 | `[~]` | universe-builder | `lib/quant_garage/universe.py::top_quartile_threshold()` returns exact 75th percentile. Migration pending |
| M6 | `[~]` | universe-builder | `lib/quant_garage/universe.py::concentration_z_score()` is the one definition. Migration pending |
| M7 | `[ ]` | portfolio-mark | Use streamed quote in live mode; skip REST round-trip |
| M8 | `[~]` | most scripts | Per-call fetched_at via `MassiveClient.get()`; migrated to 7 scripts so far |
| M9 | `[ ]` | options-flow, crypto-vol-scanner, news-scanner | Add percentile/base-rate context on composite scores |
| M10 | `[ ]` | earnings-drilldown, event-study (single mode) | Universe base rate for single-name skills |

### Doc / code drift

| ID | Status | Affects | Resolution path |
|---|---|---|---|
| D1 | `[ ]` | massive-flat-files | Document separate S3 access key + secret |
| D2 | `[ ]` | massive-websockets | Align docs to actual WS status enum |
| D3 | `[~]` | most scripts | Client uses `api.polygon.io` exclusively, citations match in 7 migrated scripts |
| D4 | `[~]` | massive-api-patterns | `lib/quant_garage/snapshot.py` uses correct paths; foundation doc still wrong |
| D5 | `[~]` | portfolio-mark | Chain is 4 steps in `resolve_price`; foundation doc still says 5 |
| D6 | `[~]` | earnings-drilldown, pitch-comps, options-flow, portfolio-mark, corp-actions | event-study EDGAR fallback implemented (was stubbed). Other scripts still claim documented behavior they don't deliver |

---

## New findings from the migration (2026-06-26)

These weren't in the original audit but surfaced during the foundation refactor:

| ID | Severity | Notes |
|---|---|---|
| N1 | Dismissed | **Resolved 2026-06-26 by direct probe.** SEC EDGAR's `acceptanceDateTime` field IS UTC. The `Z` suffix is the explicit ISO 8601 UTC marker. Verified against AAPL's last 6 earnings 8-Ks: every acceptance time interpreted as UTC lands at exactly 16:30 ET (AAPL's standard print time). Internally consistent across DST: 20:30Z in EDT months (= 16:30 EDT), 21:30Z in EST months (= 16:30 EST). The script's existing UTC interpretation is correct. The migration subagent's concern was wrong. |
| N2 | Medium | `run-aapl-tier-b.py` uses `lastQuote.p` (quote-mid) for spot price, bypassing `lastTrade`. `resolve_price()` doesn't cover lastQuote. Decide whether to extend the chain or keep the inline read |
| N3 | Cosmetic | `utcnow_iso()` emits `+00:00`; some scripts normalized to `Z`. JSON consumers regex-matching `Z` would now miss |

---

## Original audit text

The deep review from 2026-06-26 lives in
`AUDIT-ORIGINAL.md` (preserved verbatim for reference). The scorecard
above mirrors its IDs.
