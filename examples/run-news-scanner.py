#!/usr/bin/env python3
"""
Reference implementation of the news-scanner skill.

Scans a watchlist over a time window for notable news events. For each
event surfaces the headline, source, per-ticker sentiment (Benzinga
insights when available, keyword fallback otherwise), novelty band,
and the stock's post-publish price reaction. Ranks by impact and emits
two output layers:

  Layer 1: canonical JSON matching skills/news-scanner/output-schema.json
  Layer 2: Bloomberg news-tape / Benzinga Pro-style rendered stream

Usage:
    python3 examples/run-news-scanner.py
    python3 examples/run-news-scanner.py --watchlist NVDA,TSLA,AAPL --hours 12 --top 10

Reads MASSIVE_API_KEY from env, never from a file.
Writes output to examples/news-scanner-output.md (gitignored).
"""
import os
import sys
import json
import math
import argparse
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# Make `lib.quant_garage` importable when running this script from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    ET,
    utc_to_et,
    utcnow_iso,
    resolve_output_format,
    emit_to_stdout,
)


# -------- Args --------

parser = argparse.ArgumentParser(description="news-scanner runner")
parser.add_argument(
    "--watchlist",
    default="NVDA,TSLA,AAPL,SPY,META,NFLX",
    help="Comma-separated tickers",
)
parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours")
parser.add_argument("--top", type=int, default=15, help="Max events to emit")
parser.add_argument(
    "--sentiment-mode",
    choices=["auto", "keyword"],
    default="auto",
    help="auto = prefer Benzinga insights; keyword = force keyword scorer",
)
parser.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT. Default: render.")
args = parser.parse_args()
fmt = resolve_output_format(args.format)

TICKERS = [t.upper().strip() for t in args.watchlist.split(",") if t.strip()]
WINDOW_HOURS = args.hours
TOP_N = args.top
SENT_MODE = args.sentiment_mode

client = MassiveClient()

NOW_UTC = datetime.now(timezone.utc)
WINDOW_START_UTC = NOW_UTC - timedelta(hours=WINDOW_HOURS)
NOVELTY_BUCKET_START_UTC = NOW_UTC - timedelta(days=7)

REACTION_MINUTES_DEFAULT = 60
MIN_REACTION_FOR_DIVERGENCE = 0.005
MIN_SENTIMENT_FOR_DIVERGENCE = 0.4

# Track fetched_at per source bucket so sources[] carries per-call provenance.
news_last_fetched_at = utcnow_iso()
aggs_last_fetched_at = utcnow_iso()


# -------- Lexicons --------

POSITIVE_LEX = {
    "beat", "beats", "beating", "raises", "raised", "raise", "partnership",
    "breakthrough", "upgrade", "upgrades", "upgraded", "outperform",
    "surge", "surged", "record", "expanded", "expansion", "profitable",
    "profit", "exceeds", "exceeded", "accretive", "acquired", "acquires",
    "accelerated", "milestone", "approved", "approval", "wins", "won",
    "high", "rally", "rallied", "boost", "boosted", "soars", "soared",
}
NEGATIVE_LEX = {
    "cut", "cuts", "cutting", "miss", "missed", "missing", "lawsuit",
    "sued", "downgrade", "downgrades", "downgraded", "recall", "recalled",
    "probe", "investigation", "investigates", "plunge", "plunged",
    "slump", "slumped", "decline", "declined", "warning", "warns",
    "layoffs", "layoff", "fired", "fires", "delist", "delisted",
    "halt", "halted", "indicted", "underperform", "weak", "weaker",
    "weakness", "defect", "defective", "loss", "losses", "drop", "drops",
    "drag", "fell", "fall", "falls", "tumble", "tumbled", "concern",
    "concerns", "concerned",
}
EXTREME_NEG = {"fraud", "bankruptcy", "indicted", "recall", "halted"}
STOPWORDS = {
    "a", "an", "and", "or", "but", "the", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "should", "could", "may", "might", "can", "this", "that",
    "these", "those", "it", "its", "their", "they", "them", "we", "us",
    "our", "you", "your", "he", "she", "his", "her", "i", "me", "my",
    "not", "no", "so", "if", "than", "then", "into", "out", "up", "down",
    "over", "under", "after", "before", "amid", "vs", "via",
}


# -------- HTTP --------

def fetch_news(ticker, gte_iso, limit_per_page=50, max_pages=4):
    """Pull /v2/reference/news for a ticker since gte_iso. Paginate."""
    global news_last_fetched_at
    params = {
        "ticker": ticker,
        "published_utc.gte": gte_iso,
        "order": "desc",
        "sort": "published_utc",
        "limit": limit_per_page,
    }
    out = []
    pages_seen = 0
    for page, fetched_at in client.paginate("/v2/reference/news", params):
        out.extend(page)
        news_last_fetched_at = fetched_at
        pages_seen += 1
        if pages_seen >= max_pages:
            break
    return out


def fetch_minute_aggs(ticker, frm_date, to_date, resolution=5):
    """Pull range/{resolution}/minute aggs. Used for reaction + volume baseline."""
    global aggs_last_fetched_at
    path = (
        f"/v2/aggs/ticker/{ticker}/range/{resolution}/minute/"
        f"{frm_date}/{to_date}?adjusted=true&sort=asc&limit=5000"
    )
    try:
        doc, fetched_at = client.get(path)
    except Exception as e:
        print(f"  warn: aggs fetch failed for {ticker}: {e}", file=sys.stderr)
        return []
    aggs_last_fetched_at = fetched_at
    return doc.get("results", []) or []


# -------- Tokenization / TF-IDF for novelty --------

WORD_RE = re.compile(r"[a-z0-9]+")


def tokens(text, ticker=None):
    if not text:
        return []
    raw = WORD_RE.findall(text.lower())
    out = []
    skip = STOPWORDS | ({ticker.lower()} if ticker else set())
    for t in raw:
        if t in skip:
            continue
        if len(t) <= 1:
            continue
        out.append(t)
    return out


def feature_string(article):
    title = article.get("title") or ""
    desc = article.get("description") or ""
    # First sentence of description
    first = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0]
    return f"{title} {first}"


def tf_vector(tokens_list):
    """Term-frequency vector as a dict (no IDF normalization yet)."""
    if not tokens_list:
        return {}
    out = defaultdict(int)
    for t in tokens_list:
        out[t] += 1
    return dict(out)


def build_idf(token_lists):
    """Compute IDF over a corpus."""
    df = defaultdict(int)
    n = len(token_lists)
    if n == 0:
        return {}, 0
    for tl in token_lists:
        for t in set(tl):
            df[t] += 1
    idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
    return idf, n


def tfidf_vector(tokens_list, idf):
    """TF × IDF."""
    if not tokens_list:
        return {}
    tf = tf_vector(tokens_list)
    total = sum(tf.values())
    if total == 0:
        return {}
    return {t: (c / total) * idf.get(t, 1.0) for t, c in tf.items()}


def cosine(a, b):
    if not a or not b:
        return 0.0
    common = set(a.keys()) & set(b.keys())
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def cosine_distance(a, b):
    return 1.0 - cosine(a, b)


# -------- Sentiment --------

def benzinga_sentiment_for_ticker(insights, ticker):
    """Return (score, reasoning) or None when absent."""
    if not insights:
        return None
    for ins in insights:
        if ins.get("ticker") == ticker:
            label = (ins.get("sentiment") or "").lower()
            reasoning = ins.get("sentiment_reasoning")
            if label == "positive":
                return (+0.7, reasoning)
            if label == "negative":
                return (-0.7, reasoning)
            if label == "neutral":
                return (0.0, reasoning)
    return None


def keyword_sentiment(article):
    title = article.get("title") or ""
    desc = article.get("description") or ""
    text = f"{title}. {re.split(r'(?<=[.!?]) ', desc, maxsplit=1)[0]}"
    toks = WORD_RE.findall(text.lower())
    pos = 0
    neg = 0
    for tok in toks:
        if tok in POSITIVE_LEX:
            pos += 1
        if tok in NEGATIVE_LEX:
            neg += 1
        if tok in EXTREME_NEG:
            neg += 1  # double weight (1 + 1)
    total_words = max(1, len(toks))
    raw = (pos - neg) / max(1, total_words / 20)
    return max(-1.0, min(1.0, raw))


# -------- Reaction window --------

def epoch_ms(dt):
    return int(dt.timestamp() * 1000)


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# Cache minute aggs per ticker so we don't re-fetch
_AGG_CACHE = {}


def get_aggs_for_ticker(ticker, from_dt, to_dt, resolution=5):
    key = (ticker, from_dt.date().isoformat(), to_dt.date().isoformat(), resolution)
    if key in _AGG_CACHE:
        return _AGG_CACHE[key]
    aggs = fetch_minute_aggs(ticker, key[1], key[2], resolution=resolution)
    _AGG_CACHE[key] = aggs
    return aggs


def find_bar(aggs, target_ms):
    """Find the bar covering target_ms. Aggs sorted ascending by t."""
    if not aggs:
        return None, None
    # Linear scan is fine; ~400 bars per day
    for i, bar in enumerate(aggs):
        bar_t = bar.get("t")
        if bar_t is None:
            continue
        # Each bar covers [t, t + resolution_ms). Find first whose t > target.
        if bar_t > target_ms:
            return aggs[i - 1] if i > 0 else None, i - 1
    return aggs[-1], len(aggs) - 1


# H8 fix: anchor reaction to the FIRST bar at or after publish_ts, rather than
# the bar containing publish_ts. Publish timestamps frequently land in gaps
# (weekends, holidays, pre-market) where the prior-containing bar would be
# stale or wrong.
NO_BAR_AFTER_PUBLISH_MAX_HOURS = 24


def find_first_bar_at_or_after(aggs, target_ms, max_forward_ms=None):
    """Return (bar, idx) for the first bar with start_time >= target_ms.

    If max_forward_ms is set and no bar starts within that forward window,
    returns (None, None) so the caller can surface a skip reason.
    """
    if not aggs:
        return None, None
    cutoff_ms = (target_ms + max_forward_ms) if max_forward_ms is not None else None
    for i, bar in enumerate(aggs):
        bar_t = bar.get("t")
        if bar_t is None:
            continue
        if bar_t >= target_ms:
            if cutoff_ms is not None and bar_t > cutoff_ms:
                return None, None
            return bar, i
    return None, None


# H8 fix: 5-trading-day baseline. We need to GUARANTEE >=5 trading days in
# the fetched range. Fetching publish_ts - 5 calendar days gives <5 trading
# days whenever a weekend lands in the window (most of the time) and gets
# worse around holidays. 12 calendar days reliably covers 5 trading days even
# across Thanksgiving week / July 4 stretches without being wasteful.
BASELINE_TRADING_DAYS = 5
BASELINE_FETCH_CALENDAR_DAYS = 12


def compute_reaction(ticker, published_at):
    """Return dict with reaction_pct, window_label, window_minutes, anomaly, baseline, etc.

    On structural skips (no bar after publish, insufficient baseline) returns a
    dict containing a "reason" key so the caller can surface it.
    """
    if not published_at:
        return None
    # H8: widen fetch window so the baseline reliably contains >=5 trading days
    # even across holiday-heavy weeks. 12 calendar days is the safe choice.
    from_dt = published_at - timedelta(days=BASELINE_FETCH_CALENDAR_DAYS)
    # Reaction may span overnight; pull through next session
    to_dt = published_at + timedelta(days=2)
    aggs = get_aggs_for_ticker(ticker, from_dt, to_dt, resolution=5)
    if not aggs:
        return None

    pub_ms = epoch_ms(published_at)
    # H8 fix 1: anchor to the FIRST bar at or after publish_ts (next available
    # trading minute), not the bar containing publish_ts. If no bar starts
    # within 24h forward of publish, surface a skip reason.
    base_bar, base_idx = find_first_bar_at_or_after(
        aggs, pub_ms, max_forward_ms=NO_BAR_AFTER_PUBLISH_MAX_HOURS * 3600 * 1000
    )
    if base_bar is None or base_idx is None:
        return {"reason": "no_bar_after_publish"}
    reaction_anchor_offset_seconds = round((base_bar["t"] - pub_ms) / 1000)

    # Target window: publish + 60min, capped at 16:00 ET of the publish day.
    # zoneinfo handles DST so winter publishes no longer mis-bucket.
    pub_et = utc_to_et(published_at)
    close_et_today = pub_et.replace(hour=16, minute=0, second=0, microsecond=0)
    if pub_et > close_et_today:
        # After-hours publish. Defer to next-day open.
        # Find next 09:30 ET
        next_open_et = (pub_et + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        target_dt = next_open_et + timedelta(minutes=REACTION_MINUTES_DEFAULT)
        target_et_cap = next_open_et.replace(hour=16, minute=0, second=0, microsecond=0)
        if target_dt > target_et_cap:
            target_dt = target_et_cap
        target_utc = target_dt.astimezone(timezone.utc)
        window_label = "overnight"
        is_overnight = True
    elif pub_et < close_et_today.replace(hour=9, minute=30):
        # Pre-market publish. Use open + 60 min.
        open_et = pub_et.replace(hour=9, minute=30, second=0, microsecond=0)
        target_dt = open_et + timedelta(minutes=REACTION_MINUTES_DEFAULT)
        if target_dt > close_et_today:
            target_dt = close_et_today
        target_utc = target_dt.astimezone(timezone.utc)
        window_label = "pre-market"
        is_overnight = False
    else:
        # Intraday publish
        target_et = pub_et + timedelta(minutes=REACTION_MINUTES_DEFAULT)
        if target_et > close_et_today:
            target_et = close_et_today
        target_utc = target_et.astimezone(timezone.utc)
        window_label = None
        is_overnight = False

    target_ms = epoch_ms(target_utc)
    target_bar, target_idx = find_bar(aggs, target_ms)
    if target_bar is None or target_idx is None or target_idx <= base_idx:
        # Window hasn't closed yet
        return {
            "reaction_pct": None,
            "reaction_window_label": "pending",
            "reaction_window_minutes": None,
            "price_at_publish": base_bar.get("c"),
            "price_at_window_end": None,
            "volume_anomaly_x": None,
            "reaction_anchor_offset_seconds": reaction_anchor_offset_seconds,
            "n_baseline_days": None,
        }

    base_close = base_bar.get("c") or base_bar.get("o")
    end_close = target_bar.get("c")
    if not base_close or not end_close or base_close <= 0:
        return None

    reaction_pct = (end_close / base_close) - 1.0

    # Window minutes from bar timestamps
    window_minutes = round((target_bar["t"] - base_bar["t"]) / 60000)
    if window_label is None:
        if window_minutes < 60:
            window_label = f"{window_minutes}min"
        else:
            h = window_minutes // 60
            m = window_minutes % 60
            window_label = f"{h}h" if m == 0 else f"{h}h {m}min"

    # Volume anomaly: avg per-minute volume during window / prior-5-day same-TOD per-minute average
    window_bars = aggs[base_idx : target_idx + 1]
    window_vol = sum((b.get("v") or 0) for b in window_bars)
    window_minutes_for_vol = max(1, window_minutes)  # 5-min res but normalize to per-minute
    window_per_min_vol = window_vol / window_minutes_for_vol

    # H8 fix 2: baseline over prior 5 trading days. Walk back enough calendar
    # days to GUARANTEE >=5 trading days. We fetched BASELINE_FETCH_CALENDAR_DAYS
    # of history; walk that whole window so weekends and holidays don't shrink
    # the sample. Dedup by ET trading date so we count actual trading days.
    baseline_vols_per_min = []
    seen_trading_dates = set()
    for d_back in range(1, BASELINE_FETCH_CALENDAR_DAYS + 1):
        prior_pub = published_at - timedelta(days=d_back)
        prior_target = target_utc - timedelta(days=d_back)
        prior_base, pb_idx = find_bar(aggs, epoch_ms(prior_pub))
        prior_end, pe_idx = find_bar(aggs, epoch_ms(prior_target))
        if prior_base and prior_end and pe_idx is not None and pb_idx is not None and pe_idx > pb_idx:
            # Use the ET date of the prior_base bar to identify the trading day.
            bar_dt_et = utc_to_et(datetime.fromtimestamp(prior_base["t"] / 1000, tz=timezone.utc))
            trading_date = bar_dt_et.date().isoformat()
            if trading_date in seen_trading_dates:
                continue
            seen_trading_dates.add(trading_date)
            bars = aggs[pb_idx : pe_idx + 1]
            v = sum((b.get("v") or 0) for b in bars)
            mins = max(1, round((prior_end["t"] - prior_base["t"]) / 60000))
            baseline_vols_per_min.append(v / mins)
        if len(baseline_vols_per_min) >= BASELINE_TRADING_DAYS:
            break

    n_baseline_days = len(baseline_vols_per_min)

    # H8: if we still don't have 5 trading days after walking the full fetch
    # window, the sample is too small to claim a baseline. Skip with reason.
    if n_baseline_days < BASELINE_TRADING_DAYS:
        return {
            "reason": "insufficient_baseline",
            "n_baseline_days": n_baseline_days,
            "reaction_anchor_offset_seconds": reaction_anchor_offset_seconds,
        }

    baseline = sum(baseline_vols_per_min) / n_baseline_days
    anomaly = (window_per_min_vol / baseline) if baseline > 0 else None

    return {
        "reaction_pct": reaction_pct,
        "reaction_window_label": window_label,
        "reaction_window_minutes": window_minutes,
        "price_at_publish": base_close,
        "price_at_window_end": end_close,
        "volume_anomaly_x": anomaly,
        "reaction_anchor_offset_seconds": reaction_anchor_offset_seconds,
        "n_baseline_days": n_baseline_days,
    }


# -------- Divergence flag --------

def divergence(sentiment, reaction_pct):
    if reaction_pct is None:
        return "none"
    if abs(sentiment) < MIN_SENTIMENT_FOR_DIVERGENCE:
        return "none"
    if abs(reaction_pct) < MIN_REACTION_FOR_DIVERGENCE:
        return "none"
    if sentiment > 0 and reaction_pct < 0:
        return "positive_news_negative_reaction"
    if sentiment < 0 and reaction_pct > 0:
        return "negative_news_positive_reaction"
    return "none"


# -------- Main scan --------

print(f"Scanning {len(TICKERS)} tickers over last {WINDOW_HOURS}h...", file=sys.stderr)

# Fetch all news per ticker (in window for candidates, in 7-day bucket for novelty)
ticker_news_raw = {}
benzinga_present = False
for t in TICKERS:
    print(f"  fetching news: {t}", file=sys.stderr)
    try:
        articles = fetch_news(t, NOVELTY_BUCKET_START_UTC.isoformat())
    except Exception as e:
        print(f"  warn: {t}: {e}", file=sys.stderr)
        articles = []
    ticker_news_raw[t] = articles
    if any(a.get("insights") for a in articles):
        benzinga_present = True

tier = "A" if (benzinga_present and SENT_MODE == "auto") else "B"

# Build per-ticker IDF over the 7-day bucket
ticker_idf = {}
ticker_token_lists = {}
for t, articles in ticker_news_raw.items():
    tlists = [tokens(feature_string(a), ticker=t) for a in articles]
    idf, _ = build_idf(tlists)
    ticker_idf[t] = idf
    ticker_token_lists[t] = tlists


# URL dedup global
seen_urls = {}
for t, articles in ticker_news_raw.items():
    for a in articles:
        url = a.get("article_url")
        if url and url not in seen_urls:
            seen_urls[url] = (t, a)

# Build candidate events: (ticker, article) within the WINDOW_HOURS bucket only
candidates = []
skipped = []
for t, articles in ticker_news_raw.items():
    in_window = 0
    for idx, a in enumerate(articles):
        pub = parse_iso(a.get("published_utc"))
        if not pub or pub < WINDOW_START_UTC:
            continue
        in_window += 1
        # Dedup: if this article's URL already claimed by a different earlier ticker,
        # skip the duplicate event for this ticker IF the article is not actually tagged on this ticker
        # (always include if tagged; the same article can score on multiple tickers legitimately)
        article_tickers = a.get("tickers") or []
        if t not in article_tickers:
            continue
        candidates.append((t, a, idx))
    if in_window == 0:
        skipped.append({"ticker": t, "reason": f"no articles in last {WINDOW_HOURS}h"})

print(f"  {len(candidates)} candidate events in window", file=sys.stderr)

# H8: run-level counters for new structural skip reasons.
skipped_no_bar_count = 0
insufficient_baseline_count = 0
skipped_events = []

# Score every candidate
events = []
for t, a, art_idx in candidates:
    pub = parse_iso(a.get("published_utc"))
    if not pub:
        continue

    # Sentiment
    sent_score = None
    sent_source = None
    sent_reasoning = None
    if SENT_MODE == "auto":
        bz = benzinga_sentiment_for_ticker(a.get("insights") or [], t)
        if bz is not None:
            sent_score, sent_reasoning = bz
            sent_source = "benzinga"
    if sent_score is None:
        sent_score = keyword_sentiment(a)
        sent_source = "keyword"
        sent_reasoning = None

    # Novelty: cosine distance to nearest article published earlier than this one
    cand_tokens = tokens(feature_string(a), ticker=t)
    cand_vec = tfidf_vector(cand_tokens, ticker_idf.get(t, {}))
    nearest = None
    min_dist = 1.0
    for other in ticker_news_raw[t]:
        if other is a:
            continue
        other_pub = parse_iso(other.get("published_utc"))
        if not other_pub or other_pub >= pub:
            continue
        other_tokens = tokens(feature_string(other), ticker=t)
        other_vec = tfidf_vector(other_tokens, ticker_idf.get(t, {}))
        d = cosine_distance(cand_vec, other_vec)
        if d < min_dist:
            min_dist = d
            nearest = other
    novelty_score = min_dist if nearest else 1.0
    if novelty_score > 0.6:
        novelty_band = "high"
    elif novelty_score >= 0.3:
        novelty_band = "medium"
    else:
        novelty_band = "low"

    # Reaction
    rx = compute_reaction(t, pub)
    # H8: structural skip — surface reason, count it, drop the event from the
    # ranked stream so consumers don't see a half-computed reaction.
    if isinstance(rx, dict) and rx.get("reason"):
        reason = rx["reason"]
        if reason == "no_bar_after_publish":
            skipped_no_bar_count += 1
        elif reason == "insufficient_baseline":
            insufficient_baseline_count += 1
        skipped_events.append({
            "ticker": t,
            "id": a.get("id"),
            "published_at": pub.isoformat(),
            "headline": a.get("title") or "",
            "reason": reason,
            "n_baseline_days": rx.get("n_baseline_days"),
            "reaction_anchor_offset_seconds": rx.get("reaction_anchor_offset_seconds"),
        })
        continue
    if rx is None:
        rx = {
            "reaction_pct": None,
            "reaction_window_label": "n/a",
            "reaction_window_minutes": None,
            "price_at_publish": None,
            "price_at_window_end": None,
            "volume_anomaly_x": None,
            "reaction_anchor_offset_seconds": None,
            "n_baseline_days": None,
        }

    div = divergence(sent_score, rx["reaction_pct"])

    # Context line: prefer divergence, else novelty paraphrase
    context_line = None
    if div == "positive_news_negative_reaction":
        context_line = f"DIVERGENCE: positive sentiment, {rx['reaction_pct']*100:+.1f}% reaction. Likely priced in."
    elif div == "negative_news_positive_reaction":
        context_line = f"DIVERGENCE: negative sentiment, {rx['reaction_pct']*100:+.1f}% reaction. Tape says 'not as bad.'"
    elif nearest and novelty_band == "low":
        prior_title = (nearest.get("title") or "")[:60]
        context_line = f"near-duplicate of prior coverage: \"{prior_title}\""
    elif nearest and novelty_band == "medium":
        prior_title = (nearest.get("title") or "")[:60]
        context_line = f"related angle to: \"{prior_title}\""

    # Impact = |reaction| × volume_anomaly × novelty_score (degrade gracefully)
    rxn = abs(rx["reaction_pct"]) if rx["reaction_pct"] is not None else 0
    anom = rx["volume_anomaly_x"] if rx["volume_anomaly_x"] is not None else 1.0
    impact = rxn * anom * novelty_score

    pub_et = utc_to_et(pub)
    events.append({
        "id": a.get("id"),
        "ticker": t,
        "published_at": pub.isoformat(),
        "published_at_et": pub_et.strftime("%Y-%m-%d %H:%M ET"),
        "source": (a.get("publisher") or {}).get("name") or "unknown",
        "headline": a.get("title") or "",
        "url": a.get("article_url") or "",
        "sentiment_score": round(sent_score, 3),
        "sentiment_source": sent_source,
        "sentiment_reasoning": sent_reasoning,
        "novelty_score": round(novelty_score, 3),
        "novelty_band": novelty_band,
        "nearest_prior": (
            {
                "published_at": parse_iso(nearest.get("published_utc")).isoformat() if nearest else None,
                "headline": nearest.get("title") if nearest else None,
                "distance": round(min_dist, 3),
            } if nearest else None
        ),
        "reaction_pct_since_publish": rx["reaction_pct"],
        "reaction_window_label": rx["reaction_window_label"],
        "reaction_window_minutes": rx["reaction_window_minutes"],
        "reaction_anchor_offset_seconds": rx.get("reaction_anchor_offset_seconds"),
        "n_baseline_days": rx.get("n_baseline_days"),
        "price_at_publish": rx["price_at_publish"],
        "price_at_window_end": rx["price_at_window_end"],
        "volume_anomaly_x": rx["volume_anomaly_x"],
        "divergence_flag": div,
        "context_line": context_line,
        "keywords": a.get("keywords") or [],
        "related_event_ids": [],
        "impact_score": round(impact, 6),
    })

# Sort by impact descending
events.sort(key=lambda e: e["impact_score"], reverse=True)

# Same-story dedup pass: collapse near-duplicates within 60min for same ticker
final_events = []
absorbed = set()
for i, e in enumerate(events):
    if e["id"] in absorbed:
        continue
    keep = e
    pub_i = parse_iso(e["published_at"])
    # Build vector for comparison
    art_idf = ticker_idf.get(e["ticker"], {})
    # Reconstruct tokens from headline (good-enough proxy)
    e_vec = tfidf_vector(tokens(e["headline"], ticker=e["ticker"]), art_idf)
    for j in range(i + 1, len(events)):
        f = events[j]
        if f["ticker"] != e["ticker"]:
            continue
        if f["id"] in absorbed:
            continue
        pub_j = parse_iso(f["published_at"])
        if abs((pub_i - pub_j).total_seconds()) > 3600:
            continue
        f_vec = tfidf_vector(tokens(f["headline"], ticker=f["ticker"]), art_idf)
        if cosine_distance(e_vec, f_vec) < 0.2:
            absorbed.add(f["id"])
            keep["related_event_ids"].append(f["id"])
    final_events.append(keep)

# Cap at TOP_N
top_events = final_events[:TOP_N]

# Summary
def band_of_score(s):
    if s > 0.2:
        return "positive"
    if s < -0.2:
        return "negative"
    return "neutral"


sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0}
novelty_counts = {"high": 0, "medium": 0, "low": 0}
divergence_count = 0
for e in top_events:
    sentiment_counts[band_of_score(e["sentiment_score"])] += 1
    novelty_counts[e["novelty_band"]] += 1
    if e["divergence_flag"] != "none":
        divergence_count += 1

tickers_in_top = sorted({e["ticker"] for e in top_events})

# Build "take" — one to two sentences on what moved
top_movers = sorted(
    (e for e in top_events if e["reaction_pct_since_publish"] is not None),
    key=lambda e: abs(e["reaction_pct_since_publish"]),
    reverse=True,
)[:2]
if top_movers:
    parts = []
    for e in top_movers:
        sign = "+" if e["reaction_pct_since_publish"] >= 0 else ""
        parts.append(
            f"{e['ticker']} {sign}{e['reaction_pct_since_publish']*100:.1f}% on "
            f"{e['source']}'s \"{e['headline'][:50]}{'…' if len(e['headline']) > 50 else ''}\""
        )
    take = "Window's biggest moves: " + "; ".join(parts) + "."
else:
    take = "No material reactions in window."

# Payload
payload = {
    "tier": tier,
    "tier_caveats": (
        []
        if tier == "A"
        else [
            "Benzinga insights[] unavailable or forced-off; sentiment from keyword scorer.",
            "Sentiment is article-level, not sentence-level; sarcasm and negation are not handled.",
        ]
    ),
    "mode": "stream",
    "run_at": NOW_UTC.isoformat(),
    "scan_params": {
        "watchlist": TICKERS,
        "window_hours": WINDOW_HOURS,
        "top_n": TOP_N,
        "min_reaction_pct": MIN_REACTION_FOR_DIVERGENCE,
        "reaction_minutes": REACTION_MINUTES_DEFAULT,
        "sentiment_mode": SENT_MODE,
    },
    "events": top_events,
    "summary": {
        "count": len(top_events),
        "tickers_with_events": len(tickers_in_top),
        "by_sentiment": sentiment_counts,
        "by_novelty": novelty_counts,
        "divergence_count": divergence_count,
        # H8: structural skip counts so consumers can see when reaction/baseline
        # computation dropped articles vs the scan finding nothing.
        "skipped_no_bar_count": skipped_no_bar_count,
        "insufficient_baseline_count": insufficient_baseline_count,
    },
    "take": take,
    "skipped_tickers": skipped,
    "skipped_events": skipped_events,
    "sources": [
        {
            "endpoint": "https://api.polygon.io/v2/reference/news",
            "fetched_at": news_last_fetched_at,
            "context": "Benzinga News, per-ticker, last 7 days for novelty bucket",
        },
        {
            "endpoint": "https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/{from}/{to}",
            "fetched_at": aggs_last_fetched_at,
            "context": "5-minute aggregates for reaction window and volume anomaly baseline",
        },
    ],
}


# -------- Render --------

def truncate_headline(h, n=90):
    if not h:
        return ""
    if len(h) <= n:
        return h
    return h[: n - 1] + "…"


def render_block(e):
    ticker = e["ticker"].ljust(4)
    pub_et = e["published_at_et"]
    src = e["source"]
    line1 = f"{ticker}  {pub_et}  {src}"

    headline = truncate_headline(e["headline"])
    line2 = f"HEADLINE: {headline}"

    parts = []
    parts.append(f"SENTIMENT: {e['sentiment_score']:+.2f}")
    parts.append(f"NOVELTY: {e['novelty_band']}")
    if e["reaction_pct_since_publish"] is None:
        parts.append("REACTION: pending overnight")
    else:
        rpct = e["reaction_pct_since_publish"] * 100
        parts.append(f"REACTION: {rpct:+.1f}% ({e['reaction_window_label']})")
    if e["volume_anomaly_x"] is None:
        parts.append("baseline vol n/a")
    else:
        parts.append(f"{e['volume_anomaly_x']:.1f}x baseline vol")
    line3 = " · ".join(parts)

    block = [line1, line2, line3]
    if e["context_line"]:
        block.append(f"↳ {e['context_line']}")
    return "\n".join(block)


lines = []
window_label = f"{WINDOW_HOURS}h"
header = (
    f"{len(top_events)} events surfaced from {len(tickers_in_top)} tickers · "
    f"window: last {window_label} · "
    f"run {NOW_UTC.strftime('%Y-%m-%d %H:%M')} UTC"
)
lines.append(header)
if tier == "B":
    lines.append("Note: keyword sentiment scorer in use (Benzinga insights not available). Reaction window: 5-min aggs.")
lines.append("")

for e in top_events:
    lines.append(render_block(e))
    lines.append("")

skipped_names = [s["ticker"] for s in skipped]
footer = (
    f"End of stream. {len(top_events)} events across {len(tickers_in_top)} tickers."
)
if skipped_names:
    footer += f" {len(skipped_names)} tickers skipped: {', '.join(skipped_names)}."
# H8: surface structural reaction/baseline skips in the rendered stream so
# operators reading the tape can see why article counts shrank.
if skipped_no_bar_count or insufficient_baseline_count:
    footer += (
        f" Reaction skips: {skipped_no_bar_count} no_bar_after_publish, "
        f"{insufficient_baseline_count} insufficient_baseline."
    )
lines.append(footer)

rendered = "\n".join(lines)


# -------- Write output --------

out_name = "news-scanner-output.md"
out_path = os.path.join(os.path.dirname(__file__), out_name)
with open(out_path, "w") as f:
    f.write("# news-scanner run\n\n")
    f.write(f"Generated: {NOW_UTC.isoformat()}\n")
    f.write(f"Watchlist: {', '.join(TICKERS)}\n")
    f.write(f"Window: last {WINDOW_HOURS}h\n")
    f.write(f"Tier: {tier}\n\n")
    f.write("## Take\n\n")
    f.write(take + "\n\n")
    f.write("## Layer 1: canonical JSON (live data)\n\n")
    f.write("```json\n")
    f.write(json.dumps(payload, indent=2, default=str))
    f.write("\n```\n\n")
    f.write("## Layer 2: rendered stream (live data)\n\n")
    f.write("```\n")
    f.write(rendered)
    f.write("\n```\n")

print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
emit_to_stdout(rendered, payload, fmt)
