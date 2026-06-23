# Rendering: earnings-drilldown

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders in note mode for Claude Code
users. Note mode matches the format of a sell-side morning note: bold
take at the top, grouped supporting sections, one-page max.

## Mode: note

Lead with the conclusion, then 4-5 grouped sections of supporting data,
end with catalysts. A reader scans the take in 2 seconds and finds any
supporting number in 5 more.

## Header

Always lead with ticker, fiscal period, and print timing:

```
{ticker}: Q{n} {fiscal_year} Preview
Print: {weekday} {print.date} {session_label} · Consensus: ${consensus_eps} EPS, ${consensus_revenue}B rev
```

Where `session_label` is:
- `BMO` → "BMO"
- `AMC` → "AMC"
- `DMH` → "Intraday"
- `unknown` → omit

**Read `session_label` from the print data, not from a hardcoded value.**
Different names report at different times: AAPL/NVDA/MSFT print AMC, but
JPM/BAC/GS print BMO, and some retailers print intraday. A skill that
hardcodes "AMC" will mislabel the entire banking sector. The session
classifier in `references/print-history.md` writes this field per print;
the renderer just reads it. Same rule for the next-print projection line.

If consensus is null, omit the consensus line.

## Take

Single bold line, derived from the strongest signal in the analyses:

```
**Take:** {take}
```

The take generator picks the most actionable insight and phrases it
in trader terms. Always include the magnitude and at least one supporting
number so the reader can act without scrolling.

**Mispricing magnitude has two display modes** depending on how extreme
the ratio is:

- When `abs(mispricing_pct) <= 1.0` (≤100%), use the pct form
- When `abs(mispricing_pct) > 1.0`, switch to pp form so the take
  doesn't look like a script bug. A 4.2pp implied premium over a 1.6%
  realized base reads cleaner than "263% rich"

Typical patterns:

- **Straddle cheap, moderate** (mispricing_pct between -1.0 and -0.15) →
  "Straddle is {pct}% cheap vs {n}q realized (implied ±{x}%, realized ±{y}%). Premium buyers have a setup."
- **Straddle cheap, extreme** (mispricing_pct < -1.0) →
  "Straddle prices {pp}pp below {n}q realized (implied ±{x}%, realized ±{y}%). Premium buyers have a setup."
- **Straddle rich, moderate** (mispricing_pct between 0.15 and 1.0) →
  "Straddle is {pct}% rich vs {n}q realized (implied ±{x}%, realized ±{y}%). Premium sellers have a setup."
- **Straddle rich, extreme** (mispricing_pct > 1.0) →
  "Straddle prices {pp}pp above {n}q realized (implied ±{x}%, realized ±{y}%). Premium sellers have a setup."
- **Sharp PEAD pattern** (drift t_stat > 2.5, avg > 0.01) →
  "Hold {beats/misses} through T+5: {pct}% avg drift (t-stat {t}, n={n})."
- **Streak risk** (last 3+ beats with declining surprise size) →
  "Surprise streak decaying: last 3 beats average {pct}% vs {prior}% prior 5. Bar is moving."
- **Peer setup** (top peer beta > 0.6 + peer move > 1σ into print) →
  "{peer} up {pct}% into print (β {b} to {target} print day). Sector primed."

If no signal crosses its threshold, default to a factual non-recommendation:
"Setup mixed: implied ±{x}%, beat rate {b}/{n}, no edge in the data."

Two rules for take phrasing:
1. Never use directional words ("bullish", "bearish") in the take. Use
   action words ("buyers have a setup", "sellers have a setup", "hold T+5").
2. Never use "mispriced to the upside/downside": ambiguous in options
   context. Say "cheap" or "rich" relative to a named comparison.

## Implied vs realized (full mode only)

Skip this entire section if `mode === "lite"` or `implied_vs_realized === null`.

```
Implied vs realized
- Implied move (front straddle, 0.85-adj): ±{implied_move_pct * 100}% (raw straddle ±{straddle_pct * 100}%)
- Realized {n_quarters}q avg: ±{realized_avg_pct * 100}%
- IV30: {iv30} ({iv30_percentile_ttm * 100}th %ile TTM)
```

If the user explicitly wants only the raw number (some desks prefer it),
omit the 0.85-adj phrasing and show the raw straddle directly. Default
is to show both so the reader can pick.

If `closest_analog` is present, append on a new bullet:

```
- Closest analog: {closest_analog.period}, IV was {iv30_at_analog}, realized {realized_at_analog_pct * 100}%
```

## Print history

```
Print history (last {n_quarters} quarters)
- Beat rate: {beats}/{n_quarters} (avg surprise {avg_surprise_eps_pct * 100}% EPS{, +X% rev if revenue surprise present})
- Largest beat: {largest_beat.period} ({largest_beat.surprise_pct * 100}%)
- Largest miss: {largest_miss.period} ({largest_miss.surprise_pct * 100}%)
- Best reaction: {best_reaction.period} {best_reaction.next_day_return_pct * 100}% next day
- Worst reaction: {worst_reaction.period} {worst_reaction.next_day_return_pct * 100}% next day
```

Sign on percentages: always show explicit `+` or `−` so the reader
sees direction at a glance.

## Post-earnings drift

```
Post-earnings drift (T+1 to T+5)
- On beats: {avg_t5_return_pct * 100}% avg (n={n}, t-stat {t_stat}{, significant if significant})
- On misses: {avg_t5_return_pct * 100}% avg (n={n}{, sample too small if n < 4})
```

If `on_misses.n < 4`, write `"sample too small"` after the n value
instead of the t-stat. Same logic for beats. This prevents the reader
from acting on a single-observation pattern.

If `on_misses === null` (no misses in the window, common for names on
long beat streaks like AAPL), omit the misses line entirely. Do not
write "sample too small": that implies the data is there but thin,
when actually there are zero observations.

If `post_earnings_drift === null`, skip the section entirely (cleaner
than rendering "insufficient data").

## Peer reaction

```
Cross-asset
- {sector} peers traded {avg_peer_return_on_beat_pct * 100}% same-day on {ticker} beats last {n_cycles} cycles
- Top peer betas: {top_peers[0].ticker} (β {top_peers[0].beta}), {top_peers[1].ticker} (β {top_peers[1].beta})
```

If `n_peers < 3` or `n_cycles < 4`, skip the section.

## Catalysts

```
Catalysts to watch
- {catalysts[0]}
- {catalysts[1]}
- {catalysts[2]}
```

Render up to 5 catalysts. If the array is empty, skip the section
rather than write "none".

## Footer

No footer in note mode. Sources go in the JSON for audit trail but
aren't rendered to humans (analysts don't read endpoint URLs in a
morning note). If a UI dev wants to surface sources, they consume
the JSON.

## Full example

See `examples/earnings-drilldown-aapl.md` for a full rendered output
paired with the JSON payload that produced it.

## Lite-mode example

Lite mode is the same shape minus the "Implied vs realized" section.
The take refocuses on PEAD or surprise-streak signals since options
data is absent.

## What UI devs do instead

Consume the JSON directly. The four analytical blocks
(`implied_vs_realized`, `print_history`, `post_earnings_drift`,
`peer_reaction`) are each independently renderable as cards, charts,
or comparison panels. Sources can be linked from each datapoint.
The note format is a default for Claude Code; UIs typically use
something more visual.
