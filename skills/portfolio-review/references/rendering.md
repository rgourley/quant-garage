# portfolio-review rendering

Hybrid mode. Header, headline block, then each section rendered by
its own `render()` helper under a titled divider.

## Header

```
Portfolio Review — {AS_OF}
Book: {DOLLAR_FMT(BOOK_VALUE)} across {N_POSITIONS} positions ({N_EQUITIES} equities, {N_ETFS} ETFs)
```

## Headline block

Six lines max (skip lines when the source section failed):

```
HEADLINE
────────────────────────────────────────────────────────────
Regime:        {REGIME_LABEL_UPPERCASE}
Rotation:      {THEME_READ_SENTENCE}
Portfolio vol: {PCT}% · {NAME} drives {N}% of variance
Next earnings: {TICKER} ({DATE}, {N}d), {TICKER} ({DATE}, {N}d), ...
Next macro:    {EVENT_NAME} on {DATE} ({N}d, {IMPACT_TIER})
Top 8-K:       {TICKER} {DATE} · {FLAVOR} · abn T+5 {SIGNED_PCT}%
               {HEADLINE_TRUNCATED_100}
Rebalance:     vol {PCT}% -> {PCT}% · {N} trades
               Biggest trim: {TICKER} {SIGNED_DOLLAR} ({PCT} -> {PCT})
```

The 8-K and Rebalance rows have optional second lines that only
render when the extra fact (news headline, biggest-trim detail) is
present.

## Section renders

For each section that ran successfully, emit:

```
{TITLE_UPPERCASE}
════════════════════════════════════════════════════════════
{SUB_SKILL_RENDER_OUTPUT}

```

Titles in order:
1. MACRO REGIME
2. SECTOR ROTATION
3. PORTFOLIO RISK
4. EARNINGS CALENDAR (30d)
5. MACRO CALENDAR (30d)
6. CORPORATE ACTIONS (180d)
7. REBALANCE RECOMMENDATION

Sub-skill render helpers are imported directly from their modules;
this skill does not re-implement any section formatting.

## Errors footer

If any section raised during its `run()`:

```
ERRORS
────────────────────────────────────────────────────────────
  {SECTION_NAME}: {ERROR_MESSAGE}
```

The composite deliberately does NOT abort on a single-section
failure. A failed section leaves its `sections[<name>]` key null,
skips its render block, and appears in this footer.
