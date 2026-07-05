# corporate-actions-scanner rendering

Stream mode. One block per event, ranked by |T+5 reaction|.

## Block shape

```
{TICKER}  {FILING_DATE}  8-K item{s} {ITEMS}  ·  {BUCKETS}  [·  flavor: {FLAVOR}]
  HEADLINE ({SOURCE}): {HEADLINE_TRUNCATED_TO_140_CHARS}
  REACTION: T+1 {SIGNED_PCT}  ·  T+5 {SIGNED_PCT}
  ↳ {NEWS_URL}
```

Header line is always present. HEADLINE, REACTION, and URL lines are
conditional: skip when the corresponding field is null.

## Header

- `{TICKER}` uppercase symbol.
- `{FILING_DATE}` ISO date of the 8-K acceptance (not the report date).
- `{ITEMS}` comma-separated raw item codes as filed (e.g. `2.03, 8.01,
  9.01`). Prefix with `item` (singular) or `items` (plural) based on
  count.
- `{BUCKETS}` comma-separated humanized bucket labels (e.g. `material
  agreement, new debt or obligation`).
- `{FLAVOR}` — appended after `·  flavor:` only when detected.

## Body lines

- **HEADLINE** — matched news article title, truncated at 140 chars.
  Source in parens is the publisher name. Skip when no news match.
- **REACTION** — `T+1 {signed-pct}%  ·  T+5 {signed-pct}%`. Both from
  close-to-close, not SPY-adjusted. Skip a leg if the corresponding
  bar was unavailable.
- **URL** — prefix with `↳ ` (indented arrow). Skip when no URL.

Blank line between blocks.

## Footer

- Skipped ticker list, comma-separated with `(reason)` per entry.
- Caveats block: keyword-match limitations, +/- 2 day window, SPY-
  adjustment gap, private-placement lag.

## Sort order

Descending by `|reaction_t5_pct|`, falling back to `|reaction_t1_pct|`
when T+5 is null. Then trim to `top_n` events overall.
