# Review findings

Deep review of all 17 skills + 16 Python reference implementations,
2026-06-26. `npm run audit:requires` passes; these are
correctness/methodology issues the audit doesn't catch. Headline items
(C1, C2, C8, D1) spot-verified against source. Live-data validation
in progress on a Stocks/Options Business key.

Severity: **C** = corrupts the output number, **H** = high, **M** =
medium, **D** = doc/code drift (misleads anyone hand-coding from the docs).

---

## Live validation (2026-06-26, Stocks/Options/Currencies Business + all Benzinga)

Ran every example end-to-end on a real Business key.

| Skill | Result |
|---|---|
| universe-builder | PASS (curated default honest; `reference` path triggers H3) |
| options-flow | PASS — but direction tags rest on C8 (NBBO-at-trade not computed) |
| pitch-comps | PASS |
| valuation-sanity-check | PASS (args: `TICKER --target-price --assumed-growth --assumed-margin`) |
| portfolio-mark | PASS (delayed) |
| corp-actions-reconciler | PASS |
| t+1-settlement-prep | PASS |
| best-ex-check | PASS |
| news-scanner | PASS |
| earnings-drilldown (tier-b) | PASS; **first AAPL run died on `socket.timeout` with no retry** (see L3) |
| **event-study** | **FAIL — "No events matched" in earnings AND volume-spike modes. Non-functional out of the box.** |
| backtest-data-prep | PASS via REST fallback; **flat-files 403 → "NOT entitled" misdiagnosis (D1, live-confirmed)**; C1 confirmed in output |
| factor-research | not run (heavy universe walk) |
| crypto-vol-scanner | not tested (no Crypto entitlement on this stack — Currencies = FX) |

New findings from the live run:

- [ ] **L1 — event-study is non-functional.** Returns "No events matched"
  for both `earnings` and `large_volume_spike` even with full Benzinga
  Earnings entitlement. Root cause is H2 (hardcoded `TODAY`) + D6 (EDGAR
  fallback returns `[]`). The whole skill produces no output on a live key.
- [ ] **L2 — `requirements.txt` missing `pyarrow`.** backtest-data-prep's
  sole deliverable is a parquet file; with the documented requirements it
  crashes with "Unable to find a usable engine." **Fix:** add `pyarrow`.
- [ ] **L3 — earnings-drilldown has no retry on transient network error.**
  First AAPL Tier-B run died on `socket.timeout`; succeeded on retry. The
  global rule (retry-with-warning then raise) isn't applied. **Fix:** wrap
  fetches in the shared client's retry.
- [x] **C1 live-confirmed.** backtest `--survivorship clean` emits
  `delisted_during_window_count: 0`, "Active and delisted both included
  for survivorship cleanliness," take "point-in-time clean" — on the
  current top-100 universe with zero delisted names pulled.
- [x] **D1 live-confirmed.** flat-files path prints "flat-files: NOT
  entitled" on a Business key that *is* entitled; the S3 403 from
  `key==secret` creds is misread as an entitlement gap.

### Verification sweep (every Critical item, 2026-06-26)

Each Critical finding is now tagged **live** (reproduced on the API) or
**code** (confirmed at file:line). None of the Critical items failed to
reproduce. Two were refined.

| ID | Status | Evidence |
|---|---|---|
| C1 | **live** | backtest manifest: `delisted_during_window_count: 0` + "Active and delisted both included" |
| C2 | code | `run-factor-research.py:414` `mc_t = mc_now*(pt/pl)` |
| C3 | code | `:513` `mean_ic*sqrt(len)/std`, `ic_se_1m: None` at `:938` |
| C4 | code | `:451,456` `np.tile`; comment `:426` "quality score is constant across months" |
| C5 | **live** | `run-aapl-tier-b.py:316` anchors t5 at pre-print close; AAPL print: reaction −1.33% / "drift" −4.05% (drift swallows the gap) |
| C6 | code | `run-event-study.py:560,615` `abs(t) > 2.0 and n >= 8` |
| C7 | **live, upgraded** | GOOGL reverse-DCF fair value **$826** vs $343 spot (2.4x). Not just "verify" — the per-share output is implausible. Reverse-DCF + EV + share-count compound here. |
| C8 | code | `run-options-flow.py:165-173,398-411` single most-recent quote, day-VWAP classified against it |
| C9 | code | `run-corp-actions.py:178` `apply_dividend` acts on `RC` only |
| C10 | code | `run-t1-settlement-prep.py:338,346` flags + "allocated to buyer" at `trade_date == ex_date` |
| C11 | **live** | GOOGL `current_ev − current_mcap` = `8.47B` = exactly LTD; cash ignored. True EV should be *below* mcap (net cash). |
| C12 | **code + live, refined** | `run-valuation-sanity-check.py:259` prefers `share_class_shares_outstanding` (GOOGL 5.82B, Class A only) over `weighted_shares_outstanding` (12.2B true). Live output emitted mcap **$4.82T / 24.2B shares** vs true **$4.2T / 12.2B** — the dual-class per-share + mcap numbers are wrong. Effect on this run was overstatement, not the understatement originally claimed; either way the share-source selection is the bug. |

Net: filing-grade. The earlier universe-builder survivorship claim was the
only finding that needed walking back (path-dependent, now H3); everything
Critical holds.

---

## Critical: wrong or stale numbers on a real run

- [ ] **C1 — backtest-data-prep: `--survivorship clean` is fictional.**
  CLI default is `biased` (`run-backtest-data-prep.py:1009`); no
  `active=false` pull exists; universe comes from the active-only snapshot
  endpoint (`:247`). Delisted names can never enter the dataset, yet the
  render prints "Active and delisted both included for survivorship
  cleanliness" (`:773`). **Fix:** implement the `active=false` union, or
  flip the default to `biased` and delete the claim.

- [ ] **C2 — factor-research: value factor is look-ahead.**
  `mc_t = mc_now*(pt/pl)` (`run-factor-research.py:399-415`) uses today's
  market cap + share count at every historical rebalance. **Fix:** use
  point-in-time shares from `/v3/reference/tickers` (or the financials
  endpoint) at each rebalance date.

- [ ] **C3 — factor-research: IC t-stat inflated ~3x at long horizons.**
  3M/6M/12M forward returns on monthly rebalances overlap; `mean_ic*sqrt(T)/std`
  (`:513`) treats them as iid. `ic_se_1m` is always `None` (`:938`).
  **Fix:** Newey-West SE with lag = horizon months; emit `ic_se` and
  `n_months` per horizon.

- [ ] **C4 — factor-research: quality factor is constant across months**
  (`:446`, `np.tile`). Its "IC decay" is a return-autocorrelation artifact.
  **Fix:** compute quality per-rebalance from point-in-time fundamentals,
  or drop it from the horizon-decay table.

- [ ] **C5 — event-study + earnings-drilldown: "CAR"/"drift" includes the
  announcement jump.** Measured T0→T+5 from the pre-event close
  (`run-event-study.py:438-461`, `run-aapl.py:206-210`), so it's ~90% the
  print-day reaction. **Fix:** report announcement-excluded drift
  (T+1→T+horizon) as a separate field.

- [ ] **C6 — significance is `|t|>2.0` with no df correction**
  (event-study `:560,615`; earnings t-stat). At n=7-8 the 5% critical t is
  2.36. **Fix:** `scipy.stats.t.ppf(0.975, df=n-1)`.

- [ ] **C7 — valuation-sanity-check: EBITDA margin × peer EV/EBIT multiple.**
  Subject uses true EBITDA; peers fall back to EBIT when D&A is null
  (`run-valuation-sanity-check.py:188-194,615`). Unit mismatch. **Fix:**
  enforce same numerator both sides, or skip peers missing D&A.

- [ ] **C8 — options-flow: "NBBO at trade time" is never computed.**
  Pulls one most-recent quote (`/v3/quotes/{occ}?limit=1&order=desc`) and
  classifies a day-VWAP price against it (`run-options-flow.py:165-173,398-411`).
  Every direction tag rests on this. Trades pulled `order=desc limit=200`,
  so sweeps outside the last 200 prints downgrade silently. **Fix:** pull
  trades around the volume spike via `timestamp.gte/lte`, reconstruct NBBO
  at print time (copy `best-ex-check`'s `fetch_nbbo_at`).

- [ ] **C9 — corp-actions-reconciler: only `RC` dividends handled.**
  `apply_dividend` (`run-corp-actions.py:178`) acts on `RC` only; docs
  describe `SC/SD/ST/LT`. Special-cash and stock-dividend basis adjustments
  silently skipped. **Fix:** handle the full type set or document the
  scope cut honestly.

- [ ] **C10 — t+1-settlement-prep: ex-div entitlement direction wrong.**
  Fires `ex_dividend_in_window` and renders "allocated to buyer" when
  `trade_date == ex_date` (`:338,346`), where the buyer trades ex and is
  NOT entitled. **Fix:** entitlement = bought strictly before ex-date;
  scan window should be the true T+1 boundary, not the holiday-extended
  settle date.

- [ ] **C11 — valuation + comps: EV = mcap + long-term-debt only.**
  No cash, leases, or minority interest (both skills). Overstates EV for
  cash-rich mega-caps. **Fix:** EV = mcap + total debt − cash (+ leases,
  minority where available).

- [ ] **C12 — valuation: dual-class share count understates mcap ~2x.**
  Prefers `share_class_shares_outstanding` over weighted diluted
  (`:259-260`). **Fix:** use weighted-diluted shares.

---

## High

- [ ] **H1 — DST: `EASTERN = UTC-4` / `ET_OFFSET_HOURS = -4` hardcoded**
  in best-ex-check, news-scanner, portfolio-mark, earnings (`run-aapl.py:176`).
  Wrong by an hour Nov–Mar → wrong NBBO bar / session bucketing. **Fix:**
  `zoneinfo.ZoneInfo("America/New_York")`.

- [ ] **H2 — hardcoded `TODAY`** (`2026-06-23` in most, `2026-06-24` in
  event-study) drives stale universes while `fetched_at` uses real now.
  **Fix:** single `as_of = date.today()`.

- [ ] **H3 — universe-builder + factor: survivorship "clean" rendered as
  fact** while no delisted name is fetched. **Path-dependent (live-verified
  2026-06-26):** the default `--candidate-source curated` correctly reports
  `biased`. The bug is the `--candidate-source reference` + lookback path
  (`run-universe-builder.py:922-928`), which sets `mode:"clean"` with
  `delisted_in_window:0` and renders "Survivorship: clean. Delisted names
  retained" (`:1122`) while the note itself admits `active=false` is "queued
  for next PR." factor-research `:900` deletes delisted names via the
  `coverage>=0.8` filter. **Fix:** tie the label to whether delisted names
  were actually pulled.

- [ ] **H4 — pitch-comps: OLS regression with no SE/t-stat/CI.**
  4-7 points, 2 regressors, emits a DoF=1 perfectly-fit line
  (`run-pitch-comps.py:342`); the documented "skip n=4" rule isn't
  enforced. Fails the repo's own methodology bar. Also regresses EV/EBITDA
  on EBITDA margin (mechanical endogeneity). **Fix:** report SEs/t-stats,
  enforce min-n, drop the endogenous regressor.

- [ ] **H5 — EBITDA partial-quarter D&A annualization bug** copy-pasted in
  valuation (`:189`) and pitch-comps (`:207`). **Fix:** consistent
  annualization of D&A and operating income (fix once in shared lib).

- [ ] **H6 — options-flow: `vol_oi_ratio` set to raw volume when OI=0**
  (`:342`), inflating score for new strikes. **Fix:** cap or flag
  zero-OI separately.

- [ ] **H7 — crypto-vol-scanner: ragged "24h move" + tiny-sample σ.**
  Numerator now-vs-last-UTC-close (0-48h), denominator clean 1-day σ
  (`:315`); `sigma_30d` fires at n≥5 but labeled 30d (`:325`); 30d avg
  volume includes today's partial bar (`:330`). No `n=` emitted. **Fix:**
  fixed 24h lookback, min-sample guard, exclude partial day, emit `n`.
  (Note: needs Crypto entitlement — not on the current Business stack.)

- [ ] **H8 — news-scanner: reaction baseline off-by-one + out-of-range
  volume window.** `find_bar` returns the bar up to 5 min before publish
  (`:313-325`); volume baseline walks dates outside the fetched agg range
  (`:415-434`). **Fix:** anchor reaction to first bar at/after publish;
  fetch a wide enough window for the 5-day volume baseline.

- [ ] **H9 — best-ex-check: `implementation_shortfall_usd` mislabeled.**
  It's `|slip|*notional` (slippage-vs-inside), not arrival-price IS
  (`run-best-ex-check.py`). A PM reads "implementation shortfall" and
  expects arrival-price IS. **Fix:** rename, or compute true IS vs arrival.

- [ ] **H10 — portfolio-mark: `day.v` used as ADV** (`:321,339`) → false
  `low_adv` flags every morning. **Fix:** pull 30-day ADV from reference,
  or scale threshold by time-of-day.

---

## Medium

- [ ] **M1 — best-ex-check: `off_nbbo` always co-fires with `crossed_spread`**
  (double-counts in `by_reason`). **Fix:** make off_nbbo exclusive of the
  crossed-spread bucket.
- [ ] **M2 — best-ex-check: 1-sec-trade-bar-as-NBBO-proxy understates
  spread** (trade range is inside the quote, not the quote spread). Doc's
  "conservative" reasoning is backwards. **Fix:** use the quotes endpoint
  for the proxy, or document the bias direction correctly.
- [ ] **M3 — corp-actions: multi-action positions emit intermediate share
  counts as breaks with phantom deltas** (`:500-545`). **Fix:** one
  consolidated break with final state.
- [ ] **M4 — corp-actions: raw float-equality break test** throws false
  breaks on fractional-share books (`:475`). **Fix:** tolerance compare.
- [ ] **M5 — universe-builder: top-quartile off-by-one** (`:657`,
  `*0.75 - 1` plus `>=` keeps ~26%). **Fix:** index cleanly.
- [ ] **M6 — universe-builder: concentration baseline computed three
  different ways** across curated/grouped paths. **Fix:** one baseline
  definition.
- [ ] **M7 — portfolio-mark: live mode double-fetches REST per streamed
  symbol** (`:646-650`), defeating "one connection serves the book." **Fix:**
  use the streamed quote, skip the REST round-trip.
- [ ] **M8 — `fetched_at` is one import-time stamp, not per-call** (all
  examples). Per-number provenance is claimed but it's per-run.
- [ ] **M9 — scores have no distributional context** (options-flow, crypto,
  news emit composite/impact scores with no percentile/base-rate on the
  score itself). Violates the methodology bar.
- [ ] **M10 — single-name skills have no universe base rate**
  (earnings-drilldown, event-study single mode). Machinery exists in the
  cross-section path.

---

## Doc / code drift

- [ ] **D1 — massive-flat-files: S3 `key` and `secret` both set to
  `MASSIVE_API_KEY`** (`SKILL.md:90-91`). Polygon issues a separate S3
  access key + secret in the dashboard; as documented every call 403s.
  The "entitlement gotcha, contact support" note misdiagnoses this. **Fix:**
  document the separate S3 keypair.
- [ ] **D2 — WS status enum**: fallback keys off `status == "not_authorized"`
  (massive-websockets, `websocket-mark-updates.md:113-116`) but the real
  enum is `success/error/auth_success/auth_failed` with "not authorized"
  in the message text. portfolio-mark code is right; docs are wrong. **Fix:**
  align docs to code.
- [ ] **D3 — base URL + auth**: prose says `api.massive.com`, examples
  hardcode `api.polygon.io`, `next_url` pagination uses `&apiKey=` while
  primary calls use `Authorization: Bearer`. **Live-verified 2026-06-26:
  both hosts answer 200, so this is cosmetic drift, not a break.** Still:
  citation strings cite a host the code never calls. **Fix:** one host,
  one auth scheme for consistency + honest citations.
- [ ] **D4 — snapshot fallback field paths** in api-patterns
  (`snapshot.last.price`, `snapshot.min.c`) don't exist on the v2
  response (everything nests under `ticker.{lastTrade,min,day,prevDay}`).
  portfolio-mark reads the correct paths. **Fix:** correct the foundation doc.
- [ ] **D5 — portfolio-mark fallback step 1 (`snapshot.last.price`) is a
  no-op duplicate** of step 2 (`lastTrade.p`) (`:128-129`). Chain is 4
  steps, not 5. **Fix:** collapse.
- [ ] **D6 — documented-but-unimplemented machinery**: EDGAR earnings
  fallback (event-study/earnings), correlation peer layer + R²-suppression
  + EPS substitution (pitch-comps), IV30 percentile + expiry-distance flag
  (earnings), `oi_post_trade_estimate` (options-flow), the "exposure at
  <high confidence" footer (portfolio-mark), reverse-split delisting watch
  (corp-actions). **Fix:** implement or strike from SKILL/schema/references.

---

## Post-fix findings (live on ALLO, 2026-06-27)

Found by actually running the suite on ALLO (Allogene, $2.12, $711M biotech)
after the fix waves. New, not in the original list.

- [ ] **P1 (Critical) — SIC peer-selection is non-functional; comps + valuation
  get alphabetical garbage peers for any non-curated name.** Massive's
  `/v3/reference/tickers?sic_code=2836` **ignores the `sic_code` param** and
  returns `A, AA, AACB, AACI, ... AAL` alphabetically. `run-pitch-comps.py:730-738`
  takes the first 8 → ALLO's "biotech comps" came back as American Airlines +
  SPAC shells. Affects pitch-comps AND valuation-sanity-check (shared peer
  logic) for every ticker outside the ~30-name curated override. **Fix:**
  filter by `sic_code` client-side (the field is on each ticker record), or
  build the documented correlation-based peer path. The methodology doc admits
  SIC is weak but the *code* falls back to a SIC filter that doesn't even run.
- [ ] **P2 (High) — news-scanner returns 0 events when news demonstrably
  exists.** ALLO at a 90-day window: 0 events, but `/v2/reference/news?ticker=ALLO`
  has an article dated 2026-04-15 (a dilutive offering) inside that window. The
  candidate-gathering path isn't surfacing real, in-window articles. Also still
  prints "Benzinga insights not available" on a key that holds Benzinga News.
- [x] **P3 — risk-report + earnings-drilldown work well on a small-cap.**
  risk-report: ALLO vol 86.6%, beta 2.48, max DD −41.8% (not recovered),
  worst day −29.4% around the Apr offering. earnings-drilldown: clean 8-quarter
  print/reaction history, correct GAAP-loss handling, honest Tier-B caveats.
  These two are genuinely usable today.

## Structural fix (do first)

16 standalone scripts with copy-pasted infra that has diverged (3 DST
implementations, hardcoded `TODAY` at two different dates, two auth
schemes, `polygon.io` vs `massive.com`). Extract a shared `massive/`
client: one auth scheme + `next_url` pagination + retry; `as_of =
date.today()`; `zoneinfo` ET helpers; one snapshot fallback chain; one
stats module (df-aware critical-t, Newey-West, Spearman IC + SE,
winsorize). Resolves C2/C5/C6 prerequisites, H1, H2, H5, D3, M8 in one
pass.

## Suggested order
1. Shared client refactor (H1, H2, D3, M8 + sets up the stats fixes).
2. Survivorship truth: C1, H3, C4.
3. Drift/significance: C5, C6.
4. options-flow NBBO-at-print: C8.
5. Valuation/comps: C7, C11, C12, H4, H5.
6. Foundation doc cleanup: D1–D5.
7. Strike or implement D6.

---

## Live validation (2026-07-02, Stocks Starter key — mid-cap basket)

Fresh checkout of `main` at `eeb5895`. Installed `pip install -e '.[research]'`
into a local venv. Ran a spread of tools against a mid-cap watchlist
(HOOD, SOFI, ROKU, SNAP, PINS, RIVN, DKNG, AFRM) plus SPY-only for regime.

| Skill | Result |
|---|---|
| market-regime | PASS (VIX unavailable on key, handled with caveat) |
| technical-briefing (HOOD, SOFI) | PASS |
| relative-strength (8-name mid-cap watchlist) | PASS |
| earnings-blackout (same watchlist, 21d window) | PASS (past-only; Benzinga not on key) |
| event-study (RIVN, most_recent earnings) | PASS — 18-prior comparison landed clean |
| valuation-sanity-check (HOOD, SOFI, AFRM, ROKU, PINS) | PASS with caveats — see Q1–Q6 below |

### New findings

- [ ] **Q1 (High) — peer resolver misses obvious sector clusters.**
  `--peers` had to be hand-fed for both **AFRM** and **ROKU**. Their SIC
  codes (6141 personal credit, 4841 CATV) are too narrow, and
  `/v1/related-companies` returned zero SIC-validated matches even with 10
  candidates checked. This forces the analyst to know the peer set before
  they can use the tool. Suggest a curated fallback map for the top ~200
  most-scanned tickers (BNPL: PYPL, XYZ, UPST, BFH, SEZL; CTV/streaming:
  NFLX, DIS, WBD, TTD, FUBO; etc.) applied *before* punting to `--peers`.

- [ ] **Q2 (Medium) — delisted / renamed tickers silently 404.** In the
  live run, `LC` (LendingClub, renamed) and `PARA` (Paramount, merged into
  PSKY) both 404'd on `/v3/reference/tickers/{ticker}`. The tool prints a
  WARN and keeps going, so the peer set shrinks silently. Suggest either
  a rename map (LC→LTRE, PARA→PSKY, SQ→XYZ, FB→META, TWTR→X, etc.) or
  surface "n peers requested, m loaded" in the render layer so the user
  notices the dropout.

- [ ] **Q3 (High) — Monte Carlo threshold is `n>=5` on every dimension.**
  Even when growth and exit-multiple pass the threshold, if margin is
  `n=None` (all peers have non-positive EBITDA, common for growth
  cohorts), MC skips entirely. Ran cleanly on **SOFI** with 6 peers
  (n=10,000 samples, useful percentiles). Skipped on HOOD (n=2), PINS
  (n=4), AFRM (margin n=None), ROKU (margin n=2). **Fix:** degrade
  gracefully — run MC on the dimensions that clear n≥5, use point-estimate
  or peer-single for the ones that don't, print which dimension was
  point-estimated. All-or-nothing is the wrong default here.

- [ ] **Q4 (Medium) — reverse-DCF drops the margin term when subject
  EBITDA is non-positive, without surfacing it in the take.** For every
  EBITDA-negative subject in the basket (HOOD, SOFI, AFRM, PINS), the
  tool silently switched exit multiple to EV/Sales and dropped the margin
  assumption from the fair-value math. The footnote does say
  `Exit multiple: EV/Sales (auto-selected: subject EBITDA non-positive);
  margin term dropped from formula` but the human-facing "Take:" still
  quotes the analyst's assumed margin as if it drove the number. **Fix:**
  either recompute with the assumption suppressed and say "at your
  target, margin is unused because EBITDA<0", or refuse to accept a
  margin arg when the subject is loss-making.

- [ ] **Q5 (Medium) — the "Take:" generator has inconsistent language
  when target-implied multiples disagree with peer bands.**
  - HOOD implied EV/Sales **below** peer band → "target's implied
    multiples sit at or below the cohort; the gap usually means the
    thesis is undemanding".
  - SOFI implied EV/Sales, EV/EBITDA, and P/E all **above** peer band →
    "Defensible if you accept the multiple premium."

  But SOFI's MC put the target at the **80th percentile** of the
  peer-derived distribution (spot at 76th). That is "priced above the
  IQR, requires top-quartile execution" (which is exactly what the
  translation line says two paragraphs down). The Take line should
  match the MC verdict when MC is available, not hedge into
  "Defensible". Suggest: when `mc` fires, let the MC percentile drive
  the take verb.

- [ ] **Q6 (Doc) — flat-files S3 auth WARN prints on every skill
  invocation, even skills that never touch S3.** Every one of the runs
  above (market-regime, technical-briefing, relative-strength,
  earnings-blackout, event-study, valuation-sanity-check) opened with:

  > WARN: flat-files S3 auth is falling back to MASSIVE_API_KEY as both
  > access_key_id and secret_access_key. This pattern usually returns
  > 403. Generate distinct S3 credentials in your Massive dashboard...

  This is D1 territory — the shared client is emitting the S3 warning at
  import time rather than only when a flat-files call is actually made.
  It's log spam and it primes new users to worry about a 403 they'll
  never see if they're not using flat-files. **Fix:** move the WARN
  behind a lazy check that fires only when `boto3`/`s3fs` is actually
  used, not on client construction.

### Non-issues worth calling out (things that worked)

- **event-study most_recent on RIVN**: fine. Fetched 18 prior earnings,
  computed CARs, benchmarked this print (−15.3% at T+5) at the 11th
  percentile of the prior distribution. This is the same skill that was
  flagged L1 as "non-functional" in the 06-26 sweep — the fix landed.
- **relative-strength composite scoring**: the "deteriorating" tag on
  HOOD/AFRM (strong short-window RS, negative 120d RS) is a genuinely
  useful diagnostic. Composite percentile ranks matched intuition.
- **market-regime graceful VIX handling**: on a key without VIX
  entitlement, the skill computes the rest of the regime read and puts
  the missing component in caveats rather than failing. Good pattern
  to preserve.
