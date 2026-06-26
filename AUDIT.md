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

**Last updated:** 2026-06-26 (batch 3 migration complete — all 16 of 16 scripts on lib.quant_garage; L3, H1, H2, M8, D3, D5 closed)

---

## Scorecard

### Live failures

| ID | Severity | Status | Affects | Notes |
|---|---|---|---|---|
| L1 | Crit | `[x]` | event-study | Closed: SEC EDGAR 8-K item 2.02 fallback implemented (was stubbed `return []`); volume-spike mode-detection bug fixed (single ticker + window now resolves to aggregate, not single); script migrated to lib.quant_garage. Verified on AAPL single, mega-cap cross-section, mega-cap aggregate, and NVDA volume-spike. |
| L2 | Crit | `[x]` | backtest-data-prep | Closed by commit adding `pyarrow>=15.0` to `requirements.txt` |
| L3 | High | `[x]` | earnings-drilldown (Tier B) | Closed in batch-3 migration (commit `2aa7724`); all 16 scripts now retry via `MassiveClient` |

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
| H1 | `[x]` | best-ex-check, news-scanner, portfolio-mark, earnings-drilldown | Closed in batch-3 migration (commit `2aa7724`); all 16 scripts now use `utc_to_et()` from zoneinfo. DST math correct year-round |
| H2 | `[x]` | most scripts | Closed in batch-3 migration (commit `2aa7724`); all 16 scripts now use `today()`. `QUANT_GARAGE_AS_OF` env override is the documented way to freeze for reproducible runs |
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
| M5 | `[x]` | universe-builder | Closed in batch-2 migration (commit `143b3f5`); script now uses `top_quartile_threshold()`. The `*0.75-1` indexing bug is gone |
| M6 | `[x]` | universe-builder | Closed in batch-2 migration (commit `143b3f5`); script now uses `concentration_z_score()`. The single existing baseline path swapped cleanly (subagent reports no curated-vs-grouped-vs-reference divergence remained in current code) |
| M7 | `[ ]` | portfolio-mark | Use streamed quote in live mode; skip REST round-trip |
| M8 | `[x]` | most scripts | Closed in batch-3 migration (commit `2aa7724`); all 16 scripts now stamp `fetched_at` per call via `MassiveClient.get()` |
| M9 | `[ ]` | options-flow, crypto-vol-scanner, news-scanner | Add percentile/base-rate context on composite scores |
| M10 | `[ ]` | earnings-drilldown, event-study (single mode) | Universe base rate for single-name skills |

### Doc / code drift

| ID | Status | Affects | Resolution path |
|---|---|---|---|
| D1 | `[ ]` | massive-flat-files | Document separate S3 access key + secret |
| D2 | `[ ]` | massive-websockets | Align docs to actual WS status enum |
| D3 | `[x]` | most scripts | Closed in batch-3 migration (commit `2aa7724`); all 16 scripts now cite `api.polygon.io` exclusively |
| D4 | `[~]` | massive-api-patterns | `lib/quant_garage/snapshot.py` uses correct paths and all migrated scripts now use it; foundation doc at `skills/massive-api-patterns/` still claims the wrong paths and needs a separate doc fix |
| D5 | `[x]` | portfolio-mark | Closed in batch-1 migration (commit `a062e90`); portfolio-mark now uses `resolve_price()` which is the canonical 4-step chain. Foundation doc still says 5 steps but that's covered by D4 |
| D6 | `[~]` | earnings-drilldown, pitch-comps, options-flow, portfolio-mark, corp-actions | event-study EDGAR fallback implemented (was stubbed). Other scripts still claim documented behavior they don't deliver |

---

## New findings from the migration (2026-06-26)

These weren't in the original audit but surfaced during the foundation refactor:

| ID | Severity | Notes |
|---|---|---|
| N1 | Dismissed | **Resolved 2026-06-26 by direct probe.** SEC EDGAR's `acceptanceDateTime` field IS UTC. The `Z` suffix is the explicit ISO 8601 UTC marker. Verified against AAPL's last 6 earnings 8-Ks: every acceptance time interpreted as UTC lands at exactly 16:30 ET (AAPL's standard print time). Internally consistent across DST: 20:30Z in EDT months (= 16:30 EDT), 21:30Z in EST months (= 16:30 EST). The script's existing UTC interpretation is correct. The migration subagent's concern was wrong. |
| N2 | Medium | `run-aapl-tier-b.py` uses `lastQuote.p` (quote-mid) for spot price, bypassing `lastTrade`. `resolve_price()` doesn't cover lastQuote. Decide whether to extend the chain or keep the inline read |
| N3 | Cosmetic | `utcnow_iso()` emits `+00:00`; some scripts normalized to `Z`. JSON consumers regex-matching `Z` would now miss |
| N4 | Cosmetic | `run-pitch-comps.py` and `run-valuation-sanity-check.py` had a `ticker.fmv` step at the bottom of their snapshot fallback waterfalls that never executed (FMV is a separate stream-only event, not on v2 snapshot). Migration to `resolve_price()` silently drops this dead step. No behavioral change because it never returned non-None. Same family as D5 |
| N5 | Doc | `/v1/marketstatus/upcoming` returns a bare JSON array, not a `{results: [...]}` envelope like the rest of the API. `client.paginate()` assumes the envelope and `body.get("results")` returns `None` on a bare array. Callers of this endpoint must use `client.get()` and `isinstance(body, list)`. Surfaced during the run-t1-settlement-prep migration. Should be noted in the lib's docstring for `paginate()` so future skill authors don't hand `marketstatus/upcoming` to it and silently get zero rows |
| N6 | Future-blocker | `run-factor-research.py` defines local `build_universe()` and `winsorize()` functions that collide by name with `lib.quant_garage.build_universe` and `lib.quant_garage.winsorize`. Batch-3 migration only imported `MassiveClient/FetchError/today/utcnow_iso` to avoid the collision, leaving the local functions intact. When the C2/C3/C4 sprint adopts the lib helpers here, the local functions need to be renamed or removed first |

---

## Original audit text

The deep review from 2026-06-26 lives in
`AUDIT-ORIGINAL.md` (preserved verbatim for reference). The scorecard
above mirrors its IDs.
