# Sentiment scoring

Sentiment is the most hand-wavy part of this skill. Be honest about it.
The skill ships two scorers, prefers the better one when available,
and exposes which one produced each score in the JSON.

## Two paths

### Path 1: Benzinga insights (preferred)

Massive's `/v2/reference/news` endpoint returns a per-article
`insights[]` array. Each entry is `{ticker, sentiment, sentiment_reasoning}`
where `sentiment` is the categorical label `"positive"`, `"neutral"`, or
`"negative"`. Benzinga's NLP team produced these. They're not perfect,
but they're trained specifically on financial news, the label is
ticker-scoped (one article tagged on 6 tickers has 6 separate
sentiments), and the reasoning is human-readable.

Mapping to a numeric score in [-1, +1]:

```
positive  → +0.7
neutral   →  0.0
negative  → -0.7
```

The skill caps the magnitude at 0.7 (not 1.0) because Benzinga's
labels are categorical, not graded. A "positive" earnings surprise and
a "positive" partnership announcement both get the +0.7 score; the
true magnitude difference shows up downstream in the reaction %, not
in the sentiment score. Saving the +1.0 / -1.0 endpoints for a future
graded scorer (or for the keyword scorer when it produces an extreme
match like "fraud", "bankruptcy", "indicted").

When this path is used, set `sentiment_source: "benzinga"` and keep
the reasoning string in `sentiment_reasoning`.

### Path 2: Keyword fallback

When the article has no `insights[]` for the relevant ticker (rare
but it happens, usually on Benzinga's older or syndicated re-posts),
fall back to a keyword scorer over the title and the first sentence
of the description.

The scorer is a simple sum-of-weighted-matches divided by length:

```
score = (positive_hits - negative_hits) / max(1, total_words / 20)
clamp to [-1.0, +1.0]
```

Positive lexicon (case-insensitive substring match):

```
beat, beats, beating, raises, raised, partnership, breakthrough,
upgrade, upgrades, upgraded, outperform, buy rating, surge, surged,
record high, all-time high, expanded, expansion, profitable, profit,
exceeds, exceeded, top-line beat, bottom-line beat, accretive,
acquired, acquires, accelerated, milestone, approved, approval
```

Negative lexicon:

```
cut, cuts, cutting, miss, missed, missing, lawsuit, sued, downgrade,
downgrades, downgraded, recall, recalled, probe, investigation,
investigates, plunge, plunged, slump, slumped, decline, declined,
warning, warns, layoffs, layoff, fire, fires, fired, fraud, bankruptcy,
delist, delisted, halt, halted, indicted, charge, charges, charged,
underperform, sell rating, weak, weaker, weakness, defect, defective
```

Each match contributes 1 to its bucket. Extreme matches (fraud,
bankruptcy, indicted, recall) double-weight so a single occurrence
saturates the score. Negation handling is intentionally not implemented
in v1 ("not a miss" scores negative; "no fraud detected" scores
negative); the cases are rare in headlines and the cost of getting
them wrong is small.

When this path is used, set `sentiment_source: "keyword"` and leave
`sentiment_reasoning` null.

## Strengths and weaknesses

| | Benzinga insights | Keyword scorer |
|---|---|---|
| Coverage | ~95% of Benzinga-tagged articles | 100% (any text input) |
| Precision | Trained on financial text, includes context | Catches obvious cases; misses irony and negation |
| Granularity | Categorical (3 labels) | Continuous (-1 to +1) |
| Speed | Returned in the news payload, free | Computed locally |
| Failure mode | Empty `insights[]` on edge cases | Sarcasm, complex sentences, negation, multi-ticker articles |
| Reproducibility | Benzinga can change their model | Fully deterministic |

## What the skill does NOT do

- **No sentence-level sentiment.** Article-level only. A long article
  with a positive headline and negative body produces the headline's
  score.
- **No multi-ticker reconciliation.** When the same article scores
  +0.7 on NVDA and -0.7 on INTC, both rows appear in the events list
  independently. The take field should call this out when relevant.
- **No model finetuning per ticker.** A "raise" on TSLA (price hike,
  positive) and a "raise" on a bank stock (capital raise, could be
  positive or negative) score the same. The keyword scorer is
  ticker-blind in v1.
- **No source-quality weighting.** A Motley Fool sentiment and a
  Reuters sentiment get the same weight in the impact ranking. The
  rendered output surfaces the source so the user can apply their
  own weighting.

## When to override

If the user explicitly asks for the keyword scorer (for transparency
or reproducibility), pass a `--sentiment-mode keyword` flag and skip
the Benzinga path entirely. The default is `auto`: prefer Benzinga,
fall back to keyword when absent.

## What a good v2 looks like

The v1 keyword scorer is the simplest defensible substitute. A v2
should replace it with a small finetuned classifier (FinBERT, or a
Claude-prompted classifier with a structured-output schema) and keep
the Benzinga path as the cross-check. The skill exposes
`sentiment_source` precisely so the v2 can introduce a third value
(`"finbert"`, `"claude"`, etc.) without breaking downstream consumers.
