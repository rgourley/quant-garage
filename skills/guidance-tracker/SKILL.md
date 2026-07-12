---
name: guidance-tracker
description: Track corporate guidance history for a ticker via Benzinga Corporate Guidance. Classifies each event as raised / lowered / reaffirmed / initiation against the prior figure using the endpoint's built-in previous_min/max fields, groups by fiscal period, and reports the trajectory. Answers "how has management's own view of the year evolved?" Requires Stocks Basic + Benzinga Corporate Guidance add-on (approx $99/mo). Emits a clear NOT_AUTHORIZED tier caveat when the entitlement is missing.
---

# guidance-tracker

You hand over a ticker. The skill pulls every corporate guidance event
Benzinga has on record over the lookback window, compares each event
to the prior figure for the same fiscal period, and labels it as a
raise, cut, reaffirmation, or initiation. Groups results by fiscal
period so the reader can see "management guided FY26 EPS at $5.20 in
January, raised to $5.60 in April, reaffirmed in June."

## When to invoke

- A PM asks "has NVDA management been raising or cutting guidance
  this cycle?"
- A fundamental analyst wants a heads-up on companies that just
  lowered guidance
- Screening a watchlist for consistent raisers as a bullish tilt
- The user says "guidance history", "guidance trajectory",
  "management raise", "corporate guidance track record"

Not for: consensus vs guided (that's earnings-drilldown territory).
Not for actual vs guided (also earnings-drilldown once the print
lands).

## What you need

- A ticker (`--ticker`, required)
- `MASSIVE_API_KEY` exported
- Stocks Basic plan minimum PLUS **Benzinga Corporate Guidance
  add-on** (approx $99/mo). Without the add-on the endpoint returns
  NOT_AUTHORIZED; the skill emits a clean tier caveat and no
  events.

Optional:

- `--lookback-days` (default 540): calendar-day window back from today.

## What you get back

Two output layers from one run.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-event: `date`, `fiscal_year`, `fiscal_period`, `label`
(`raised`/`lowered`/`reaffirmed`/`initiation`/`mixed`/`unclear`),
EPS midpoint current and prior, revenue midpoint current and prior,
per-metric direction and delta_pct. Top-level `by_period` group of
events per fiscal period plus a trajectory array showing the sequence
of labels for that period.

**Layer 2: rendered note**. Header with counts by label, timeline
sorted most-recent-first with EPS and revenue on the same line as
the raise/cut tag, one-line Take. See
[`references/rendering.md`](./references/rendering.md).

## How it works

1. **Pull guidance events** via
   `GET /benzinga/v1/guidance?ticker={T}&date.gte={D}&limit=1000&sort=date.desc`.
2. **Classify per event** using the endpoint's built-in `previous_`
   fields. EPS midpoint = `estimated_eps_guidance` (fallback to
   `(min+max)/2`). Prior midpoint = `(previous_min + previous_max)/2`.
   Delta_pct = `(cur - prior) / |prior|`. Below 0.5% is `reaffirmed`;
   above is `raised` or `lowered`; missing prior is `initiation`.
3. **Combine EPS and revenue labels** into a headline:
   `raised`+`raised` = raised; `raised`+`lowered` = mixed; and so on.
4. **Group by fiscal_period.** Each period gets its trajectory (the
   ordered sequence of labels), so "FY26 Q4: initiated → raised →
   raised → reaffirmed" is one legible line.
5. **Take** summarizes the overall balance of raises vs cuts and
   surfaces the most-recent event's label.

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth,
  retry, pagination.

## Output mode: note

Narrative note with a chronological timeline. Guidance events are
few per name (5-30 in 18 months typically); a wide table would
lose the fiscal-period grouping that makes the timeline legible.

## Endpoints used

- `GET /benzinga/v1/guidance?ticker={T}&date.gte={D}` — every
  guidance event Benzinga has on record for the ticker in the window.

## Doesn't handle (yet)

- **Chain to actual results.** No comparison of guided vs delivered.
  `earnings-drilldown` covers actual-vs-consensus; a workflow
  composite could add actual-vs-guided.
- **Cross-ticker sector scan.** No "list every consumer name that
  cut guidance this quarter." Queued as a composite.
- **Analyst reaction overlay.** No pairing with rating changes /
  price target moves. Would compose cleanly with a future
  `analyst-tracker`.
- **Detection of guidance-plan vs one-off.** No flag for
  "management gives structural annual guidance" vs "gave a one-off
  during a special-situation update."

These are clean PR extensions. The output schema is
forward-compatible.
