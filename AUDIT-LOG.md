# Audit closure log

Closure history for items from the 2026-06-26 deep audit. The active
scorecard lives in [`AUDIT.md`](./AUDIT.md); the original deep review
is preserved in `AUDIT-ORIGINAL.md`.

Closures are grouped by sprint wave; within a wave, items are ordered
by ID.

---

## Wave 5 — 2026-06-26 (crypto-vol-scanner + news-scanner)

Commits: `ac4b2a2` (H7), `71f68a7` (H8).

| ID | Affects | Closure notes |
|---|---|---|
| H7 | crypto-vol-scanner | Four bugs in one file. (a) `RV_WINDOW_BARS = 24` constant; `rv_24h` slices `hourly_closes_completed[-(RV_WINDOW_BARS+1):]` so the window is fixed not rolling. (b) `MIN_BARS_24H = 20`; under-sampled coins emit `realized_vol_24h_pct=None` + `realized_vol_reason="insufficient_bars"`. (c) New `completed_hourly_closes(hourly_aggs, now_utc)` drops the current incomplete bar; both 24h and 30d distributions consume the filtered list so neither drifts with run time. (d) Each event carries `realized_vol_n_bars` and `realized_vol_reason`; run-level `insufficient_bars_count` in summary. 20-of-24 (~83%) is consistent with the existing 30-day window's ≥5-returns floor |
| H8 | news-scanner | Two related bugs. (a) `find_first_bar_at_or_after()` replaces the contains-publish-ts lookup; reaction now anchors to the first bar with `t >= publish_ts` within a 24h forward cap, emitting `reaction_anchor_offset_seconds`. No bar within window → `reason="no_bar_after_publish"`, drop event, bump `skipped_no_bar_count`. (b) Baseline fetch widened from 6 calendar days to 12 (`BASELINE_FETCH_CALENDAR_DAYS = 12`) — covers holiday-heavy weeks (Thanksgiving / July 4 / MLK / Memorial Day) without paying for excess history. Dedup by ET trading date so `n_baseline_days` is real trading-day count; <5 → `reason="insufficient_baseline"`, drop event, bump `insufficient_baseline_count`. Skipped events surface in `payload.skipped_events[]` with full context |

## Wave 4 — 2026-06-26 (quick-win cluster)

Commit: `cd5fc5e`.

| ID | Affects | Closure notes |
|---|---|---|
| H6 | options-flow | Zero-OI candidates now carry `vol_oi_ratio=None` + `zero_oi=True`. `compute_score` drops the OI term and renormalizes the remaining weights to sum to 1.0. Rendered output shows `"OI: 0 (new)"`. Run-level summary surfaces `zero_oi_count` |
| H10 | portfolio-mark | Added `fetch_adv_30d()` with module-level `_ADV_CACHE`. Window: `ref_date − 45d .. ref_date − 1d`, requires ≥5 sessions. `confidence_for` takes `adv_30d` and emits `unknown_adv` reason code when insufficient. Output emits both `day_volume` (provenance) and `adv_30d` (bucket source). Detail text now reads "30d ADV X (below 500k mid-ADV cutoff)" |
| D1 | massive-flat-files | WebSearch confirmed S3 keys are distinct from the REST API key (generated separately in the Polygon dashboard). Access section rewritten; `${MASSIVE_API_KEY}` replaced with `${POLYGON_S3_ACCESS_KEY}`/`${POLYGON_S3_SECRET_KEY}` in both `aws configure` and boto3 examples |
| D2 | massive-websockets | Verified against the official massive-com client-python WS mock server. Added `auth_failed` to the status enum (was missing); existing `success` / `error` / "not authorized" entries were correct. JS example gained an `auth_failed` branch |
| N3 | lib client | `utcnow_iso()` now returns `Z`-suffixed UTC (`.replace("+00:00", "Z")`). Verified: `2026-06-27T03:58:39.843018Z` |
| N4 | pitch-comps, valuation-sanity-check | No-op confirmed. The earlier `resolve_price()` migration already cleaned out the dead `ticker.fmv` waterfall step; only a docstring reference remains in `run-pitch-comps.py` |
| N5 | lib client | Added `Warning:` paragraph to `paginate()` docstring noting that bare-array endpoints like `/v1/marketstatus/upcoming` need `get()` directly because `body.get("results")` returns `None` |

## Wave 3 — 2026-06-26 (factor-research overhaul)

Commit: `7a7b33a` (code), `8b804dd` (scorecard).

| ID | Affects | Closure notes |
|---|---|---|
| C2 | factor-research | Per-rebalance market cap is now built from historical weighted-diluted share count at the most-recent-pre-rebalance filing (`fetch_fundamentals` bumped from `timeframe=annual&limit=2` to `timeframe=quarterly&limit=80`; zero net additional API calls). Tickers with no filing before a given rebalance are dropped for that month only. Cache renamed `fundamentals_pit.json` so stale annual JSON doesn't poison runs |
| C3 | factor-research | Per-horizon IC t-stat now uses `newey_west_se(ic_series, lag=horizon-1)` from `lib.quant_garage.stats`. At h=1 lag=0 reduces to iid SE so behavior is unchanged where there's no overlap; at h=12 it covers all 11 months. `ic_se_1m/3m/6m/12m` and `n_months_1m/.../12m` now populate per factor (previously `ic_se_1m` was always `null`). Wraps the SE call so n<2 / non-positive variance reports `None` rather than crashing |
| C4 | factor-research | Quality factor (ROE = net_income / book_equity) is now computed per rebalance from the same filing-history cache used for C2. The `np.tile` block at the prior :451 / :456 is gone. Same +150% / −100% sanity bounds preserved. Marginal cost was tiny because the per-ticker filing cache was already in place for C2 |
| N6 | factor-research | Local `build_universe()` renamed to `_build_factor_universe()`; local `winsorize()` renamed to `_winsorize_series()`. Lib import expanded to pull in `newey_west_se`. The local `_winsorize_series` was kept (not replaced with `lib.quant_garage.winsorize`) because the panel code needs pandas-Series semantics and the lib version returns a plain list |

## Wave 2 — 2026-06-26 (drift, significance, EV math)

Commits: `80f7536`, `b75a117`, `76413f5`.

| ID | Affects | Closure notes |
|---|---|---|
| C5 | event-study, earnings-drilldown | Post-announcement drift (T+1→T+horizon) added as a separate field alongside the existing announcement-inclusive CAR (T0→T+horizon). Rendered output shows both "Event-window CAR" and "Post-announcement drift" blocks. JSON schema gains `post_announce_drift_*_pct` per horizon |
| C6 | event-study | All `abs(t) > 2.0` checks in event-study + earnings-drilldown replaced with `is_significant(t, n)` from `lib.quant_garage.stats`. At n=4 critical t is 3.18, not 2.0; the prior code over-asserted significance at small samples |
| C7 | valuation-sanity-check | Peers missing D&A are excluded from EBITDA comparison and from p25/median/p75 stats. Each excluded peer carries `excluded_from_ebitda_comp: true, reason: "missing_da"`. `tier_caveats` counts excluded peers |
| C11 | valuation, pitch-comps | EV math is now `mcap + total_debt − cash + operating_leases + minority_interest` in both scripts. Required fields (mcap, total_debt, cash) raise NotImplementedError if missing; optional fields (leases, minorities) default to 0 and populate `ev_components.missing_fields`. `ev_components` surfaced per ticker for audit trail |
| C12 | valuation | Share count source switched from `share_class_shares_outstanding` (Class A only on dual-class) to `weighted_average_diluted_shares_outstanding` with documented fallback. Both `current_mcap` and `target_mcap` paths fixed. `shares_source` field on output for audit trail |
| N7 | client | `MassiveClient.__init__` no longer raises on missing `MASSIVE_API_KEY`; the check moved to `_headers()` so instantiation is cheap and `--help` paths work. Verified via `python3 examples/run-event-study.py --help` with the env var unset |

## Wave 1 — 2026-06-26 (live failures, options NBBO, corp actions)

Commits: `183bddf`, `29cb063`, `39211d9`, foundation refactor.

| ID | Affects | Closure notes |
|---|---|---|
| C1 | backtest-data-prep | Honest-labeling fix. `--survivorship` argparse now only accepts `biased`; `delisted_during_window_count: None` when no inactive pull happened (was misleading 0); render block unconditionally says "Active only (current snapshot)". Real `active=false` union deferred to a follow-up sprint |
| C8 | options-flow | NBBO at trade time via per-trade `/v3/quotes?timestamp.lte={ns}&order=desc&limit=1`. Trades pulled with `timestamp.gte/lte` window instead of `order=desc limit=200` so sweeps outside the recent window no longer silently downgrade. The `fetch_nbbo_at` helper kept inline (couldn't cleanly share with best-ex-check; different lookback windows and fetch wrappers) |
| C9 | corp-actions-reconciler | `apply_dividend()` now switches on `dividend_type`: RC (cash basis adj), SC (special cash, basis adj + flag), SD (stock dividend as fractional split), LT (large stock dividend as fractional split per IRS), ST (reshapes and routes to `apply_split()`). Unknown types append to `tier_caveats` rather than silently skipping |
| C10 | t+1-settlement-prep | Cum-dividend entitlement requires `trade_date < ex_dividend_date` strictly; ex-date trades emit informational "NOT allocated to buyer" notice instead of being flagged as entitled |
| L1 | event-study | SEC EDGAR 8-K item 2.02 fallback implemented (was stubbed `return []`); volume-spike mode-detection bug fixed (single ticker + window now resolves to aggregate, not single); script migrated to lib.quant_garage. Verified on AAPL single, mega-cap cross-section, mega-cap aggregate, and NVDA volume-spike |
| L2 | backtest-data-prep | Added `pyarrow>=15.0` to `requirements.txt` |
| L3 | earnings-drilldown (Tier B) | Closed in batch-3 migration (commit `2aa7724`); all 16 scripts now retry via `MassiveClient` |

## Foundation refactor — 2026-06-26 (lib.quant_garage + script migration)

Commits: `a062e90` (batch 1), `143b3f5` (batch 2), `2aa7724` (batch 3).

| ID | Affects | Closure notes |
|---|---|---|
| H1 | best-ex-check, news-scanner, portfolio-mark, earnings-drilldown | All 16 scripts now use `utc_to_et()` from zoneinfo. DST math correct year-round |
| H2 | most scripts | All 16 scripts now use `today()`. `QUANT_GARAGE_AS_OF` env override is the documented way to freeze for reproducible runs |
| H3 | universe-builder, factor-research | universe-builder's false "Survivorship: clean" assertion is gone; mode now derived from evidence (`fetched_inactive AND delisted_in_window > 0`) |
| M5 | universe-builder | Now uses `top_quartile_threshold()`. The `*0.75-1` indexing bug is gone |
| M6 | universe-builder | Now uses `concentration_z_score()`. The single existing baseline path swapped cleanly |
| M8 | most scripts | All 16 scripts now stamp `fetched_at` per call via `MassiveClient.get()` |
| D3 | most scripts | All 16 scripts now cite `api.polygon.io` exclusively |
| D5 | portfolio-mark | Now uses `resolve_price()` (canonical 4-step chain). Foundation doc still says 5 steps but that's covered by D4 |

---

## Dismissed / informational

| ID | Notes |
|---|---|
| N1 | SEC EDGAR `acceptanceDateTime` field IS UTC. The `Z` suffix is the explicit ISO 8601 UTC marker. Verified against AAPL's last 6 earnings 8-Ks: every acceptance time interpreted as UTC lands at exactly 16:30 ET (AAPL's standard print time). Internally consistent across DST: 20:30Z in EDT months (= 16:30 EDT), 21:30Z in EST months (= 16:30 EST). The migration subagent's concern was wrong |
