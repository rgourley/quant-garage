# Impact ranking

## The formula

Every candidate event gets a single scalar score that determines its
position in the stream:

```
impact = |reaction_pct| × volume_anomaly × novelty_score
```

Rank descending. Cap at the user-supplied top N (default 15). All
three factors are in [0, ∞) on construction; the product is in [0, ∞)
and we don't normalize because we never compare across runs, only
within a single run.

## Why these three

The three factors capture the three ways an event can matter:

1. **`|reaction_pct|`**: did the price actually move? The single best
   ex-post measure of impact. We use absolute value because a -2%
   move is as actionable as a +2% move.
2. **`volume_anomaly`**: did the move happen on conviction? A 1%
   move on 1x volume is noise; the same move on 8x volume is a
   directional flow event. The anomaly is computed as average
   per-minute volume during the reaction window divided by the
   prior-5-day same-time-of-day per-minute average.
3. **`novelty_score`**: is this a new angle? A re-run of yesterday's
   headline shouldn't outrank a fresh catalyst even if it produced a
   similar reaction. Novelty in [0, 1] from the TF-IDF distance.

Sentiment is intentionally not in the impact formula. The reaction %
already captures the directional signal; adding sentiment would
double-count and would penalize neutral-but-impactful stories
(merger announcements, leadership changes) whose sentiment is hard
to score but whose reaction is unambiguous.

## What happens with missing factors

The formula degrades gracefully when any factor is missing:

- **Missing reaction.** Article published off-hours and the next-day
  window hasn't closed yet. Use 0 for the reaction term; impact will
  be 0 and the event ranks at the bottom (correct behavior; we don't
  know yet if it mattered).
- **Missing volume anomaly.** No prior-5-day baseline (newly listed
  ticker, or weekend run). Default to 1.0 so it neither boosts nor
  penalizes the event.
- **Missing novelty.** First-ever article on the ticker in the
  bucket. Default novelty to 1.0 (genuinely new by construction).

A non-degraded impact uses all three; a fully-degraded one collapses
to `|reaction_pct|`, which is a defensible fallback.

## Tie-breaks

When two events have impact scores within 1% of each other:

1. Higher novelty wins.
2. If tied on novelty, more recent published_at wins.
3. If still tied, the article from the higher-quality publisher
   (Reuters/Bloomberg/Benzinga > Motley Fool/Zacks) wins.

These are documented but the cases are rare; ranking is almost always
decided by the impact magnitude.

## Same-story dedup

After ranking but before the top-N cut, the skill runs a final dedup
pass:

For each event at rank K, scan events at rank > K for "same story" using:

- Same ticker AND
- |published_at delta| < 60 minutes AND
- TF-IDF cosine distance < 0.2 on title+description

Collapse the lower-ranked event into the higher-ranked one's
`related_event_ids`. Keep the higher-impact one in the stream.

This catches the case where the same wire story produces three
near-identical entries from different publishers in the first hour
(Reuters → CNBC → MarketWatch). All three would otherwise rank
similarly and crowd the top of the stream.

## Per-ticker fairness (optional)

Default behavior: pure impact ranking. If NVDA had 5 of the top 10
events, that's fine; NVDA is what moved today.

Optional behavior with `--max-per-ticker 2`: after impact ranking,
limit each ticker to at most 2 events in the top N. Drop lower-impact
events from the over-represented ticker. Useful when the user wants
breadth across the watchlist rather than depth on the day's leader.

Not enabled by default because the value of news-scanner is exactly
to surface that NVDA had 5 events worth reading; muting that defeats
the purpose. But the flag exists for portfolio overview use cases.

## What "impactful" actually means here

The skill ranks ex-post: how much did the price actually move after
publish. This is a different question from "how impactful was the
news in absolute terms" (where a regulatory ruling that grants a
year-long approval window has huge value but produces a small
same-day reaction).

For ex-ante impact (will this news matter long-term?), the skill is
not the right tool. Earnings-drilldown, post-earnings-drift, and
analyst-rating-history skills exist for that. News-scanner answers
"what moved my book in the last 24 hours," which is the 6am
morning-note question.

## Sample-size honesty

Impact scores look precise but they're built on small samples:

- The reaction window is 60 minutes (sometimes less if the publish
  is near the close). A single bad print in those 60 minutes can
  tilt the reaction by 0.5%.
- The volume anomaly baseline is 5 days. Holidays, half-days, and
  earnings dates are NOT excluded; a sloppy baseline produces noisy
  anomalies.
- Novelty is computed against the trailing 7 days only. A story
  that's been in the news for 30 days will incorrectly score as
  novel if no version of it appeared in the last 7.

These are documented v2 candidates; the skill ships with the simple
versions and lets the operator interpret.
