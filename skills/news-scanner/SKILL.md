---
name: news-scanner
description: Surface the day's news events that actually moved a stock. For each notable headline across a watchlist (or the broader market), render a Bloomberg news tape / Benzinga Pro-style stream with sentiment, novelty, and the post-publish price reaction. Ranked by impact, capped at top N (default 15-20). The 6am sell-side morning-note prep workflow.
---

# news-scanner

You hand over a watchlist and a time window. The skill pulls every news
event Massive has on those tickers in the window, derives a sentiment
score per ticker per article, measures whether the angle is novel or a
re-run, computes the stock's price reaction since publish, ranks events
by impact, and emits a stream of the top N.

This is the workflow a sell-side analyst runs at 6am to write the
morning note. Twenty headlines that actually moved a stock, with the
context (sentiment + novelty + reaction + volume anomaly + divergence
flag) to write about them in 30 minutes. Unlike a news terminal or RSS
reader, news-scanner ranks events by signal quality (price reaction ×
volume anomaly × novelty) rather than recency, and ships the
methodology with the output.

## When to invoke

- An analyst is prepping the morning note and wants the overnight tape
  ranked by impact
- A PM is asking "what's the news on my book today"
- The user says "scan news on NVDA TSLA AAPL", "what moved overnight",
  or "any catalyst on my watchlist"
- A trader wants to spot price/news divergence (negative headline,
  positive reaction = bad news already priced in)

## What you need

- A watchlist of tickers (default: NVDA, TSLA, AAPL, SPY, META, NFLX)
- A time window in hours (default: last 24h)
- `MASSIVE_API_KEY` exported in the environment
- Stocks Basic + Benzinga News add-on minimum

The skill runs at two fidelity tiers.

- **Tier A (Benzinga sentiment + minute aggs):** Benzinga News add-on
  returns per-ticker `insights[]` with a categorical sentiment label
  ("positive" / "negative" / "neutral") and `sentiment_reasoning` from
  Benzinga's own NLP. Stocks Starter or higher gives reliable minute
  aggregates for the reaction window. This is the default tier.
- **Tier B (keyword fallback):** Benzinga News add-on missing or the
  Benzinga `insights` field is empty. Sentiment falls back to a keyword
  scorer (positive: beat, raise, partnership, upgrade; negative: cut,
  miss, lawsuit, downgrade, recall, probe). Reaction calc still works
  on Stocks Basic but uses 5-minute aggregates instead of 1-minute.
  Documented in [`references/sentiment-scoring.md`](./references/sentiment-scoring.md).

## What you get back

Two output layers from one analysis.

**Layer 1: canonical JSON** matching [`output-schema.json`](./output-schema.json).
Per-event fields: ticker, published_at, source, headline, url,
sentiment_score (in [-1, +1]), sentiment_source ("benzinga" or
"keyword"), novelty_score, novelty_band, reaction_pct_since_publish,
reaction_window_label, volume_anomaly_x, divergence_flag, context_line.
UIs, alert pipelines, and downstream agents consume this.

**Layer 2: rendered stream** in Bloomberg news-tape / Benzinga Pro
style. Three lines per event, optional `↳` continuation for context.
Format rules in [`references/rendering.md`](./references/rendering.md).
Compact, scanable, key:value pairs. Claude Code users read this.

## How it works

1. For each ticker in the watchlist, pull
   `/v2/reference/news?ticker={t}&published_utc.gte={window_start}&limit=50`.
   Dedupe by `article_url` across the merged set so a story syndicated
   across publishers only appears once. See
   [`references/news-sources-and-coverage.md`](./references/news-sources-and-coverage.md).
2. Score sentiment per (ticker, article). Prefer the Benzinga `insights`
   entry for that ticker if present (map "positive" → +0.7, "neutral" →
   0, "negative" → -0.7); otherwise fall back to a keyword scorer over
   the title and description. See
   [`references/sentiment-scoring.md`](./references/sentiment-scoring.md).
3. Score novelty per (ticker, article). Bucket the last 7 days of
   articles for the ticker; compute TF-IDF over titles + first-sentence
   of description; cosine distance to nearest neighbor in the bucket.
   Distance > 0.6 = high novelty (new angle), 0.3-0.6 = medium, < 0.3 =
   low (already covered). See
   [`references/novelty-detection.md`](./references/novelty-detection.md).
4. Compute the price reaction. Pull `/v2/aggs/ticker/{ticker}/range/5/minute/...`
   from publish minute through min(publish + 60 minutes, market close).
   Reaction % = (close at window end / close at publish minute) - 1.
   Volume anomaly = avg per-minute volume during the window /
   prior-5-day same-time-of-day average per-minute volume.
5. Flag price/news divergence per
   [`references/price-news-divergence.md`](./references/price-news-divergence.md):
   positive sentiment + negative reaction = priced in / sell-the-news;
   negative sentiment + positive reaction = bad news already priced in.
6. Rank by `impact = |reaction_pct| × volume_anomaly × novelty_score`.
   See [`references/impact-ranking.md`](./references/impact-ranking.md).
   Emit top N (default 15).

## Foundations used

- [`massive-api-patterns`](../massive-api-patterns) for REST auth, the
  best-price fallback chain for spot, and rate-limit handling on the
  per-ticker news fan-out

## Output mode: stream

Stream mode is the format Bloomberg's news tape, Benzinga Pro's feed,
and Reuters Eikon use for incoming events. Each event is a
self-contained block; the reader scans top to bottom and stops when
they see one they want to act on. Inherited from
[`options-flow/references/rendering.md`](../options-flow/references/rendering.md),
adapted for news per
[`references/rendering.md`](./references/rendering.md).

## Endpoints used

- `GET /v2/reference/news?ticker={t}&published_utc.gte={iso}&limit=50`:
  Benzinga News. Returns `results[]` with `id`, `title`, `description`,
  `published_utc`, `article_url`, `tickers[]`, `keywords[]`,
  `publisher.name`, and a critical `insights[]` array with per-ticker
  `sentiment` ("positive"/"negative"/"neutral") and `sentiment_reasoning`.
- `GET /v2/aggs/ticker/{ticker}/range/5/minute/{from}/{to}`: minute (or
  5-minute) aggregates for the underlying. Used to compute reaction %
  and volume anomaly post-publish.
- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`: spot
  fallback chain for the "spot at publish" reference when minute aggs
  are missing or stale.

## Doesn't handle (yet)

- Sector / peer reaction. A positive NVDA story usually moves AMD and
  AVGO too; the skill doesn't surface sympathy plays. Clean v2.
- Wire-service deduplication beyond URL match. Reuters → Bloomberg →
  CNBC rewrites of the same story have different URLs and different
  first sentences; TF-IDF catches most but not all. A more rigorous
  story-clustering pass (LSH or sentence embeddings) is a v2 candidate.
- Real-time WebSocket streaming. v1 is REST-polled. The
  `massive-websockets` foundation covers the live-stream pattern for a
  future variant.
- Insider transactions, SEC filings, and FDA calendar items. These are
  catalyst-class news that don't ship through the Benzinga News feed;
  they live on the corporate-actions and reference endpoints. Separate
  skill.

These are clean PR extensions and welcome contributions.
