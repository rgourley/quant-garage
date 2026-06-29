# Audit closure log

Closure history for items from the 2026-06-26 deep audit. The active
scorecard lives in [`AUDIT.md`](./AUDIT.md); the original deep review
is preserved in `AUDIT-ORIGINAL.md`.

Closures are grouped by sprint wave; within a wave, items are ordered
by ID.

---

## Wave 16 — 2026-06-29 (4-skill expansion: daily-use trader workflows)

Closing the gap vs Jow-Dones-class data-MCP products. Four new skills built in parallel + new technicals lib + cross-cutting brand updates. Skill count 16 → 20.

Commits: `933a8f3` (technicals lib), `74b7997` (earnings-blackout), `570c0c9` (relative-strength), `f831eaf` (market-regime), `ce7efc0` (technical-briefing).

| ID | Affects | Closure notes |
|---|---|---|
| TECH-LIB | lib | New `lib/quant_garage/technicals.py` with SMA, EMA, RSI (Wilder), MACD (12/26/9), Bollinger (20/2σ), ATR (Wilder 14). All standard textbook math, numpy arrays aligned to input length with leading NaNs. Smoke-tested on 20-bar synthetic close series. Wired through `__init__.py`. Reusable for any future technicals-aware skill |
| TECH-BRIEFING | technical-briefing (new) | Single-name technical briefing. Pulls 252 daily bars + snapshot, computes all 6 indicators via lib helpers, derives 5-bucket composite trend regime with explicit reasons, RSI-bucketed momentum read, Bollinger position label, ATR as % of price, ADV-bucketed liquidity (thin/medium/liquid/mega). 18-key adaptive take map keyed on (regime, momentum) with neutral fallback. Trend gate refined from spec: bullish_strong / bearish_strong allow RSI ≥ 50 (or ≤ 50) rather than strict 50-70 range, because monotone uptrend produces RSI > 70 which is trend *confirmation* not contradiction |
| MARKET-REGIME | market-regime (new) | Daily macro briefing. Pulls SPY + VIX + 11 SPDR sector ETFs. SPY trend (5 buckets via SMA stack), VIX state with percentile rank vs trailing year (graceful fallback if VIX → I:VIX → caveat), breadth proxy from sector ETF % above 50-day / 200-day, 20-day RS leadership ranking. Composite regime label (risk_on, risk_off, mixed_risk_on, mixed_risk_off, neutral) with explicit reasons[]. Leadership rule refined from spec: "at least 2 of {XLK, XLY, XLC} in top-3" rather than "#1 must be growth" — avoids flipping composite on 1-bp differences. Breadth caveat explicit (sector-ETF proxy, not full A/D line) |
| EARNINGS-BLACKOUT | earnings-blackout (new) | Lightweight watchlist scanner. Resolver helpers inline-copied from event-study's wave-13 pattern (Benzinga Tier A → SEC EDGAR 8-K item 2.02/7.01/8.01 fallback, CIK via Massive primary + SEC ticker.txt fallback). 7 status buckets (blackout_imminent / blackout_soon / blackout_extended / just_printed / recent_print / clear / unresolved). Exception-report rendering groups by status with imminent first. Unresolved tickers surfaced explicitly to avoid silent omission. Runs on Stocks Basic (EDGAR fallback is free) |
| RELATIVE-STRENGTH | relative-strength (new) | Watchlist RS ranker. Pulls daily aggs per ticker + benchmark, computes RS in basis points per window (default 5/20/60/120 days). Composite percentile rank within-watchlist (not against universe baseline — that's factor-research's job). 5 trend labels (stable_leader, improving, deteriorating, stable_laggard, mixed) based on head-of-series (5/20/60d) gradient. Optional `--include-sectors` adds the 11 SPDR ETFs to the ranking for sector-relative context |
| CROSS-CUT | top-level README, PLAN-MATRIX, assets | README gains entries for all 4 new skills in appropriate sections (Earnings work / Quant research / new Market context section). PLAN-MATRIX adds 4 new rows + earnings-blackout to the free-Basic-tier runnable list (bumped to 6). assets/skills.html + og.html + closing.html bumped to 20 SKILLS and the 4 new names interleaved into the list. PNGs re-rendered |

**Em-dash note:** Five commit subjects in this wave carry em-dashes (carried over from my spec templates to subagents). My fault — the no-em-dash rule applies to prose output and I should've stripped them in the specs. Local-only history rewrite was blocked by the auto-classifier; leaving as-is since the cost (em-dashes in 5 git log subjects) is lower than the friction of authorizing a history rewrite. Going forward, specs will use colons instead.

## Wave 15 — 2026-06-27/28 (silent caps + EV/Sales + brand pass)

Commits: `d444cc0` (news-scanner pagination), `95e145a` (D1 script-level S3), `e0c4082` (Massive rebrand), `38f32f3` (valuation EV/Sales), `319669d` (multiple-selection reference), `2032cd7` (pitch-comps EV/Sales), `07de56c`/`2d30581` (OG copy + PNG), `a8c8bd5` (universe truncation).

| ID | Affects | Closure notes |
|---|---|---|
| PAGINATION | news-scanner | `max_pages=4 × limit=50` silently truncated high-news megacaps (NVDA/TSLA) to most-recent 200 articles over windows >7 days. Now `MAX_PAGES_PER_TICKER = max(4, int(window_days * 25 / 50) + 1)` — 90-day window gets 46 pages × 50 = 2300 articles. Inline cap-hit detection (last-page-was-full heuristic) drives a `tier_caveat` + `payload.pagination_status.capped_tickers[]` audit field |
| D1 | backtest-data-prep | The wave-4 D1 doc fix updated SKILL.md but the script still used `MASSIVE_API_KEY` as both S3 access_key_id and secret_access_key (guaranteed 403). Now reads `MASSIVE_S3_ACCESS_KEY` + `MASSIVE_S3_SECRET_KEY` (canonical brand-aligned env vars) with `POLYGON_S3_*` as legacy fallback. Loud WARN names the misconfiguration and links to massive.com/dashboard. 403 handler tailors message based on which auth source was used |
| BRAND | backtest-data-prep, massive-flat-files docs | Polygon → Massive in user-facing strings: dashboard link, env var names, WARN/INFO messages, SKILL.md examples. Technical URLs (`files.polygon.io`, `api.polygon.io`) stay unchanged — those are the real endpoints regardless of brand |
| EVSALES | valuation-sanity-check, pitch-comps | Auto-switch to EV/Sales when subject EBITDA non-positive. valuation-sanity-check: `--multiple {ev_ebitda,ev_sales,auto}` (default auto); `reverse_dcf_implied_cagr`, `fair_value_at_peer_median`, and `compute_mc_fair_value` all branch on `multiple_kind`; EV/Sales mode drops the margin term from the formula AND the MC sensitivity output; MC peer driver pool sources `peer_ev_sales` instead of `peer_ev_ebitda`. pitch-comps: per-multiple valid peer count with `MIN_PEERS_PER_MULTIPLE = 4` floor; multiples below the floor dropped from take consideration + named in tier_caveats; primary lens auto-selected (EV/EBITDA if subject profitable AND enough peers, else EV/Sales, else first available); `payload.multiple_selection` surfaces `primary`, `primary_source`, `valid_multiples`, `dropped_multiples`, `peer_counts`. Render leads with "Primary valuation lens: X (why)" callout. Methodology reference at `skills/valuation-sanity-check/references/multiple-selection.md` |
| OG | assets/og.html, assets/og.png | OG headline 'Sell-side analysis.' → 'Analyst workflows.' Matches the broader audience framing in README intro + LinkedIn post + GitHub About blurb. "Sell-side" was too narrow for the explicit retail / indie-dev / power-user targets. Kept the "desk" pun as the punchline |
| UNIVERSE | factor-research, universe-builder | `fetch_all_rest`/`fetch_all` silently truncated /v3/reference/tickers at 2000 / 100 alphabetical names respectively. The real US common-stock universe is ~5000-8000; truncation meant factor IC numbers reflected only A-D tickers, and universe-builder's screens ran on the first 100 alphabetical names. Hard cap raised to 15000 in both helpers (safely above real universe). Helpers now return `(rows, truncated)` so callers detect cap-hit. universe-builder's `--candidate-cap` default bumped 100 → 15000 with help text rewritten ('hard ceiling, not a target' + alphabetical-bias warning). Both scripts log 'Universe candidates pulled: N (TRUNCATED if applicable)' to stderr. factor-research's `universe_definition` JSON gains `universe_size` and `universe_truncated`. `MassiveClient.paginate` confirmed to have NO internal row cap so the script-level hard_cap was the only ceiling in the chain |

## Wave 14 — 2026-06-27 (factor-research hardening)

Wave 3 closed the methodology bugs (C2/C3/C4), but the script's silent-failure modes on Massive's sparse balance-sheet feed stayed unaddressed.

Commit: `d504cff`.

| ID | Affects | Closure notes |
|---|---|---|
| FACTOR | factor-research | (1) Field-name fallback chains matching the C11.b pattern via shared `_pick_from_filing()` helper. Net income: `net_income_loss → net_income → net_income_from_continuing_operations`; equity: `equity → stockholders_equity → total_equity → common_stockholders_equity`; revenue: `revenues → revenue → total_revenue → net_revenue`. Filings carry `*_source` audit tags. Cache bumped to `fundamentals_pit_v2.json` so a stale v1 cache doesn't poison new runs. (2) Per-rebalance coverage diagnostics at `payload.per_rebalance_coverage[]` with `n_scored`, `coverage_pct`, `dropped_for_reason` per factor per rebalance. Drop reasons are factor-specific (`missing_equity`, `negative_or_zero_equity`, `roe_out_of_bounds`, `insufficient_history`, etc.). (3) Thresholds: `coverage_pct < 0.50` fires a tier_caveat; factors stuck below 10% on ALL rebalances are omitted from the IC summary entirely (10% chosen because n≈40 of an intended n=400 is misleading enough to omit, matching the spec's intent better than my over-stated 30%). Module-level constants `_COVERAGE_CAVEAT_PCT` and `_COVERAGE_DROP_PCT` for retuning. (4) IC n surfacing: `n_pairs_per_rebalance` per horizon per factor as `{date, n_pairs, ic}` entries so consumers see the cross-section size driving each per-month IC; `n_months_{h}m` already per horizon; `n_months < 6` now surfaces a tier_caveat explicitly (previously silently returned None for SE/t-stat). (5) New `--debug-factor` flag dumps full per-rebalance coverage + IC raw distribution + NW SE inputs per horizon. (6) **Bonus: added Size factor** (`-log(market_cap)` point-in-time) — script previously had 4 factors (Momentum, Low-Vol, Value, Quality); spec listed 5; subagent added Size with the same fallback semantics. Renderer rewritten from hardcoded 3+1 layout to groups-of-3 loop so any factor count (including after drops) renders cleanly. Methodology unchanged: Newey-West SE, point-in-time mcap, per-rebalance quality from wave 3 are untouched |

## Wave 13 — 2026-06-27 (event-study no-events diagnosis)

Real-world ALLO test returned "No events matched the input criteria." with no indication which step failed. Three causes collapsed into one silent error.

Commit: `d40323b`.

| ID | Affects | Closure notes |
|---|---|---|
| EVENT | event-study | (1) Per-resolver diagnostics via module-level `_RESOLVER_DIAGNOSTICS[ticker][stage]` — chose side-effect dict over rewriting return signatures because resolvers are called from 3+ sites. Records cik_found / raw_filing_count / matched_filter_count / after_date_filter_count / failure_reason per stage. `build_no_events_message()` consumes the dict to emit a per-ticker breakdown instead of the generic error. (2) 8-K item filter expanded from 2.02-only to `2.02 \| 7.01 \| 8.01`. Each event tagged with `item_code` + `signal_strength` (`strong` for 2.02 — standard earnings release; `soft` for 7.01 Reg FD and 8.01 Other Events). `generate_take_single` prefixes soft-signal takes with a caveat so the operator sees it before any return statistic. (3) SEC ticker.txt fallback: when Massive's `/v3/reference/tickers/{T}.cik` returns None, `get_cik` queries `https://www.sec.gov/files/company_tickers.json` (verified live: 10,433 entries, ALLO present, no auth, ~800KB). Cached module-level. (4) New `--debug-resolver` flag dumps the full diagnostics dict per ticker for edge-case investigation. (5) `_edgar_cache` patched to replay diagnostics on cache hits so repeated calls still surface the same failure reason |

## Wave 12 — 2026-06-27 (peer selection + news window + options direction)

Three real-world bugs surfaced by user testing on ALLO (CAR-T biotech). All three were silent failures that produced output looking correct.

Commits: `253c5f7` (news-scanner), `ed0f4c2` (peer selection), `ab1284c` (options-flow direction).

| ID | Affects | Closure notes |
|---|---|---|
| H8.b | news-scanner | The fetch window was hardcoded to `NOW - 7 days` regardless of `--hours`. Any window > 7 days silently shrank to 7. ALLO 90-day scan returned 0 events despite a verified 2026-04-15 article in Massive's news endpoint (73 days back). Renamed misleading `NOVELTY_BUCKET_START_UTC` to `NEWS_FETCH_START_UTC = NOW - max(--hours, 7d)`. Novelty scoring is corpus-based (not timestamp-cutoff-based), so longer windows actually IMPROVE re-run detection. Follow-up flagged but not fixed: pagination cap (`max_pages=4 × limit=50 = 200`) silently truncates high-news megacaps over long windows; recommend scaling `max_pages` to `ceil(window_hours/24/5)` or emitting a `pagination_capped` warning |
| PEER | pitch-comps, valuation-sanity-check | `/v3/reference/tickers?sic_code={sic}` silently ignored the `sic_code` query parameter and returned the universe alphabetical. ALLO (SIC 2836, Biological Products) came back with `['A','AA','AACB','AACI','AACO','AACP','AADX','AAL']` — American Airlines and SPAC shells as "biotech peers". Affected ~11,900 tickers not in the curated override map. New `lib/quant_garage/peers.py` with `select_peers(client, ticker, n, validate_sic)` using `/v1/related-companies/{ticker}` as primary path (included in all Stocks plans, hybrid news-co-occurrence + behavioral matching). Optional SIC cross-validation via per-candidate `/v3/reference/tickers/{T}` drops mismatched candidates. Empty result → raises `ValueError` with `--peers` override hint; NO alphabetical fallback. `peer_result.method` audit field tracks `related_companies` vs `related_companies_sic_validated`. tier_caveats surfaces `subject_sic`, `n_candidates_pre_filter`, `n_dropped_sic_mismatch`. Both scripts adopt the lib helper |
| C8.b | options-flow | Wave-1 C8 wired NBBO-at-trade-time correctly, but when options quotes are unavailable (entitlement gap on keys without Options Developer, sparse-quote contracts, OCC format mismatch), every trade silently degraded to tag=`unknown` → direction=`unknown` → rendered as OTHER. `classify_trades_against_nbbo` now counts `n_total_trades`, `n_with_nbbo`, `n_missing_nbbo` and emits per-print `nbbo_availability`. Trade-price-percentile fallback heuristic per contract: trade ≥ p75 of the day's price distribution → `above_ask` (heuristic-tagged); ≤ p25 → `below_bid`; else `at_mid`. Per-trade `direction_method` field is `nbbo_inside`, `trade_price_heuristic`, or `unknown`. Dominant-direction tally half-weights heuristic-method votes so NBBO wins when both are present. Contract-level `direction_confidence` (`high` ≥80% NBBO, `medium` 50-80%, `low` <50%, `unknown`) + `direction_method_mix` for audit. Run-level tier_caveats fires with an Options Developer upgrade hint when >50% of trades fell back to heuristic |

## Wave 11 — 2026-06-27 (C11 follow-up: loosen EV strict-required)

Commit: `40b5500`. Follow-up to the C11 closure in wave 2.

| ID | Affects | Closure notes |
|---|---|---|
| C11.b | pitch-comps, valuation-sanity-check | Wave 2's C11 close made cash + total_debt strictly required in `compute_ev_components` (raised `NotImplementedError` if absent). Real-world feedback showed Massive's `/vX/reference/financials` doesn't populate either field for most names (verified on AAPL — no `cash` field, only `long_term_debt`). Result: most peers got dropped silently, EV/EBITDA band collapsed to n=0-1, and `--mc` mode in valuation-sanity-check aborted with `exit_multiple n=0`. Loosened both to fallback chains: cash tries `cash → cash_and_cash_equivalents → cash_and_short_term_investments → cash_short_term_investments` then defaults 0; total_debt tries reported `total_debt → synthesized LTD+STD → LTD-only → STD-only` then defaults 0. Each peer carries `cash_source` and `debt_source` audit fields. `tier_caveats` fires when ≥30% of peers used any fallback path. Only `mcap` stays strictly required. MC mode regains functionality because exit_multiple distribution no longer collapses |

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
