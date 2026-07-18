---
name: rate-signal
description: Rates-only deep dive on the Treasury curve. Pulls four liquid Treasury ETFs (SHY short end, IEF belly, TLT long end, TIP inflation-protected) over one window and decomposes the curve the way a rates desk does: curve slope (SHY vs TLT, four-way bull/bear steepening/flattening), real yields (TIP vs IEF), break-evens (IEF vs TIP, inflation expectations rising/falling), and long-end-vs-belly momentum divergence (TLT vs IEF). The four reads resolve into one four-way curve label plus a confidence score (high when the curve, real-yield, and momentum reads agree; low when they conflict). The take is a one-sentence rates note. Use when the question is the curve itself: "is the curve steepening", "bull or bear flattening", "are real yields rising", "what are break-evens doing". For the broad cross-asset dashboard (credit, dollar, gold, commodities too) use macro-basket. Runs on any stocks tier (Free Basic works with --sleep 13).
---

# rate-signal

macro-basket gives you the whole cross-asset tape in one dashboard: rates,
credit, the dollar, gold, commodities. rate-signal drops the rest and
zooms into one thing: the Treasury curve. It is the detail behind the
single curve line macro-basket prints.

You run it and get the four reads a rates desk actually watches:

- **Curve slope (2s10s proxy).** SHY (short end) versus TLT (long end),
  combined with rate direction into the four-way label: bull steepening,
  bull flattening, bear steepening, bear flattening.
- **Real yields.** TIP versus IEF.
- **Break-evens (inflation expectations).** Nominal minus real, IEF versus
  TIP.
- **Momentum divergence.** TLT (long end) versus IEF (belly): a flag when
  the two disagree and the curve read is muddier than the label suggests.

The four resolve into one curve label plus a confidence score, and a take
line that reads like the top of a rates note.

This is not a rates model or a yield forecast. It is a descriptive read of
what the Treasury-ETF tape is pricing right now, grounded in real prices,
so an LLM does not have to guess whether the curve is bull-flattening.

## When to invoke

- The session question is the curve: "is the curve steepening or
  flattening", "bull or bear", "are real yields rising", "what are
  break-evens doing", "long end versus the belly"
- Following up on a macro-basket run when the reader wants the rates detail
  behind the one-line curve summary
- The user says "rates read", "curve read", "2s10s", "duration signal",
  "real yields", "break-evens", "steepener or flattener"

For the broad cross-asset read (rates plus credit, the dollar, gold, and
commodities) use [`macro-basket`](../macro-basket). That skill is the broad
dashboard; rate-signal is the rates-only deep dive.

## What you need

- Nothing required beyond a key. Defaults cover the standard run.
- `MASSIVE_API_KEY` exported in the environment.
- Any stocks tier (all four members are US-listed ETFs). On Free Basic
  pass `--sleep 13` so the 4-series pull stays under the 5-calls/min cap.

Optional:

- `--window` (default `60`): lookback window in trading days for the
  curve reads. The short sub-window used by the momentum read scales from
  it (`max(5, window // 4)`).

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
An `instruments` array (SHY, IEF, TLT, TIP with window return and obs
count), a `signals` block (curve, real_yield, breakeven, momentum), a
`confidence` object (level plus the agreements and conflicts behind it),
and a composed `take`. UIs and downstream agents consume this.

**Layer 2: rendered note**: the four-instrument return table, the signals
block, the confidence lines, and the take. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull daily aggregates** for SHY, IEF, TLT, TIP over
   `max(window, 252) * 1.6` calendar days, via
   `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}?adjusted=true`.
2. **Curve slope** from SHY vs TLT relative return combined with TLT
   direction into the four-way bull/bear steepening/flattening label.
3. **Real yields** from TIP vs IEF, **break-evens** from IEF vs TIP, and
   **momentum divergence** from TLT vs IEF over a short sub-window.
4. **Confidence** from cross-signal agreement, then **compose the take**
   into a one-sentence rates note. The pair choices, thresholds, and the
   confidence rule live in [`references/methodology.md`](./references/methodology.md).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  rate-limit handling, and the `/v2/aggs` daily endpoint conventions.

## Output mode: note

A short, high-signal read. The rendered note is the curve label, the four
sub-reads, the confidence, and the take: what a rates desk would put in a
one-paragraph morning note. The JSON layer carries the traceable numbers.

## Endpoints used

- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true`
  Daily closes per instrument. One call per basket member (four total).

## Doesn't handle (yet)

- **Cash-market yields.** Every read is an ETF-return proxy, not the
  actual 2s10s in basis points or the real 10-year TIPS yield.
  Directionally right, not a cash-market substitute. A Treasury-yield
  endpoint would upgrade this; queued.
- **The full curve.** Four points (short, belly, long, real) is not the
  whole term structure. No 2s5s, 5s30s, or explicit butterflies. Queued.
- **Regime persistence.** The reads are point-in-time over the window; no
  "how long has this regime held" measure. Pair with
  historical-analog-finder. Queued.

These are clean PR extensions. The output schema is forward-compatible.
