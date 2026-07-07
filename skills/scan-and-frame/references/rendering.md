# scan-and-frame rendering

Hybrid mode. Header + headline block + 4 titled sections (factor-
research skipped when not enabled).

## Header

```
Scan and Frame — {AS_OF}
Universe: {SOURCE} · Min mcap: ${N}B · Sectors: {SECTORS_OR_ALL}
```

## Headline

```
HEADLINE
──────────
Regime:        {REGIME_UPPERCASE}
Universe:      {N} names survived filters
Top RS:        {TICKER} ({PERCENTILE}%ile), ...
Top factor:    {NAME} (t={T_STAT}, IC {IC})    (only when factor-research ran)
```

## Sections

1. `MACRO REGIME` — market_regime.render()
2. `UNIVERSE CANDIDATES` — universe_builder.render()
3. `RS RANKING` — relative_strength.render()
4. `FACTOR CONTEXT` — factor_research.render() (skipped if not enabled)

## Errors

Standard block.
