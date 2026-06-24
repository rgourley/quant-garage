# Novelty detection

## The question

Is this article a new angle on the ticker, or the third rewrite of a
story that broke 4 hours ago? Novelty is the second most important
ranking signal after reaction. A high-novelty positive headline with a
+1% reaction is more actionable than a low-novelty positive headline
with a +2% reaction (the second is mostly the market re-pricing the
already-known story).

## The approach: trailing 7-day TF-IDF bucket

For each candidate article on a given ticker, compute its cosine
distance to the nearest neighbor in a per-ticker bucket of the trailing
7 days.

Steps:

1. Pull all articles for the ticker in the prior 7 days (separate
   `/v2/reference/news?ticker=&published_utc.gte=&limit=100` call,
   or, more efficiently, widen the scan-window fetch and keep the
   pre-window articles for novelty context).
2. For each article (candidate + history), build a feature string:
   `title + " " + first_sentence_of_description`. First sentence,
   not full body, because the rest of the article tends to be
   boilerplate that washes out the distinctive vocabulary.
3. Compute TF-IDF vectors over the bucket using a simple bag-of-words
   tokenizer (lowercase, strip punctuation, split on whitespace,
   drop English stopwords, drop the ticker symbol itself).
4. For each candidate, find the smallest cosine distance to any
   article published earlier than the candidate. Articles that are
   the oldest in the bucket get novelty 1.0 by definition.
5. Banding:
   - `> 0.6` → `high` (genuinely new angle)
   - `0.3 - 0.6` → `medium` (recombination of known elements)
   - `< 0.3` → `low` (paraphrase or near-duplicate of prior coverage)

## Worked example

Trailing 7-day bucket for NVDA, today's candidate is "Nvidia,
Foxconn announce India fab partnership for AI chips":

Prior articles in the bucket:

1. "Nvidia stock hits all-time high on AI demand" → distance 0.71
2. "Foxconn beats Q2 estimates on iPhone strength" → no NVDA tag,
   excluded from bucket
3. "Speculation grows over Nvidia's India expansion plans" → distance
   0.34 (overlaps on "Nvidia", "India", but the angle is speculation
   vs. announcement)
4. "Nvidia partners with Cisco on enterprise AI" → distance 0.55

Nearest distance: 0.34 (item 3, the speculation piece).

Novelty score: 0.34 → `medium` band.

Reasonable read: the India angle was already in the air; today's
news is the confirmation, not the first whisper. The reaction will
be smaller than if novelty had scored `high`.

## Why TF-IDF and not embeddings

The honest reasons:

1. **Cost.** TF-IDF is free; computing sentence embeddings for every
   article on every ticker on every run costs an API call per article.
2. **Speed.** A 30-ticker scan over 7 days can produce 1000+ articles.
   TF-IDF over that bucket runs in milliseconds; sentence embeddings
   take an order of magnitude longer.
3. **Defensibility.** TF-IDF is deterministic and explainable. The
   token overlap between two headlines is auditable; embedding
   distances are not.
4. **Good enough.** Financial news headlines are tight, vocab-heavy,
   and tend to share named entities (tickers, company names, product
   names) in the words that matter. TF-IDF on this corpus is
   surprisingly close to embedding-based similarity for the use case.

The cost of getting it wrong: a high-distance score on a true
paraphrase ("Nvidia hits new high" vs "NVDA reaches record peak") will
incorrectly tag the second article as novel. Mitigation: the
URL-dedup pass catches the worst case (identical articles syndicated);
the title-similarity pass at distance < 0.2 catches the worst
paraphrases.

## What this approach misses

- **Synonyms across companies.** "Apple", "AAPL", "the iPhone maker",
  "Cupertino" all refer to the same entity. TF-IDF only sees them as
  separate tokens. v2 should preprocess with a named-entity
  normalization pass.
- **Semantic distance.** "Apple sues Epic" and "Epic countersues Apple"
  share most tokens but report different events. The cosine distance
  is small; the novelty should be medium. TF-IDF gets this wrong.
- **Temporal decay.** Articles 6 days old contribute the same weight
  as articles 6 hours old. A more rigorous scorer would decay older
  matches; v1 doesn't.
- **First-sentence noise.** Some articles open with a clickbait hook
  ("You won't believe what NVDA did today...") that washes out the
  factual content. The skill could fall back to body-text matching if
  the first sentence is < 5 informative tokens. Not implemented in v1.

## What gets persisted

For each candidate article, the JSON's `nearest_prior` field captures
the article that produced the distance:

```json
{
  "novelty_score": 0.34,
  "novelty_band": "medium",
  "nearest_prior": {
    "published_at": "2026-06-21T18:14:00Z",
    "headline": "Speculation grows over Nvidia's India expansion plans",
    "distance": 0.34
  }
}
```

UIs can render this on hover; the stream format usually doesn't show
it, but the rendered context line often paraphrases it ("third India
mention this week, prior coverage speculative").

## v2 candidates

- Sentence-Transformer embeddings (MiniLM or bge-small) with a single
  call per article, batched. Beats TF-IDF on synonym handling.
- Locality-Sensitive Hashing for cheap near-duplicate detection at
  scale (when scanning hundreds of tickers).
- Decay-weighted nearest-neighbor with half-life = 24h.
- Named-entity normalization pass before tokenization.
- Optional Claude-prompted novelty assessor for the top-N candidates
  (precise but expensive).
