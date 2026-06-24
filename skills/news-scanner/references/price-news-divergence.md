# Price/news divergence

## The setup

The sentiment score answers "what is this article saying about the
ticker." The reaction % answers "what did the market do after the
article published." Most of the time they agree: positive headline,
positive reaction; negative headline, negative reaction. The
interesting cases are when they don't.

## The two divergence patterns

### Pattern 1: positive news, negative reaction

A genuinely positive headline (sentiment_score > +0.4) followed by a
material drop (reaction < -0.5%). The signal: the market knew already
and is pricing in something the headline doesn't capture.

Common causes:

- **Sell the news / priced in.** The story leaked over days; longs
  exit on confirmation.
- **Below-whisper.** A beat that's smaller than the buy-side whisper.
  The headline reads positive, but the actual print disappoints.
- **Forward guide overshadows.** Q2 was great, Q3 guide is cut.
  Headline only mentions Q2 because that's the lede.
- **One-line teasers.** A favorable analyst note from a B-tier
  shop while a top-tier shop downgrades.

Flag: `positive_news_negative_reaction`. Trader read: "look past the
headline; something else is driving the tape."

### Pattern 2: negative news, positive reaction

A clearly negative headline (sentiment_score < -0.4) followed by a
material rally (reaction > +0.5%). The signal: the market priced in
worse, and the actual outcome is "not as bad as feared."

Common causes:

- **Already priced in.** The stock dropped 8% into the print on the
  expectation of a miss; the miss happens, the stock rallies 2% on
  short covers.
- **Bad news is good news.** A company announces layoffs; the market
  reads it as margin discipline and bids the stock up.
- **Guidance walk-up.** Bad miss, but management raises full-year
  guide on the call. The headline catches the miss; the tape catches
  the raise.
- **Litigation overhang resolved.** "Company settles SEC probe for
  $X." Sentiment scores negative; the resolution removes uncertainty
  and the stock rallies.

Flag: `negative_news_positive_reaction`. Trader read: "the bear case
just lost a thesis; cover or unwind shorts."

## Thresholds

The skill flags divergence when both conditions hold:

- `|sentiment_score| > 0.4` (the headline has a clear direction)
- `|reaction_pct| > 0.005` (the price moved at least 0.5%)
- The signs disagree

Below those thresholds, the divergence is too weak to call. The flag
stays `none`.

## Why 0.4 and 0.5%

- **0.4 sentiment threshold.** Benzinga's neutral score maps to 0,
  positive/negative to ±0.7. The 0.4 threshold cleanly excludes neutral
  while including all directional scores. For keyword scorers it
  picks up articles with at least two clear directional tokens.
- **0.5% reaction threshold.** Below 0.5% in a 60-minute window is
  inside typical intraday noise for liquid mega-caps. Above 0.5% is a
  move that's hard to attribute to chance.

These can be tuned per-ticker in v2 (small caps need wider bands,
mega-caps tighter), but the v1 uses a single global threshold.

## Edge case: off-hours publish

If the article publishes between 16:00 ET and 09:30 ET the next
trading day, there's no intraday reaction window inside the article's
own session. The skill defers:

- For overnight publishes, compute the reaction from publish minute
  through the next open's first 60-minute window.
- Tag the reaction window label as `"overnight"` and set the actual
  minute count in `reaction_window_minutes`.
- Divergence detection still applies; just measured against the
  next-day window.

This is documented in the rendered output as the time-of-day note
when the publish was off-hours.

## What this is NOT

- Not a trading signal in isolation. Divergence flags are context for
  a trader, not buy/sell triggers. Two of the most common divergence
  cases (priced-in earnings beats, settled-litigation rallies) are
  also obvious to anyone reading the tape; the flag is useful for
  filtering, not for alpha.
- Not a sentiment correction. The skill does not retroactively rewrite
  the sentiment score when divergence appears. The score remains the
  text-based score; the reaction is the market's score. Both numbers
  are in the JSON for downstream consumers to combine.

## What the rendered output does

When the divergence flag is non-`none`, the rendered context line
calls it out:

```
↳ DIVERGENCE: positive sentiment, -1.8% reaction. Likely priced in.
↳ DIVERGENCE: negative sentiment, +0.9% reaction. Tape says "not as bad."
```

When the flag is `none`, the context line is whatever the novelty
nearest-prior produces, or nothing at all if there's no clear context
to add.
