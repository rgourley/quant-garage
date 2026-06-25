# Peer selection

This skill uses the same three-layer peer waterfall as `pitch-comps`:
curated override map → correlation fallback → SIC fallback. See the
parent reference at
[`../../pitch-comps/references/peer-selection.md`](../../pitch-comps/references/peer-selection.md)
for the full methodology, override map, and rationale on why hand-
curation beats SIC for the top names.

This file documents what's different in the sanity-check context.

## Why peer choice matters more here than in pitch-comps

`pitch-comps` shows the comp table; a banker reading it can re-rank or
swap a peer that doesn't fit. The sanity check **drives the conclusion**:
the 25-75 percentile band that the analyst's assumptions are compared
against, and the peer-median CAGR that the reverse-DCF reads against,
are both summary statistics of the cohort. A weak peer set produces
a weak sanity check.

The curated override map is therefore the strongly preferred path. The
correlation fallback is acceptable; the SIC fallback should be flagged
prominently in the rendered note ("peer set is SIC-derived; the
distribution may not reflect trader consensus").

## Shared override map

The override map lives in
[`../../pitch-comps/references/peer-selection.md`](../../pitch-comps/references/peer-selection.md).
This skill's reference Python implementation imports the same dict
directly (or duplicates it, with the rule that updates land in both).
Cross-reference on every update.

## What's recorded in the JSON

Same shape as `pitch-comps`:

```json
"peer_selection": {
  "method": "curated_override",
  "n_peers": 7
}
```

The rendered note header reads `peer set: 7 names via curated_override`
so the reader knows the trust marker without opening the JSON.

## Edge cases unique to sanity-check

- **Negative-EBITDA peers** drop out of the EV/EBITDA distribution
  (multiple is meaningless) but stay in the EV/Sales and growth/margin
  distributions. Per-multiple `n_peers_in_distribution` records the
  surviving sample size so the reader can sanity-check the sample.
- **Foreign issuer ADRs** (SAP, ASML, NVO) with empty financials in
  Massive's endpoint stay in the peer list with `null` multiples and
  drop out of every distribution. The peer-list membership is itself
  information ("the analyst should know NVDA trades vs ASML and TSM
  even if the multiples aren't directly comparable").
- **Small peer set (n < 4) on a check.** The distribution is unstable
  and the percentile bands become noisy. The schema's
  `n_peers_in_distribution` per check lets the renderer flag a
  small-sample caveat.
