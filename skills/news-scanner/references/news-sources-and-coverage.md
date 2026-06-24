# News sources and coverage

## What Massive's news feed actually is

`/v2/reference/news` is the Benzinga News firehose, fronted by Massive
and filterable by ticker. Benzinga is a news aggregator-plus-original-
reporting outlet whose feed is the default ingest for most retail and
mid-tier institutional terminals. The feed includes Benzinga's own
reporting plus syndicated headlines from a long tail of publishers.

This skill treats the Benzinga feed as canonical for the v1 because:

- It's the only news endpoint available on a Massive account
- It already does the cross-publisher aggregation work
- It ships per-article sentiment via the `insights[]` field, computed
  by Benzinga's NLP team, when a ticker tag is present

What it is not: Bloomberg Terminal's news ingest, Reuters Eikon's
feed, or a Refinitiv-grade primary-source firehose. Those products
include 10-K/10-Q press release wires the moment they hit, exchange
notifications, and embargoed institutional reports. Benzinga catches
most of those signals at most a few seconds after the wire services
do, but it's a derived feed, not a primary one.

## The publishers you'll actually see

Run a 24-hour scan across NVDA / TSLA / AAPL / SPY / META / NFLX and
you'll get articles from:

- **Benzinga itself.** Original reporting on earnings, analyst rating
  changes, unusual options activity, and political/legal news. Tag
  these as `source: "Benzinga"`. Sentiment is most reliable on these
  because Benzinga's own NLP team trained on their own format.
- **The Motley Fool, Zacks, Investor's Business Daily.** Aggregator/
  opinion content. Tone usually positive, novelty usually low. The
  novelty scorer correctly downgrades these; sentiment is still
  useful when it's strongly negative (a Motley Fool headline saying
  "Should you sell X?" is meaningful).
- **Reuters, Bloomberg, CNBC, MarketWatch.** Primary wire / cable
  business news. These are the highest-value entries when present:
  they typically hit minutes after the underlying corporate event and
  the reaction is sharp.
- **Press releases and PR Newswire.** When a company issues an 8-K
  or a product announcement, the PR copy lands here. Sentiment from
  PR is always positive (it's the company's own framing); the skill
  weights novelty more heavily on these to avoid promotion noise.
- **The long tail.** Crunchbase, TipRanks, GuruFocus, Seeking Alpha
  re-posts, niche industry blogs. Often syndicated repeats of an
  earlier wire; the dedup pass removes the URL-identical copies and
  the novelty scorer suppresses the paraphrased ones.

## What each source brings

| Source | Strength | Weakness |
|---|---|---|
| Reuters / Bloomberg | First to publish on hard news, low spin | Often paywalled; URL changes break dedup |
| Benzinga original | Fast, includes ratings + flow context | Mid-quality NLP; some opinion creeps in |
| Press releases / 8-K wire | Definitive (it's the company's own filing) | One-sided framing; always positive sentiment |
| CNBC / MarketWatch / WSJ | Analytical takes, narrative context | Often hours after the original wire |
| Motley Fool / Zacks | Search visibility, retail tone | Aggregator; novelty almost always low |
| TipRanks / GuruFocus | Analyst rating roll-ups | Roll-ups of roll-ups; sentiment unreliable |

## Deduplication

A single corporate event can produce 10-30 articles across publishers
in the first hour. The skill does two passes:

1. **URL dedup.** Strict match on the canonical `article_url` field.
   Removes syndication. Cheap and high-precision.
2. **Title-similarity dedup** (optional, in the novelty pass). Two
   articles in the same 7-day bucket with TF-IDF cosine distance < 0.2
   are collapsed into one event; the older one wins, and the newer ones
   become `related_event_ids` entries. This catches paraphrased
   re-reports where the URL is different but the story is the same.

Pass 1 typically removes 30-40% of the raw fetch on a busy day. Pass 2
removes another 5-10%.

## Time window

Default lookback is 24 hours from the run timestamp. That captures the
overnight tape plus any morning prints. For end-of-week summary use
72-168 hours; for live market-hours flow use 4-6 hours.

Benzinga's `published_utc.gte` filter is exact, but be aware:

- Some publishers backfill `published_utc` to the original wire time
  rather than the Benzinga ingest time. A 6am scan can pull a 3am
  Reuters story that Benzinga ingested at 5:50am with the original
  3am timestamp.
- Pre-market articles (4-9:30 ET) and post-market (16:00-20:00 ET) are
  included; the reaction calc handles the off-hours case by deferring
  the reaction window to the next open. See `price-news-divergence.md`
  for the overnight reaction handling.

## Why this matters for the user

A user asking "what news on NVDA" doesn't want 50 articles. They want
the 3-5 events that produced a real reaction. Sources matter because
they help the user decide whether to believe the take: a Reuters
breaking-news headline with +1.8% reaction is a different read than a
Motley Fool opinion piece with +1.8% (the Reuters one is causal; the
Motley Fool one is correlation).

The skill renders the source in the header line so the user can apply
their own publisher weighting at a glance. The JSON keeps the
publisher's logo URL and homepage for UI consumers.
