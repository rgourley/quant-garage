# sector-rotation-signal rendering

Table mode. Header block, theme line, rotation table, rotating-in /
rotating-out summaries, caveats footer.

## Header

```
Sector Rotation Signal — {AS_OF}
Rotation window: {DAYS}d ({THEN_DATE} -> {NOW_DATE}) · Primary RS: {RS_WINDOW}d · Secondary RS: {SECONDARY_WINDOW}d
```

## Theme

```
Theme: {THEME_READ_SENTENCE}
```

Blank line before the table.

## Rotation table

Sort by `rank_now` (ascending — best sector at top).

```
Sector  Name                      Rank  Δ Rank    20d RS   Δ 20d RS    60d RS  Rotation
------------------------------------------------------------------------------------------------
{TICKER}  {NAME_TRUNCATED_24}    {N}    {SIGNED}   {BPS}      {BPS}      {BPS}   {ARROWS} {LABEL}
```

Rotation arrows:
- rotating_in_strong: `↑↑ rotating in`
- rotating_in: `↑  rotating in`
- stable: `   stable`
- rotating_out: `↓  rotating out`
- rotating_out_strong: `↓↓ rotating out`

## Rotating in / out summary

```
Rotating in:  XLV (+3), XLF (+4), XLU (+7), XLP (+3)
Rotating out: XLB (-4), XLY (-3), XLK (-9)
```

If both lists are empty:

```
No sectors moved more than 1 rank position this window.
```

## Caveats footer

Standard block: noise-floor threshold, SPDR proxy limitations, RS is
past-return, rotation reads are heuristic.
