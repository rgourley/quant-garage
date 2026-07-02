"""
news-scanner as an importable library function.

Scans a watchlist over a lookback window for notable news events. For
each event surfaces headline, source, per-ticker sentiment (Benzinga
insights when available, keyword fallback otherwise), novelty band, and
the stock's post-publish price reaction (5-minute aggs baseline). Ranks
by impact and returns a Bloomberg-tape-style stream + canonical JSON.

    from quant_garage.skills.news_scanner import run, render
    payload = run(["NVDA","TSLA","AAPL"], hours=24, top_n=15)
"""
from __future__ import annotations

import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .. import (
    MassiveClient,
    utc_to_et,
    utcnow_iso,
    percentile_rank,
    format_rank_label,
)


REACTION_MINUTES_DEFAULT = 60
MIN_REACTION_FOR_DIVERGENCE = 0.005
MIN_SENTIMENT_FOR_DIVERGENCE = 0.4
NO_BAR_AFTER_PUBLISH_MAX_HOURS = 24
BASELINE_TRADING_DAYS = 5
BASELINE_FETCH_CALENDAR_DAYS = 12


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

WORD_RE = re.compile(r"[a-z0-9]+")

# Cross-call minute-aggs cache (same-process reuse: news + earnings + event-study
# all hit /v2/aggs/*/range/5/minute for reaction windows).
_AGG_CACHE: dict[tuple, list[dict]] = {}


class _Sources:
    def __init__(self) -> None:
        self.news_last_fetched_at = utcnow_iso()
        self.aggs_last_fetched_at = utcnow_iso()


# ----- HTTP -----

def _fetch_news(
    client: MassiveClient, ticker: str, gte_iso: str,
    max_pages: int, sources: _Sources, limit_per_page: int = 50,
) -> tuple[list[dict], bool]:
    params = {
        "ticker": ticker,
        "published_utc.gte": gte_iso,
        "order": "desc",
        "sort": "published_utc",
        "limit": limit_per_page,
    }
    out: list[dict] = []
    pages_seen = 0
    capped = False
    last_page_len = 0
    for page, fetched_at in client.paginate("/v2/reference/news", params):
        out.extend(page)
        sources.news_last_fetched_at = fetched_at
        pages_seen += 1
        last_page_len = len(page)
        if pages_seen >= max_pages:
            if last_page_len == limit_per_page:
                capped = True
            break
    return out, capped


def _fetch_minute_aggs(
    client: MassiveClient, ticker: str, frm_date: str, to_date: str,
    sources: _Sources, resolution: int = 5,
) -> list[dict]:
    path = (
        f"/v2/aggs/ticker/{ticker}/range/{resolution}/minute/"
        f"{frm_date}/{to_date}?adjusted=true&sort=asc&limit=5000"
    )
    try:
        doc, fetched_at = client.get(path)
    except Exception as e:
        print(f"  warn: aggs fetch failed for {ticker}: {e}", file=sys.stderr)
        return []
    sources.aggs_last_fetched_at = fetched_at
    return doc.get("results", []) or []


def _get_aggs_for_ticker(
    client: MassiveClient, ticker: str,
    from_dt: datetime, to_dt: datetime, sources: _Sources, resolution: int = 5,
) -> list[dict]:
    key = (ticker, from_dt.date().isoformat(), to_dt.date().isoformat(), resolution)
    if key in _AGG_CACHE:
        return _AGG_CACHE[key]
    aggs = _fetch_minute_aggs(client, ticker, key[1], key[2], sources, resolution=resolution)
    _AGG_CACHE[key] = aggs
    return aggs


# ----- Tokenization / TF-IDF -----

def _tokens(text: str, ticker: str | None = None) -> list[str]:
    if not text:
        return []
    raw = WORD_RE.findall(text.lower())
    skip = STOPWORDS | ({ticker.lower()} if ticker else set())
    return [t for t in raw if t not in skip and len(t) > 1]


def _feature_string(article: dict) -> str:
    title = article.get("title") or ""
    desc = article.get("description") or ""
    first = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0]
    return f"{title} {first}"


def _tf_vector(tokens_list: list[str]) -> dict[str, int]:
    if not tokens_list:
        return {}
    out: dict[str, int] = defaultdict(int)
    for t in tokens_list:
        out[t] += 1
    return dict(out)


def _build_idf(token_lists: list[list[str]]) -> tuple[dict[str, float], int]:
    df: dict[str, int] = defaultdict(int)
    n = len(token_lists)
    if n == 0:
        return {}, 0
    for tl in token_lists:
        for t in set(tl):
            df[t] += 1
    idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
    return idf, n


def _tfidf_vector(tokens_list: list[str], idf: dict[str, float]) -> dict[str, float]:
    if not tokens_list:
        return {}
    tf = _tf_vector(tokens_list)
    total = sum(tf.values())
    if total == 0:
        return {}
    return {t: (c / total) * idf.get(t, 1.0) for t, c in tf.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a.keys()) & set(b.keys())
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _cosine_distance(a: dict[str, float], b: dict[str, float]) -> float:
    return 1.0 - _cosine(a, b)


# ----- Sentiment -----

def _benzinga_sentiment_for_ticker(insights: list[dict], ticker: str) -> tuple[float, str | None] | None:
    if not insights:
        return None
    for ins in insights:
        if ins.get("ticker") == ticker:
            label = (ins.get("sentiment") or "").lower()
            reasoning = ins.get("sentiment_reasoning")
            if label == "positive": return (+0.7, reasoning)
            if label == "negative": return (-0.7, reasoning)
            if label == "neutral":  return (0.0, reasoning)
    return None


def _keyword_sentiment(article: dict) -> float:
    title = article.get("title") or ""
    desc = article.get("description") or ""
    text = f"{title}. {re.split(r'(?<=[.!?]) ', desc, maxsplit=1)[0]}"
    toks = WORD_RE.findall(text.lower())
    pos = 0; neg = 0
    for tok in toks:
        if tok in POSITIVE_LEX: pos += 1
        if tok in NEGATIVE_LEX: neg += 1
        if tok in EXTREME_NEG:  neg += 1
    raw = (pos - neg) / max(1, max(1, len(toks)) / 20)
    return max(-1.0, min(1.0, raw))


# ----- Reaction window -----

def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _find_bar(aggs: list[dict], target_ms: int) -> tuple[dict | None, int | None]:
    if not aggs:
        return None, None
    for i, bar in enumerate(aggs):
        bar_t = bar.get("t")
        if bar_t is None:
            continue
        if bar_t > target_ms:
            return (aggs[i - 1] if i > 0 else None), i - 1
    return aggs[-1], len(aggs) - 1


def _find_first_bar_at_or_after(
    aggs: list[dict], target_ms: int, max_forward_ms: int | None = None,
) -> tuple[dict | None, int | None]:
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


def _compute_reaction(
    client: MassiveClient, ticker: str, published_at: datetime, sources: _Sources,
) -> dict | None:
    if not published_at:
        return None
    from_dt = published_at - timedelta(days=BASELINE_FETCH_CALENDAR_DAYS)
    to_dt = published_at + timedelta(days=2)
    aggs = _get_aggs_for_ticker(client, ticker, from_dt, to_dt, sources, resolution=5)
    if not aggs:
        return None
    pub_ms = _epoch_ms(published_at)
    base_bar, base_idx = _find_first_bar_at_or_after(
        aggs, pub_ms, max_forward_ms=NO_BAR_AFTER_PUBLISH_MAX_HOURS * 3600 * 1000
    )
    if base_bar is None or base_idx is None:
        return {"reason": "no_bar_after_publish"}
    reaction_anchor_offset_seconds = round((base_bar["t"] - pub_ms) / 1000)

    pub_et = utc_to_et(published_at)
    close_et_today = pub_et.replace(hour=16, minute=0, second=0, microsecond=0)
    if pub_et > close_et_today:
        next_open_et = (pub_et + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        target_dt = next_open_et + timedelta(minutes=REACTION_MINUTES_DEFAULT)
        target_et_cap = next_open_et.replace(hour=16, minute=0, second=0, microsecond=0)
        if target_dt > target_et_cap:
            target_dt = target_et_cap
        target_utc = target_dt.astimezone(timezone.utc)
        window_label = "overnight"
    elif pub_et < close_et_today.replace(hour=9, minute=30):
        open_et = pub_et.replace(hour=9, minute=30, second=0, microsecond=0)
        target_dt = open_et + timedelta(minutes=REACTION_MINUTES_DEFAULT)
        if target_dt > close_et_today:
            target_dt = close_et_today
        target_utc = target_dt.astimezone(timezone.utc)
        window_label = "pre-market"
    else:
        target_et = pub_et + timedelta(minutes=REACTION_MINUTES_DEFAULT)
        if target_et > close_et_today:
            target_et = close_et_today
        target_utc = target_et.astimezone(timezone.utc)
        window_label = None

    target_ms = _epoch_ms(target_utc)
    target_bar, target_idx = _find_bar(aggs, target_ms)
    if target_bar is None or target_idx is None or target_idx <= base_idx:
        return {
            "reaction_pct": None, "reaction_window_label": "pending",
            "reaction_window_minutes": None,
            "price_at_publish": base_bar.get("c"), "price_at_window_end": None,
            "volume_anomaly_x": None,
            "reaction_anchor_offset_seconds": reaction_anchor_offset_seconds,
            "n_baseline_days": None,
        }

    base_close = base_bar.get("c") or base_bar.get("o")
    end_close = target_bar.get("c")
    if not base_close or not end_close or base_close <= 0:
        return None
    reaction_pct = (end_close / base_close) - 1.0

    window_minutes = round((target_bar["t"] - base_bar["t"]) / 60000)
    if window_label is None:
        if window_minutes < 60:
            window_label = f"{window_minutes}min"
        else:
            h = window_minutes // 60
            m = window_minutes % 60
            window_label = f"{h}h" if m == 0 else f"{h}h {m}min"

    window_bars = aggs[base_idx : target_idx + 1]
    window_vol = sum((b.get("v") or 0) for b in window_bars)
    window_minutes_for_vol = max(1, window_minutes)
    window_per_min_vol = window_vol / window_minutes_for_vol

    baseline_vols_per_min: list[float] = []
    seen_trading_dates: set[str] = set()
    for d_back in range(1, BASELINE_FETCH_CALENDAR_DAYS + 1):
        prior_pub = published_at - timedelta(days=d_back)
        prior_target = target_utc - timedelta(days=d_back)
        prior_base, pb_idx = _find_bar(aggs, _epoch_ms(prior_pub))
        prior_end, pe_idx = _find_bar(aggs, _epoch_ms(prior_target))
        if prior_base and prior_end and pe_idx is not None and pb_idx is not None and pe_idx > pb_idx:
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


def _divergence(sentiment: float, reaction_pct: float | None) -> str:
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


# ----- Public API -----

def run(
    watchlist: Iterable[str] | str,
    hours: int = 24,
    top_n: int = 15,
    sentiment_mode: str = "auto",
    client: MassiveClient | None = None,
) -> dict:
    """Scan a watchlist for notable news events. Return canonical payload dict.

    Args:
        watchlist: comma-separated string or iterable of tickers.
        hours: lookback window. Default 24.
        top_n: max events to surface. Default 15.
        sentiment_mode: 'auto' (Benzinga → keyword) or 'keyword' (force fallback).
        client: reuse an existing MassiveClient.
    """
    if isinstance(watchlist, str):
        tickers = [t.strip().upper() for t in watchlist.split(",") if t.strip()]
    else:
        tickers = [t.strip().upper() for t in watchlist if t and t.strip()]
    if not tickers:
        raise ValueError("watchlist must contain at least one ticker")
    if sentiment_mode not in ("auto", "keyword"):
        raise ValueError("sentiment_mode must be 'auto' or 'keyword'")

    client = client or MassiveClient()
    sources = _Sources()
    now_utc = datetime.now(timezone.utc)
    window_start_utc = now_utc - timedelta(hours=hours)
    effective_news_window_hours = max(hours, 7 * 24)
    news_fetch_start_utc = now_utc - timedelta(hours=effective_news_window_hours)
    effective_window_days = effective_news_window_hours / 24
    max_pages_per_ticker = max(4, int(effective_window_days * 25 / 50) + 1)
    max_articles_per_ticker = max_pages_per_ticker * 50

    ticker_news_raw: dict[str, list[dict]] = {}
    benzinga_present = False
    pagination_capped_tickers: list[dict] = []
    for t in tickers:
        try:
            articles, capped = _fetch_news(
                client, t, news_fetch_start_utc.isoformat(),
                max_pages=max_pages_per_ticker, sources=sources,
            )
        except Exception:
            articles, capped = [], False
        ticker_news_raw[t] = articles
        if capped:
            pagination_capped_tickers.append({"ticker": t, "n_fetched": len(articles)})
        if any(a.get("insights") for a in articles):
            benzinga_present = True

    tier = "A" if (benzinga_present and sentiment_mode == "auto") else "B"

    ticker_idf: dict[str, dict[str, float]] = {}
    for t, articles in ticker_news_raw.items():
        tlists = [_tokens(_feature_string(a), ticker=t) for a in articles]
        idf, _ = _build_idf(tlists)
        ticker_idf[t] = idf

    candidates: list[tuple[str, dict, int]] = []
    skipped: list[dict] = []
    for t, articles in ticker_news_raw.items():
        in_window = 0
        for idx, a in enumerate(articles):
            pub = _parse_iso(a.get("published_utc"))
            if not pub or pub < window_start_utc:
                continue
            in_window += 1
            article_tickers = a.get("tickers") or []
            if t not in article_tickers:
                continue
            candidates.append((t, a, idx))
        if in_window == 0:
            skipped.append({"ticker": t, "reason": f"no articles in last {hours}h"})

    skipped_no_bar_count = 0
    insufficient_baseline_count = 0
    skipped_events: list[dict] = []
    events: list[dict] = []
    for t, a, art_idx in candidates:
        pub = _parse_iso(a.get("published_utc"))
        if not pub:
            continue

        sent_score = None
        sent_source = None
        sent_reasoning = None
        if sentiment_mode == "auto":
            bz = _benzinga_sentiment_for_ticker(a.get("insights") or [], t)
            if bz is not None:
                sent_score, sent_reasoning = bz
                sent_source = "benzinga"
        if sent_score is None:
            sent_score = _keyword_sentiment(a)
            sent_source = "keyword"

        cand_tokens = _tokens(_feature_string(a), ticker=t)
        cand_vec = _tfidf_vector(cand_tokens, ticker_idf.get(t, {}))
        nearest = None
        min_dist = 1.0
        for other in ticker_news_raw[t]:
            if other is a:
                continue
            other_pub = _parse_iso(other.get("published_utc"))
            if not other_pub or other_pub >= pub:
                continue
            other_tokens = _tokens(_feature_string(other), ticker=t)
            other_vec = _tfidf_vector(other_tokens, ticker_idf.get(t, {}))
            d = _cosine_distance(cand_vec, other_vec)
            if d < min_dist:
                min_dist = d
                nearest = other
        novelty_score = min_dist if nearest else 1.0
        novelty_band = "high" if novelty_score > 0.6 else "medium" if novelty_score >= 0.3 else "low"

        rx = _compute_reaction(client, t, pub, sources)
        if isinstance(rx, dict) and rx.get("reason"):
            reason = rx["reason"]
            if reason == "no_bar_after_publish":
                skipped_no_bar_count += 1
            elif reason == "insufficient_baseline":
                insufficient_baseline_count += 1
            skipped_events.append({
                "ticker": t, "id": a.get("id"),
                "published_at": pub.isoformat(),
                "headline": a.get("title") or "",
                "reason": reason,
                "n_baseline_days": rx.get("n_baseline_days"),
                "reaction_anchor_offset_seconds": rx.get("reaction_anchor_offset_seconds"),
            })
            continue
        if rx is None:
            rx = {
                "reaction_pct": None, "reaction_window_label": "n/a",
                "reaction_window_minutes": None,
                "price_at_publish": None, "price_at_window_end": None,
                "volume_anomaly_x": None,
                "reaction_anchor_offset_seconds": None,
                "n_baseline_days": None,
            }

        div = _divergence(sent_score, rx["reaction_pct"])

        context_line = None
        if div == "positive_news_negative_reaction":
            context_line = f"DIVERGENCE: positive sentiment, {rx['reaction_pct']*100:+.1f}% reaction. Likely priced in."
        elif div == "negative_news_positive_reaction":
            context_line = f"DIVERGENCE: negative sentiment, {rx['reaction_pct']*100:+.1f}% reaction. Tape says 'not as bad.'"
        elif nearest and novelty_band == "low":
            context_line = f'near-duplicate of prior coverage: "{(nearest.get("title") or "")[:60]}"'
        elif nearest and novelty_band == "medium":
            context_line = f'related angle to: "{(nearest.get("title") or "")[:60]}"'

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
                    "published_at": _parse_iso(nearest.get("published_utc")).isoformat() if nearest else None,
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

    score_distribution = [e["impact_score"] for e in events if e.get("impact_score") is not None]
    score_universe_n = len(score_distribution)

    events.sort(key=lambda e: e["impact_score"], reverse=True)

    final_events: list[dict] = []
    absorbed: set = set()
    for i, e in enumerate(events):
        if e["id"] in absorbed:
            continue
        pub_i = _parse_iso(e["published_at"])
        art_idf = ticker_idf.get(e["ticker"], {})
        e_vec = _tfidf_vector(_tokens(e["headline"], ticker=e["ticker"]), art_idf)
        for j in range(i + 1, len(events)):
            f = events[j]
            if f["ticker"] != e["ticker"]:
                continue
            if f["id"] in absorbed:
                continue
            pub_j = _parse_iso(f["published_at"])
            if abs((pub_i - pub_j).total_seconds()) > 3600:
                continue
            f_vec = _tfidf_vector(_tokens(f["headline"], ticker=f["ticker"]), art_idf)
            if _cosine_distance(e_vec, f_vec) < 0.2:
                absorbed.add(f["id"])
                e["related_event_ids"].append(f["id"])
        final_events.append(e)

    top_events = final_events[:top_n]

    for _e in top_events:
        pr = percentile_rank(_e["impact_score"], score_distribution)
        _e["percentile_rank"] = pr
        _e["rank_label"] = format_rank_label(pr)
        if pr is None:
            _e["rank_reason"] = "insufficient_universe"
        _e["score_universe_n"] = score_universe_n

    def _band(s: float) -> str:
        if s > 0.2:  return "positive"
        if s < -0.2: return "negative"
        return "neutral"

    sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0}
    novelty_counts = {"high": 0, "medium": 0, "low": 0}
    divergence_count = 0
    for e in top_events:
        sentiment_counts[_band(e["sentiment_score"])] += 1
        novelty_counts[e["novelty_band"]] += 1
        if e["divergence_flag"] != "none":
            divergence_count += 1
    tickers_in_top = sorted({e["ticker"] for e in top_events})

    top_movers = sorted(
        (e for e in top_events if e["reaction_pct_since_publish"] is not None),
        key=lambda e: abs(e["reaction_pct_since_publish"]),
        reverse=True,
    )[:2]
    if top_movers:
        parts = []
        for e in top_movers:
            sign = "+" if e["reaction_pct_since_publish"] >= 0 else ""
            hdr = e['headline'][:50] + ('…' if len(e['headline']) > 50 else '')
            parts.append(
                f"{e['ticker']} {sign}{e['reaction_pct_since_publish']*100:.1f}% on "
                f'{e["source"]}\'s "{hdr}"'
            )
        take = "Window's biggest moves: " + "; ".join(parts) + "."
    else:
        take = "No material reactions in window."

    tier_caveats = (
        []
        if tier == "A"
        else [
            "Benzinga insights[] unavailable or forced-off; sentiment from keyword scorer.",
            "Sentiment is article-level, not sentence-level; sarcasm and negation are not handled.",
        ]
    )
    if pagination_capped_tickers:
        capped_names = ", ".join(c["ticker"] for c in pagination_capped_tickers)
        tier_caveats.append(
            f"Pagination cap hit on {len(pagination_capped_tickers)} tickers ({capped_names}). "
            f"Up to {max_pages_per_ticker} × 50 = {max_articles_per_ticker} articles fetched per ticker; "
            f"older articles in the window were not retrieved."
        )

    return {
        "skill": "news-scanner",
        "tier": tier,
        "tier_caveats": tier_caveats,
        "mode": "stream",
        "run_at": now_utc.isoformat(),
        "scan_params": {
            "watchlist": tickers,
            "window_hours": hours,
            "top_n": top_n,
            "min_reaction_pct": MIN_REACTION_FOR_DIVERGENCE,
            "reaction_minutes": REACTION_MINUTES_DEFAULT,
            "sentiment_mode": sentiment_mode,
        },
        "events": top_events,
        "summary": {
            "count": len(top_events),
            "tickers_with_events": len(tickers_in_top),
            "by_sentiment": sentiment_counts,
            "by_novelty": novelty_counts,
            "divergence_count": divergence_count,
            "skipped_no_bar_count": skipped_no_bar_count,
            "insufficient_baseline_count": insufficient_baseline_count,
        },
        "take": take,
        "skipped_tickers": skipped,
        "skipped_events": skipped_events,
        "sources": [
            {
                "endpoint": "https://api.polygon.io/v2/reference/news",
                "fetched_at": sources.news_last_fetched_at,
                "context": f"Benzinga News, per-ticker, last {effective_news_window_hours}h fetch (>=7d for novelty corpus)",
            },
            {
                "endpoint": "https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/{from}/{to}",
                "fetched_at": sources.aggs_last_fetched_at,
                "context": "5-minute aggregates for reaction window and volume anomaly baseline",
            },
        ],
        "pagination_status": {
            "max_pages_per_ticker": max_pages_per_ticker,
            "max_articles_per_ticker": max_articles_per_ticker,
            "capped_tickers": pagination_capped_tickers,
        },
    }


# ----- Renderer -----

def _truncate_headline(h: str, n: int = 90) -> str:
    if not h:
        return ""
    return h if len(h) <= n else h[: n - 1] + "…"


def _render_block(e: dict) -> str:
    ticker = e["ticker"].ljust(4)
    line1 = f"{ticker}  {e['published_at_et']}  {e['source']}"
    line2 = f"HEADLINE: {_truncate_headline(e['headline'])}"

    parts = [f"SENTIMENT: {e['sentiment_score']:+.2f}", f"NOVELTY: {e['novelty_band']}"]
    if e["reaction_pct_since_publish"] is None:
        parts.append("REACTION: pending overnight")
    else:
        parts.append(f"REACTION: {e['reaction_pct_since_publish']*100:+.1f}% ({e['reaction_window_label']})")
    if e["volume_anomaly_x"] is None:
        parts.append("baseline vol n/a")
    else:
        parts.append(f"{e['volume_anomaly_x']:.1f}x baseline vol")
    pr = e.get("percentile_rank")
    universe_n = e.get("score_universe_n") or 0
    if pr is not None:
        parts.append(f"IMPACT: {e['rank_label']} ({pr:.0f}th %ile, n={universe_n})")
    line3 = " · ".join(parts)

    block = [line1, line2, line3]
    if e["context_line"]:
        block.append(f"↳ {e['context_line']}")
    return "\n".join(block)


def render(payload: dict) -> str:
    top_events = payload["events"]
    hours = payload["scan_params"]["window_hours"]
    tier = payload["tier"]
    tickers_in_top = sorted({e["ticker"] for e in top_events})

    now_str = payload["run_at"][:16].replace("T", " ")
    lines: list[str] = []
    header = (
        f"{len(top_events)} events surfaced from {len(tickers_in_top)} tickers · "
        f"window: last {hours}h · run {now_str} UTC"
    )
    lines.append(header)
    if tier == "B":
        lines.append(
            "Note: keyword sentiment scorer in use (Benzinga insights not available). "
            "Reaction window: 5-min aggs."
        )
    lines.append("")

    for e in top_events:
        lines.append(_render_block(e))
        lines.append("")

    skipped_names = [s["ticker"] for s in payload["skipped_tickers"]]
    footer = f"End of stream. {len(top_events)} events across {len(tickers_in_top)} tickers."
    if skipped_names:
        footer += f" {len(skipped_names)} tickers skipped: {', '.join(skipped_names)}."
    summary = payload["summary"]
    if summary["skipped_no_bar_count"] or summary["insufficient_baseline_count"]:
        footer += (
            f" Reaction skips: {summary['skipped_no_bar_count']} no_bar_after_publish, "
            f"{summary['insufficient_baseline_count']} insufficient_baseline."
        )
    lines.append(footer)
    return "\n".join(lines)
