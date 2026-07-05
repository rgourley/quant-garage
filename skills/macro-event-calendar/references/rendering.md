# macro-event-calendar rendering

Table mode. Header block, one-row-per-event table sorted by date,
crowded-days callout, caveats footer.

## Header

```
Macro Event Calendar — {AS_OF}
Forward window: {WINDOW_DAYS}d · Benchmark {BENCHMARK} · Historical reactions: {HISTORY_DAYS}d lookback
```

Blank line, then the event-count summary:

```
{N_EVENTS} events across {N_DATES} dates
```

## Table

```
Date             ET  Event                        Impact   Mean|Δ|   Median     p90     n
------------------------------------------------------------------------------------
{DATE}{~}  {ET}  {EVENT_NAME_TRUNCATED_28}  {STAR_TIER}  {PCT}   {PCT}   {PCT}    {N}
```

Columns:

- **Date**: ISO date; `~` marker immediately after date if pattern-
  derived (not hardcoded).
- **ET**: release time in Eastern Time (`HH:MM`).
- **Event**: humanized event name, truncated to 28 characters.
- **Impact**: 1-4 stars from the tier: very_high = ★★★★, high = ★★★,
  medium = ★★, low = ★.
- **Mean|Δ| / Median / p90**: historical absolute 1-day SPY move on
  this release type, formatted as `X.XX%`.
- **n**: historical sample count.

After the table: `~ = pattern-derived date; verify against official
calendar`.

## Crowded days

If `crowded_days` has entries:

```
Crowded days ({N}):
  {DATE}: {N_EVENTS} events — {EVENT_NAMES_COMMA_SEPARATED}
```

## Caveats

Standard block. Include the pattern-derived caveat, FOMC hardcoded
schedule note, regime-conditional caveat, and missing prior/consensus
caveat.
