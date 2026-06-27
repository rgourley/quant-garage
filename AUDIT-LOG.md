# Audit closure log

Closure history for items from the 2026-06-26 deep audit. The active
scorecard lives in [`AUDIT.md`](./AUDIT.md); the original deep review
is preserved in `AUDIT-ORIGINAL.md`.

Closures are grouped by sprint wave; within a wave, items are ordered
by ID.

---

## Wave 10 — 2026-06-26 (skill-doc audit + lastQuote inline read)

Commit: `0647af1`.

| ID | Affects | Closure notes |
|---|---|---|
| D4 | massive-api-patterns | Doc claimed a 5-step chain whose first two steps (`snapshot.last.price`, `snapshot.lastTrade.p`) were duplicates, with `last.price` always returning `None` per D5's earlier finding. Doc also omitted the `ticker.` nesting that the actual v2 response uses. Fixed to the canonical 4-step chain matching `lib/quant_garage/snapshot.py::resolve_price` with full key paths (`snapshot.ticker.lastTrade.p`, `…min.c`, `…day.c`, `…prevDay.c`). FMV was incorrectly listed in the v2 waterfall; clarified as Business-tier stream-only, not on v2 REST. Dead links to non-existent `references/{endpoints,error-handling,throttling}.md` removed |
| D6 | earnings-drilldown, pitch-comps, options-flow, portfolio-mark, corp-actions-reconciler | Per-script SKILL.md audit. **earnings-drilldown:** 1 claim clarified (Tier A peer reaction is a methodology skip due to SIC misclassification; 3 analyses + optional 4th). **pitch-comps:** 4 stale claims (2 EV-math entries from pre-C11 dropped, 2 5-step waterfalls fixed to lib's 4-step). **options-flow:** 2 claims fixed (per-trade `/v3/quotes/{occ}` added; multi-leg conditions widened 232-240 → 232-245 to match `MULTI_LEG_CONDITIONS = range(232, 246)`). **portfolio-mark:** 4 fixes (channel preference order reversed to `T → AM → FMV`, 5-step delayed chain → 4-step, ADV pull retitled from `/v3/reference/tickers` to actual `/v2/aggs/.../range/1/day`, M7's Q-channel parallel subscribe documented). **corp-actions:** 2 fixes (RC/SC/SD/LT/ST dividend routing now described matching C9; "streams findings" claim dropped — single end-of-run report) |
| N2 | run-aapl-tier-b.py | Decision: keep `lastQuote.p` inline; do NOT extend `resolve_price()` to cover it. 8-line explanatory comment added above the read explaining why (quote-mid is a synthetic estimate from bid/ask, structurally different from a trade print; folding it into the trade-only chain would blur the contract for every other consumer; Tier B keys on quiet names depend on this fresh estimate before the snapshot falls back to a stale day close) |

## Wave 9 — 2026-06-26 (slippage-cost honest rename)

Commit: `bb6ae00`.

| ID | Affects | Closure notes |
|---|---|---|
| H9 | slippage-cost (renamed from best-ex-check) | The script measures fill vs NBBO at fill time. It does NOT compute true arrival-price Implementation Shortfall (which would require a pre-decision benchmark price the input CSV never carries). The "best-ex-check" name implied a best-execution audit; the work is actually slippage measurement. Honest rename across folder (`skills/best-ex-check` → `skills/slippage-cost`), reference script (`examples/run-best-ex-check.py` → `examples/run-slippage-cost.py`), `name:` frontmatter in SKILL.md, output-schema.json title/description, requires.yml, all references/*, README.md, PLAN-MATRIX.md, assets/skills.html, plus in-prose mentions in CONTRIBUTING and the lib timezones docstring. Suggested-next-action prose updated from "No clear best-ex violation" to "No clear fill-vs-NBBO violation" in the script and the rendering/flag-categories references. No behavior change |

## Wave 8 — 2026-06-26 (percentile rank + universe base rate)

Commit: `ada407f`.

| ID | Affects | Closure notes |
|---|---|---|
| M9 | options-flow, crypto-vol-scanner, news-scanner | New `lib/quant_garage/percentile.py` with `percentile_rank` (mean-rule, n<5 returns None, off-distribution clamps), `format_rank_label` (8-bucket map: `top 5%` → `bottom 10%`), `base_rate` (`{n, median, mean, p25, p75}` with linear-interp quantiles). Run-wide distributions per script: options-flow uses every qualifying print across every ticker; crypto-vol-scanner uses every scored event; news-scanner uses every impact-scored event pre-dedup. Each surfaced item carries `percentile_rank`, `rank_label`, `score_universe_n` in JSON and a `(top 5%, 87th %ile, n=247)` suffix in render. Small universes get `rank_reason: "insufficient_universe"` |
| M10 | event-study (single mode), earnings-drilldown | event-study gained `--with-base-rate` flag; when on, reuses existing `resolve_*` + `compute_event_returns` helpers against a 15-name mega-cap default (excluding subject ticker) to populate `universe_base_rate.by_metric` with `{n, median, mean, p25, p75}` for ar_t1/ar_t3/car_t5/drift_t3/drift_t5. Renderer shows per-metric "this vs universe median/p25/p75" lines. Flag off → schema-compatible `{reason: "live_universe_pull_disabled"}` + tier_caveat. earnings-drilldown is schema-only (no runnable script — only the static aapl example + output-schema.json); `universe_base_rate` property added with `reason` enum (`not_implemented_yet | live_universe_pull_disabled | live_universe_pull_returned_no_events`) |

## Wave 7 — 2026-06-26 (slippage-cost + corp-actions + portfolio-mark)

Commits: `7688094` (M1+M2), `54550ef` (M3+M4), `7682ce8` (M7).

| ID | Affects | Closure notes |
|---|---|---|
| M1 | slippage-cost | Three mutually exclusive buckets: `crossed_spread` (outside NBBO + slip > 20bps) > `off_nbbo` (outside NBBO + 0 < slip <= 20bps) > `on_nbbo` (at or inside the inside). Prior logic had no `on_nbbo` bucket at all; a fill 50bps past the ask got both `crossed_spread` and `off_nbbo` labels. Distribution now sums to 100% across the full population, not just breaks |
| M2 | slippage-cost | Tier A switched to per-fill `/v3/quotes?timestamp.lte={fill_ns}&order=desc&limit=1` with a 60-second backstop window for thin-quote names. Tier B (no quotes entitlement) keeps 1-second aggregate bars as proxy; bias direction documented in `tier_caveats` field and rendered footer: **under-counts off-NBBO and crossed_spread, over-counts on-NBBO** because the 1s band is wider than the instantaneous NBBO |
| M3 | corp-actions-reconciler | One consolidated record per ticker. Top-level `initial_shares` / `initial_cost_basis` / `final_shares` / `final_cost_basis` / `delta_shares` plus a chronological `adjustments[]` array and `break_state` (`'reconciled'`/`'partial'`/`'unknown_type'`). Spinoff subsidiary positions each get their own consolidated record. Rendered `BREAK N:` block now shows an adjustments timeline instead of N separate entries |
| M4 | corp-actions-reconciler | `SHARE_TOLERANCE = 1e-6` constant; `shares_equal(a, b)` helper wraps `math.isclose(..., abs_tol=SHARE_TOLERANCE)`. 4 call sites swapped (apply_split CIL, apply_dividend SD/LT CIL, apply_spinoff subsidiary CIL, main share_break compare). Basis comparison left at the existing cent tolerance. Eliminates spurious "break" flags from float drift after successive split-adjust multiplications |
| M7 | portfolio-mark | LiveRunner subscribes to the `Q.` quote channel in parallel with `T`/`AM`/`FMV`; `_handle_quote()` writes bid, ask, quote_as_of_utc, quote_count to per-symbol state. Prior unconditional `snapshot_mark(sym)` at line 740 (REST round-trip even when stream had the mark) is gone. 5-second startup grace (`STREAM_STARTUP_GRACE_SECONDS`); on grace exhaustion a single REST snapshot fallback runs and tags `mark_source = "snapshot.last_quote"`. New mark_source values: `stream.last_quote`, `snapshot.last_quote`. Inverted/zero quotes rejected. Delayed mode unchanged |

## Wave 6 — 2026-06-26 (pitch-comps OLS + shared annualization)

Commit: `9809e55`.

| ID | Affects | Closure notes |
|---|---|---|
| H5 | valuation-sanity-check, pitch-comps | New `lib/quant_garage/annualize.py` with `ltm_sum`, `annualize_quarter`, `operating_income`, `operating_income_annualized`, `da_annualized`. Both scripts now route every D&A and operating-income calc through the helper; EBITDA-derived numbers match across the two for identical input financials. `metrics.da_source` and `metrics.op_income_source` audit fields land in per-ticker JSON output. Source tag is one of `'LTM'`, `'Q4'`, `'unavailable'` |
| H4 | pitch-comps | OLS regression now emits `coef`, `se`, `t_stat`, `ci_lower`, `ci_upper`, `is_significant` per coefficient using df-aware `critical_t` from `lib.quant_garage.stats`. `MIN_PEERS_FOR_OLS = 5` floor; below that `regression_skipped: true, reason: "insufficient_peers", n, min_required` appears in output and the render emits a "Regression-adjusted (skipped)" block. **Endogenous regressor dropped:** `ebitda_margin` removed from the `ev_ebitda` regression only (EBITDA appears in y's denominator and the regressor's numerator — mechanical inverse, not economic signal). `ev_sales` and `p_e` keep both controls (no mechanical tie). `regressor_dropped: "ebitda_margin"` surfaces in the output |

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
| C8 | options-flow | NBBO at trade time via per-trade `/v3/quotes?timestamp.lte={ns}&order=desc&limit=1`. Trades pulled with `timestamp.gte/lte` window instead of `order=desc limit=200` so sweeps outside the recent window no longer silently downgrade. The `fetch_nbbo_at` helper kept inline (couldn't cleanly share with slippage-cost; different lookback windows and fetch wrappers) |
| C9 | corp-actions-reconciler | `apply_dividend()` now switches on `dividend_type`: RC (cash basis adj), SC (special cash, basis adj + flag), SD (stock dividend as fractional split), LT (large stock dividend as fractional split per IRS), ST (reshapes and routes to `apply_split()`). Unknown types append to `tier_caveats` rather than silently skipping |
| C10 | t+1-settlement-prep | Cum-dividend entitlement requires `trade_date < ex_dividend_date` strictly; ex-date trades emit informational "NOT allocated to buyer" notice instead of being flagged as entitled |
| L1 | event-study | SEC EDGAR 8-K item 2.02 fallback implemented (was stubbed `return []`); volume-spike mode-detection bug fixed (single ticker + window now resolves to aggregate, not single); script migrated to lib.quant_garage. Verified on AAPL single, mega-cap cross-section, mega-cap aggregate, and NVDA volume-spike |
| L2 | backtest-data-prep | Added `pyarrow>=15.0` to `requirements.txt` |
| L3 | earnings-drilldown (Tier B) | Closed in batch-3 migration (commit `2aa7724`); all 16 scripts now retry via `MassiveClient` |

## Foundation refactor — 2026-06-26 (lib.quant_garage + script migration)

Commits: `a062e90` (batch 1), `143b3f5` (batch 2), `2aa7724` (batch 3).

| ID | Affects | Closure notes |
|---|---|---|
| H1 | slippage-cost, news-scanner, portfolio-mark, earnings-drilldown | All 16 scripts now use `utc_to_et()` from zoneinfo. DST math correct year-round |
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
