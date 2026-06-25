# Rendering: valuation-sanity-check

The skill emits canonical JSON matching `output-schema.json`. This
reference shows how that JSON renders in note mode. Note mode in this
repo follows the sell-side flash-note convention used by
`earnings-drilldown`: bold take at the top, grouped supporting
sections, closing read, one-page max.

## Block order

Five blocks, separated by blank lines:

1. Header (one line)
2. Subject + target line (one line)
3. Bold take (one paragraph, 3-4 lines)
4. Three sanity sections (Multiple, Growth, Margin)
5. Reverse-DCF block
6. Closing read (one or two sentences)

No prose intros. The reader opens the note expecting a sanity check;
deliver one.

## Header

```
{ticker} · Valuation sanity check as of {date}
```

When `tier == "B"`, append a second line:

```
Tier B run (free Basic, peer fanout rate-limited). Re-run on Stocks Starter for full fanout.
```

When `peer_selection.method != "curated_override"`, append a note:

```
Peer set: {n_peers} names via {method}
```

The trust marker tells the reader whether to defend the cohort.

## Subject + target line

One line summarizing the current state and the target:

```
Target: ${target_price} · Current: ${current_price} · Implied upside {pct_signed}
```

Format:

- Target and current prices to two decimals when < $100, one decimal
  when $100-$999, no decimals when ≥ $1000.
- Implied upside as signed pct with one decimal (`+23.4%`, `-12.1%`).

## Bold take

One paragraph at the top, in the format spelled out in
[`take-generator.md`](./take-generator.md):

```
Take: Target requires {ticker} growing {growth_pp}% CAGR for
{horizon} years at {margin_pp}% EBITDA margin. Peer median is
{peer_growth_pp}% / {peer_margin_pp}%. The assumption is
{growth_delta_pp_signed}pp ahead on growth and
{margin_delta_pp_signed}pp ahead on margin. {Verdict line per
take-generator}.
```

Soft-wrap to keep each line under ~80 characters. The take generator
chooses the verdict template based on how many of the three sanity
checks fired out-of-band.

## Multiple sanity section

```
Multiple sanity (target-implied vs peer band)
- Implied EV/Sales:    {implied}x  vs peer 25/75 band  [{p25}x — {p75}x]  → {STATUS}
- Implied EV/EBITDA:   {implied}x  vs peer 25/75 band  [{p25}x — {p75}x]  → {STATUS}
- Implied P/E:         {implied}x  vs peer 25/75 band  [{p25}x — {p75}x]  → {STATUS}
```

Formatting rules:

- Multiples: one decimal, right-aligned within the implied column.
- Peer band: shown as `[p25 — p75]` with an em-dash-style separator
  rendered as `--` (two hyphens, not Unicode em-dash). Repo
  convention disallows em-dashes; the bracket separator is `--`.

  Wait, no. Re-check: the example block in the task uses the
  Unicode `—` between the band endpoints. The repo's ban on em-dashes
  applies to prose, not to numeric-range delimiters where it's a
  visual separator (the same way `pitch-comps` cohort-statistics
  block uses `-` between p25 and p75). For consistency with the rest
  of the suite, use a plain `-` (single hyphen) between the band
  endpoints: `[9.5x - 14.6x]`. This matches `pitch-comps`'s `25/75 %ile`
  row formatting.

- Status: `→ ABOVE`, `→ IN LINE`, `→ BELOW` in uppercase, after the
  arrow. `→` is fine (it's a single character, not a banned dash).
- Null status: `→ N/A`, with the implied and band cells showing `n/a`.

When `n_peers_in_distribution < 4` on a multiple, append a small-sample
caveat to that row:

```
- Implied EV/EBITDA:   35.4x  vs peer 25/75 band  [21.7x - 32.6x]  → ABOVE  (n=3)
```

## Growth sanity section

```
Growth sanity (assumed vs peer band)
- Revenue growth (1yr):  {assumed_pct}  vs peer 25/75 band  [{p25_pct}, {p75_pct}]  → {STATUS}
- 5yr revenue CAGR:     {assumed_pct}  vs peer 25/75 band  [{p25_pct}, {p75_pct}]  → {STATUS}
```

In v1, both rows use the **same peer TTM growth distribution**
(documented in [`growth-margin-sanity.md`](./growth-margin-sanity.md))
because we don't have a true 5y peer CAGR. The two rows in the
renderer give the analyst's input context (the assumed_growth is read
as both "1yr growth assumption" and "5yr CAGR assumption" in the
sense that the model holds growth constant over the horizon). When
v2 ships true 5y CAGR per peer, the rows diverge.

For now, the v1 renderer shows **one row** (combined "Revenue growth
(over horizon)") to avoid implying two distinct comparisons. The
schema records the single comparison; the renderer just labels it
honestly:

```
Growth sanity (assumed vs peer band)
- Revenue growth (over horizon):  +28%  vs peer TTM band  [+11%, +22%]  → ABOVE
```

Append "TTM proxy" to the band label when the v1 simplification
applies (always, in v1).

## Margin sanity section

```
Margin sanity (assumed vs peer band)
- EBITDA margin:        {assumed_pct}  vs peer 25/75 band  [{p25_pct}, {p75_pct}]  → {STATUS}
```

Operating margin is not in v1's schema for this comparison (peer
operating margin is in the `pitch-comps` per-peer metrics but the
sanity-check skill focuses on EBITDA margin since that's the input
the analyst usually provides). A clean v2 PR adds the second row.

## Reverse-DCF block

```
Reverse-DCF view (at current ${current_price})
- Implied 5yr revenue CAGR ({assumed_margin_pct} margin floor): {implied_cagr_pct}
- vs peer median 5yr CAGR:                                       {peer_median_cagr_pct}
- Air in current price:                                          {air_pp_signed}pp CAGR
```

Formatting:

- All CAGRs as unsigned pct with no decimals (`22%`, `18%`).
- `air_pp_signed` as signed pp with one decimal (`+4.0pp`,
  `-2.5pp`). Append `pp CAGR` as the unit.

Append the fair-value-at-peer-median anchor as a fourth bullet when
the reverse-DCF produced a result:

```
- Fair value at peer-median growth (same margin): ${fair_value}
```

Fair value formatting: same rules as the target/current price line.

When `reverse_dcf.implied_cagr is null` (insufficient data), skip
the whole block. Don't write "insufficient data."

## Closing read

One or two sentences at the bottom, in the format spelled out in
[`take-generator.md`](./take-generator.md):

```
Read: {generated read text}
```

The verdict template is chosen by the take generator based on how
many of the three sanity checks fired out-of-band and what the
reverse-DCF implied vs peer median.

## Full example

See `examples/valuation-sanity-check-output.md` for a full rendered
output paired with the JSON payload that produced it.

## What UI devs do instead

Consume the JSON directly. The three sanity blocks
(`multiple_sanity[]`, `growth_sanity`, `margin_sanity`) each render
cleanly as a comparison bar (assumption vs band) with the status
chip. The `reverse_dcf` block renders as a scatter showing
peer-median CAGR vs implied CAGR with the air-gap highlighted. The
note format is the Claude Code default; UIs build their own visual
layer.

## Footer

No footer in note mode. Sources go in the JSON for audit trail but
aren't rendered to humans (analysts don't read endpoint URLs in a
flash note).
