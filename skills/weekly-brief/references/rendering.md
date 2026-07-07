# weekly-brief rendering

Hybrid mode. Header + 4-line headline + 4 titled sections + errors.

## Header

```
Weekly Brief — week of {AS_OF}
Watchlist: {N} tickers · Forward window: {WINDOW_DAYS}d
```

## Headline

```
HEADLINE
──────────
Regime:        {REGIME_UPPERCASE}
Rotation:      {THEME_SENTENCE}
This week:     {EVENT_NAME} ({DATE}, {DAYS_OUT}d, {IMPACT}), ...
Prints ({N}): {TICKER} ({DATE}, {DAYS_OUT}d), ...
```

Skip lines when the corresponding section failed.

## Sections

1. `MACRO REGIME` — market_regime.render()
2. `SECTOR ROTATION` — sector_rotation_signal.render()
3. `MACRO CALENDAR (7d)` — macro_event_calendar.render()
4. `WATCHLIST EARNINGS (7d)` — earnings_blackout.render()

## Errors

Standard block.
