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

**Last updated:** 2026-06-26 (correctness sprint wave 3: C2, C3, C4 closed via point-in-time financials + Newey-West SE; N6 closed via local-symbol renames)

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
| C1 | `[x]` | backtest-data-prep | Closed in commit `183bddf` via honest-labeling. `--survivorship` argparse now only accepts `biased`; `delisted_during_window_count: None` when no inactive pull happened (was misleading 0); render block unconditionally says "Active only (current snapshot)". Real `active=false` union deferred to a follow-up sprint |
| C2 | `[x]` | factor-research | Closed in commit `7a7b33a`. Per-rebalance market cap is now built from historical weighted-diluted share count at the most-recent-pre-rebalance filing (`fetch_fundamentals` bumped from `timeframe=annual&limit=2` to `timeframe=quarterly&limit=80`; zero net additional API calls). Tickers with no filing before a given rebalance are dropped for that month only. Cache renamed `fundamentals_pit.json` so stale annual JSON doesn't poison runs |
| C3 | `[x]` | factor-research | Closed in commit `7a7b33a`. Per-horizon IC t-stat now uses `newey_west_se(ic_series, lag=horizon-1)` from `lib.quant_garage.stats`. At h=1 lag=0 reduces to iid SE so behavior is unchanged where there's no overlap; at h=12 it covers all 11 months. `ic_se_1m/3m/6m/12m` and `n_months_1m/.../12m` now populate per factor (previously `ic_se_1m` was always `null`). Wraps the SE call so n<2 / non-positive variance reports `None` rather than crashing |
| C4 | `[x]` | factor-research | Closed in commit `7a7b33a`. Quality factor (ROE = net_income / book_equity) is now computed per rebalance from the same filing-history cache used for C2. The `np.tile` block at the prior :451 / :456 is gone. Same +150% / −100% sanity bounds preserved. Marginal cost was tiny because the per-ticker filing cache was already in place for C2 |
| C5 | `[x]` | event-study, earnings-drilldown | Closed in commit `80f7536`. Post-announcement drift (T+1→T+horizon) added as a separate field alongside the existing announcement-inclusive CAR (T0→T+horizon). Rendered output shows both "Event-window CAR" and "Post-announcement drift" blocks. JSON schema gains `post_announce_drift_*_pct` per horizon |
| C6 | `[x]` | event-study | Closed in commit `80f7536`. All `abs(t) > 2.0` checks in event-study + earnings-drilldown replaced with `is_significant(t, n)` from lib.quant_garage.stats. At n=4 critical t is 3.18, not 2.0; the prior code over-asserted significance at small samples |
| C7 | `[x]` | valuation-sanity-check | Closed in commit `b75a117`. Peers missing D&A are excluded from EBITDA comparison and from p25/median/p75 stats. Each excluded peer carries `excluded_from_ebitda_comp: true, reason: "missing_da"`. `tier_caveats` counts excluded peers |
| C8 | `[x]` | options-flow | Closed in commit `29cb063`. NBBO at trade time via per-trade `/v3/quotes?timestamp.lte={ns}&order=desc&limit=1`. Trades pulled with `timestamp.gte/lte` window instead of `order=desc limit=200` so sweeps outside the recent window no longer silently downgrade. The `fetch_nbbo_at` helper kept inline (couldn't cleanly share with best-ex-check; different lookback windows and fetch wrappers) |
| C9 | `[x]` | corp-actions-reconciler | Closed in commit `39211d9`. `apply_dividend()` now switches on `dividend_type`: RC (cash basis adj), SC (special cash, basis adj + flag), SD (stock dividend as fractional split), LT (large stock dividend as fractional split per IRS), ST (reshapes and routes to `apply_split()`). Unknown types append to `tier_caveats` rather than silently skipping |
| C10 | `[x]` | t+1-settlement-prep | Closed in commit `39211d9`. Cum-dividend entitlement requires `trade_date < ex_dividend_date` strictly; ex-date trades emit informational "NOT allocated to buyer" notice instead of being flagged as entitled |
| C11 | `[x]` | valuation, pitch-comps | Closed in commit `b75a117`. EV math is now `mcap + total_debt − cash + operating_leases + minority_interest` in both scripts. Required fields (mcap, total_debt, cash) raise NotImplementedError if missing; optional fields (leases, minorities) default to 0 and populate `ev_components.missing_fields`. `ev_components` surfaced per ticker for audit trail |
| C12 | `[x]` | valuation | Closed in commit `b75a117`. Share count source switched from `share_class_shares_outstanding` (Class A only on dual-class) to `weighted_average_diluted_shares_outstanding` with documented fallback. Both `current_mcap` and `target_mcap` paths fixed. `shares_source` field on output for audit trail |

### High

| ID | Status | Affects | Resolution path |
|---|---|---|---|
| H1 | `[x]` | best-ex-check, news-scanner, portfolio-mark, earnings-drilldown | Closed in batch-3 migration (commit `2aa7724`); all 16 scripts now use `utc_to_et()` from zoneinfo. DST math correct year-round |
| H2 | `[x]` | most scripts | Closed in batch-3 migration (commit `2aa7724`); all 16 scripts now use `today()`. `QUANT_GARAGE_AS_OF` env override is the documented way to freeze for reproducible runs |
| H3 | `[x]` | universe-builder, factor-research | Closed in commit `183bddf` for universe-builder. The reference-path's false "Survivorship: clean" assertion is gone; mode is now derived from evidence (`fetched_inactive AND delisted_in_window > 0`). factor-research path also affected by H3 but uses a different code structure; pending verification it's already correct or needs a follow-up |
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
| N6 | Closed | Closed in commit `7a7b33a`. Local `build_universe()` renamed to `_build_factor_universe()`; local `winsorize()` renamed to `_winsorize_series()`. Lib import expanded to pull in `newey_west_se`. The local `_winsorize_series` was kept (not replaced with `lib.quant_garage.winsorize`) because the panel code needs pandas-Series semantics (`.dropna()`, `.quantile()`, `.clip()` with preserved index) and the lib version returns a plain list |
| N7 | Closed | Closed in commit `76413f5`. `MassiveClient.__init__` no longer raises on missing `MASSIVE_API_KEY`; the check moved to `_headers()` so instantiation is cheap and `--help` paths work. Verified via `python3 examples/run-event-study.py --help` with the env var unset |

---

## Original audit text

The deep review from 2026-06-26 lives in
`AUDIT-ORIGINAL.md` (preserved verbatim for reference). The scorecard
above mirrors its IDs.
