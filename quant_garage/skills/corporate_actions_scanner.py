"""
corporate-actions-scanner as an importable library function.

For a ticker or watchlist, surfaces material corporate actions filed as
8-K events over a lookback window. Cross-references SEC EDGAR 8-K items
with Massive news to explain each action, then measures the price
reaction. Complements news-scanner (which catches everything) and
earnings-drilldown (which handles Item 2.02) by giving offerings,
splits, spin-offs, buybacks, and M&A their own dedicated surface.

Motivated by the 2026-07-02 portfolio review that missed an ALLO
public offering (87.5M shares at $2.00, 34% dilution) because
news-scanner defaulted to a 24-hour window and earnings-blackout only
covers forward earnings.

    from quant_garage.skills.corporate_actions_scanner import run, render
    payload = run("ALLO", lookback_days=180)
    payload = run(["NVDA","AAPL","ALLO"], lookback_days=90)
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from .. import (
    MassiveClient,
    FetchError,
    today,
    utc_to_et,
    utcnow_iso,
)


SEC_HEADERS = {"User-Agent": "Rob Gourley rgourley@gmail.com"}


# 8-K item taxonomy. Each item maps to a bucket the tool surfaces, plus
# a materiality tag. Items not in this map are treated as non-material
# by default. Item 2.02 is deliberately excluded (earnings-drilldown
# handles it). Item 5.07 (vote results) and 5.08 (shareholder nomination)
# are marked non-material for the default surface.
ITEM_TAXONOMY: dict[str, tuple[str, bool]] = {
    "1.01": ("material_agreement", True),   # M&A, financing, strategic deals
    "1.02": ("agreement_terminated", True),
    "1.03": ("bankruptcy", True),
    "2.01": ("acquisition_disposition", True),
    "2.03": ("new_debt_or_obligation", True),
    "2.04": ("acceleration_or_default", True),
    "2.05": ("costs_of_exit", True),
    "2.06": ("material_impairment", True),
    "3.01": ("delisting_or_listing_failure", True),
    "3.02": ("unregistered_equity_sale", True),  # private placement
    "3.03": ("security_holder_rights_modified", True),
    "4.01": ("auditor_change", True),
    "4.02": ("restatement", True),  # non-reliance on prior financials
    "5.01": ("control_change", True),  # M&A close
    "5.02": ("officer_or_director_change", False),  # CEO/CFO are material but 5.02 is noisy
    "5.03": ("charter_bylaws_amendment", True),  # name change, share structure
    "5.05": ("ethics_code_change", False),
    "7.01": ("reg_fd_disclosure", True),
    "8.01": ("other_material_event", True),  # offerings, product news, litigation
}


# Newsroom keywords that reliably identify the flavor of an action.
# Matched against the news title + description when we cross-reference
# 8-Ks with Massive news articles filed on the same day.
FLAVOR_KEYWORDS: dict[str, list[str]] = {
    "public_offering": [
        "prices offering", "pricing of public offering",
        "public offering of common stock", "announces offering",
        "underwritten public offering", "follow-on offering",
    ],
    "atm_offering": [
        "at-the-market", "at the market offering", "atm offering",
        "sales agreement",
    ],
    "private_placement": [
        "private placement", "unregistered", "pipe transaction",
    ],
    "share_repurchase": [
        "repurchase program", "buyback", "share repurchase",
        "authorized to repurchase", "repurchase authorization",
    ],
    "special_dividend": [
        "special dividend", "special cash distribution",
    ],
    "stock_split": [
        "stock split", "forward split", "reverse split",
    ],
    "spin_off": [
        "spin-off", "spinoff", "distribute shares of",
    ],
    "acquisition_announcement": [
        "definitive agreement to acquire", "agrees to acquire",
        "announces acquisition of", "to acquire",
    ],
    "acquisition_target": [
        "to be acquired by", "definitive agreement", "merger agreement",
    ],
    "restatement": [
        "non-reliance", "restated financial", "restatement",
    ],
}


def _fetch_sec(url: str) -> dict:
    req = urllib.request.Request(url, headers=SEC_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _classify_items(items_str: str) -> list[str]:
    """Parse the comma-separated items field on a filing and return
    the bucket labels for items in ITEM_TAXONOMY."""
    if not items_str:
        return []
    out: list[str] = []
    for raw in items_str.split(","):
        item = raw.strip()
        # SEC sometimes prefixes with "Item " — strip
        if item.lower().startswith("item "):
            item = item[5:].strip()
        if item in ITEM_TAXONOMY:
            out.append(item)
    return out


def _detect_flavor(text: str) -> str | None:
    """Match the FLAVOR_KEYWORDS against a news title/description blob.
    Returns the flavor key with the most keyword hits, or None."""
    if not text:
        return None
    lo = text.lower()
    best_flavor = None
    best_hits = 0
    for flavor, keywords in FLAVOR_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in lo)
        if hits > best_hits:
            best_hits = hits
            best_flavor = flavor
    return best_flavor


def _fetch_ticker_meta(client: MassiveClient, ticker: str) -> dict | None:
    try:
        body, _ = client.get(f"/v3/reference/tickers/{ticker}")
    except FetchError:
        return None
    return body.get("results")


def _fetch_8ks(cik_padded: str, lookback_start: date) -> list[dict]:
    """Pull recent 8-K filings for a CIK, filter to lookback window."""
    doc = _fetch_sec(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
    recent = doc.get("filings", {}).get("recent", {})
    n = len(recent.get("form", []))
    if n == 0:
        return []
    out: list[dict] = []
    for i in range(n):
        if recent["form"][i] != "8-K":
            continue
        filing_date_str = recent["filingDate"][i]
        try:
            filing_date = date.fromisoformat(filing_date_str)
        except ValueError:
            continue
        if filing_date < lookback_start:
            continue
        items = recent.get("items", [""] * n)[i]
        buckets = _classify_items(items)
        if not buckets:
            continue
        out.append({
            "filing_date": filing_date_str,
            "acceptance_dt": recent.get("acceptanceDateTime", [""] * n)[i],
            "accession": recent["accessionNumber"][i],
            "items": [b for b in items.split(",") if b.strip()],
            "buckets": buckets,
            "primary_doc_description": recent.get(
                "primaryDocDescription", [""] * n)[i],
        })
    out.sort(key=lambda x: x["filing_date"])
    return out


def _fetch_news_window(
    client: MassiveClient, ticker: str, lookback_start: date,
) -> list[dict]:
    """Pull Massive news for a ticker, filter to lookback window.
    Returns most-recent first."""
    published_gte = f"{lookback_start.isoformat()}T00:00:00Z"
    articles: list[dict] = []
    try:
        page_gen = client.paginate(
            "/v2/reference/news",
            {"ticker": ticker, "published_utc.gte": published_gte,
             "limit": 100, "order": "desc"},
        )
        for page, _ in page_gen:
            articles.extend(page)
            if len(articles) >= 500:  # hard cap per ticker
                break
    except FetchError:
        return articles
    return articles


# Bucket -> expected flavors. When a filing's items map to any of
# these buckets, articles whose detected flavor is in the corresponding
# set are prioritized. Missing buckets fall back to "any flavor".
BUCKET_EXPECTED_FLAVORS: dict[str, set[str]] = {
    "material_agreement":
        {"acquisition_announcement", "share_repurchase", "special_dividend"},
    "acquisition_disposition":
        {"acquisition_announcement", "acquisition_target"},
    "control_change":
        {"acquisition_target", "acquisition_announcement"},
    "unregistered_equity_sale":
        {"private_placement", "public_offering"},
    "new_debt_or_obligation":
        {"private_placement"},
    "charter_bylaws_amendment":
        {"stock_split", "spin_off"},
    "restatement":
        {"restatement"},
    "other_material_event":
        set(),  # anything goes; 8.01 is the catch-all
    "reg_fd_disclosure":
        set(),  # 7.01 is the catch-all
}


def _match_news_to_filing(
    articles: list[dict], filing_date: str,
    filing_buckets: list[str] | None = None,
) -> dict | None:
    """Find the news article that best matches an 8-K filing.

    Ranking (best first):
      1. Article's detected flavor is one of the expected flavors for
         the filing's item buckets (huge boost).
      2. Article has at least one flavor keyword hit (a hint of
         relevance beyond a generic wire piece).
      3. Closer publication date (0-day gap ranks before +/- 1, etc.).
      4. More flavor keyword hits overall (tiebreak).

    Articles with zero flavor hits AND published on a different day
    are dropped: linking a generic same-week wire story to a specific
    material 8-K is worse than reporting no match. Same-day + zero
    hits are still allowed so we surface the filing itself when the
    only news that day is generic market coverage.
    """
    try:
        target = date.fromisoformat(filing_date)
    except ValueError:
        return None

    expected_flavors: set[str] = set()
    for bucket in filing_buckets or []:
        expected_flavors |= BUCKET_EXPECTED_FLAVORS.get(bucket, set())
    # Empty expected_flavors means "no specific expectation" — every
    # candidate scores 0 on axis 1, and the tiebreakers decide.

    candidates: list[tuple[int, int, int, int, dict]] = []
    for a in articles:
        pub_str = a.get("published_utc") or ""
        try:
            pub_date = date.fromisoformat(pub_str[:10])
        except ValueError:
            continue
        gap = abs((pub_date - target).days)
        if gap > 2:
            continue
        text = f"{a.get('title', '')} {a.get('description', '')}"
        detected_flavor = _detect_flavor(text)
        hits = sum(
            1 for kws in FLAVOR_KEYWORDS.values()
            for kw in kws if kw in text.lower()
        )
        # Drop off-day articles with zero flavor signal.
        if gap > 0 and hits == 0:
            continue
        flavor_match = (
            1 if detected_flavor and detected_flavor in expected_flavors
            else 0
        )
        has_any_flavor = 1 if hits > 0 else 0
        # Sort key: prefer flavor match (desc), then any-flavor (desc),
        # then closer date (asc), then more hits (desc).
        candidates.append(
            (-flavor_match, -has_any_flavor, gap, -hits, a)
        )
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return candidates[0][4]


def _fetch_daily_aggs(
    client: MassiveClient, ticker: str, from_date: date, to_date: date,
) -> list[dict]:
    """Pull daily bars for the ticker over the range. Returns the raw
    results list (each with t, o, h, l, c, v)."""
    try:
        body, _ = client.get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/"
            f"{from_date.isoformat()}/{to_date.isoformat()}",
            {"adjusted": "true", "sort": "asc", "limit": 500},
        )
    except FetchError:
        return []
    return body.get("results") or []


def _reaction_pct(
    bars: list[dict], event_date: date, t_plus: int,
) -> float | None:
    """Compute close-to-close percentage change from the last close on
    or before event_date to the close t_plus trading days later.
    Returns None if not enough bars in the window."""
    if not bars:
        return None
    # Index bars by date (YYYY-MM-DD)
    day_index: dict[date, dict] = {}
    for b in bars:
        d = datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc).date()
        day_index[d] = b
    sorted_dates = sorted(day_index.keys())
    # Find the last trading day <= event_date
    base_date = None
    for d in reversed(sorted_dates):
        if d <= event_date:
            base_date = d
            break
    if base_date is None:
        return None
    # Find the trading day t_plus positions after base
    try:
        base_idx = sorted_dates.index(base_date)
    except ValueError:
        return None
    target_idx = base_idx + t_plus
    if target_idx >= len(sorted_dates):
        return None
    base_close = day_index[base_date]["c"]
    target_close = day_index[sorted_dates[target_idx]]["c"]
    if base_close == 0:
        return None
    return (target_close - base_close) / base_close


def run(
    watchlist: Iterable[str] | str,
    lookback_days: int = 180,
    material_only: bool = True,
    top_n: int = 30,
    client: MassiveClient | None = None,
) -> dict:
    """Scan a watchlist for material corporate actions filed as 8-Ks.

    Args:
        watchlist: single ticker string, comma-separated string, or iterable.
        lookback_days: how far back to scan. Default 180.
        material_only: if True, drop 8-Ks whose items are all non-material
            per ITEM_TAXONOMY. Default True.
        top_n: max events to surface, ranked by |T+5 abnormal reaction|
            (falls back to raw T+5, then T+1, when abnormal is unavailable).
            Default 30.
        client: reuse an existing MassiveClient.
    """
    if isinstance(watchlist, str):
        tickers = [t.strip().upper() for t in watchlist.split(",") if t.strip()]
    else:
        tickers = [t.strip().upper() for t in watchlist if t and t.strip()]
    if not tickers:
        raise ValueError("watchlist must contain at least one ticker")
    if lookback_days < 1:
        raise ValueError("lookback_days must be >= 1")

    client = client or MassiveClient()
    today_d = today()
    lookback_start = today_d - timedelta(days=lookback_days)

    # SPY bars over the full lookback window are needed for market-
    # adjusted reactions. One call for the whole run instead of per-
    # filing. Padded to today so the T+5 buffer is covered.
    spy_bars = _fetch_daily_aggs(
        client, "SPY", lookback_start - timedelta(days=10), today_d,
    )

    events: list[dict] = []
    skipped: list[dict] = []

    for t in tickers:
        print(f"Scanning {t}...", file=sys.stderr)
        meta = _fetch_ticker_meta(client, t)
        if not meta:
            skipped.append({"ticker": t, "reason": "ticker metadata unavailable"})
            continue
        cik_raw = meta.get("cik")
        if not cik_raw:
            skipped.append({"ticker": t, "reason": "no CIK in ticker metadata"})
            continue
        cik_padded = str(cik_raw).zfill(10)

        try:
            filings = _fetch_8ks(cik_padded, lookback_start)
        except Exception as exc:
            skipped.append({"ticker": t, "reason": f"EDGAR fetch failed: {exc}"})
            continue

        if material_only:
            filings = [
                f for f in filings
                if any(ITEM_TAXONOMY[b][1] for b in f["buckets"] if b in ITEM_TAXONOMY)
            ]

        if not filings:
            skipped.append({
                "ticker": t,
                "reason": f"no material 8-K filings in last {lookback_days}d",
            })
            continue

        # Fetch news window once per ticker
        articles = _fetch_news_window(client, t, lookback_start)

        # Fetch daily bars covering all filings + T+5 buffer
        first_filing = date.fromisoformat(filings[0]["filing_date"])
        bars_from = first_filing - timedelta(days=10)
        bars_to = min(today_d, date.fromisoformat(filings[-1]["filing_date"])
                      + timedelta(days=14))
        bars = _fetch_daily_aggs(client, t, bars_from, bars_to)

        for f in filings:
            fdate = date.fromisoformat(f["filing_date"])
            # Pass the filing's item buckets so the matcher can prefer
            # articles with a flavor consistent with the filing type.
            filing_bucket_labels = [
                ITEM_TAXONOMY[b][0] for b in f["buckets"]
                if b in ITEM_TAXONOMY
            ]
            news = _match_news_to_filing(
                articles, f["filing_date"], filing_bucket_labels,
            )
            flavor = None
            headline = None
            source = None
            if news:
                text = f"{news.get('title', '')} {news.get('description', '')}"
                flavor = _detect_flavor(text)
                headline = news.get("title")
                source = (news.get("publisher") or {}).get("name")
            reaction_t1 = _reaction_pct(bars, fdate, 1)
            reaction_t5 = _reaction_pct(bars, fdate, 5)
            spy_t1 = _reaction_pct(spy_bars, fdate, 1) if spy_bars else None
            spy_t5 = _reaction_pct(spy_bars, fdate, 5) if spy_bars else None
            abnormal_t1 = (reaction_t1 - spy_t1
                            if reaction_t1 is not None and spy_t1 is not None
                            else None)
            abnormal_t5 = (reaction_t5 - spy_t5
                            if reaction_t5 is not None and spy_t5 is not None
                            else None)
            # Bucket labels (map to English)
            bucket_labels = [
                ITEM_TAXONOMY[b][0] for b in f["buckets"] if b in ITEM_TAXONOMY
            ]
            events.append({
                "ticker": t,
                "filing_date": f["filing_date"],
                "acceptance_dt": f["acceptance_dt"],
                "accession": f["accession"],
                "items_raw": f["items"],
                "buckets": bucket_labels,
                "flavor": flavor,
                "headline": headline,
                "news_source": source,
                "news_url": (news or {}).get("article_url"),
                "reaction_t1_pct": (round(reaction_t1 * 100, 2)
                                     if reaction_t1 is not None else None),
                "reaction_t5_pct": (round(reaction_t5 * 100, 2)
                                     if reaction_t5 is not None else None),
                "spy_t1_pct": (round(spy_t1 * 100, 2)
                                if spy_t1 is not None else None),
                "spy_t5_pct": (round(spy_t5 * 100, 2)
                                if spy_t5 is not None else None),
                "abnormal_t1_pct": (round(abnormal_t1 * 100, 2)
                                     if abnormal_t1 is not None else None),
                "abnormal_t5_pct": (round(abnormal_t5 * 100, 2)
                                     if abnormal_t5 is not None else None),
                "primary_doc_description": f["primary_doc_description"],
            })

    # Dedupe: SEC occasionally files multiple 8-Ks on the same day with the
    # same item set (e.g. announcement + closing of the same offering). Keep
    # one per (ticker, filing_date, sorted-buckets) tuple, preferring the
    # one with a headline match, then the one with more items.
    seen: dict[tuple, dict] = {}
    for e in events:
        key = (e["ticker"], e["filing_date"], tuple(sorted(e["buckets"])))
        if key not in seen:
            seen[key] = e
            continue
        incumbent = seen[key]
        challenger = e
        # Prefer entry with a headline match, then more raw items.
        inc_has_headline = bool(incumbent.get("headline"))
        ch_has_headline = bool(challenger.get("headline"))
        if ch_has_headline and not inc_has_headline:
            seen[key] = challenger
        elif ch_has_headline == inc_has_headline:
            if len(challenger["items_raw"]) > len(incumbent["items_raw"]):
                seen[key] = challenger
    events = list(seen.values())

    # Rank by absolute T+5 abnormal (SPY-adjusted) reaction. Falls back
    # to raw T+5, then T+1, when the abnormal legs are unavailable.
    def _rank_key(e):
        for k in ("abnormal_t5_pct", "reaction_t5_pct",
                  "abnormal_t1_pct", "reaction_t1_pct"):
            v = e.get(k)
            if v is not None:
                return -abs(v)
        return 0

    events.sort(key=_rank_key)
    events = events[:top_n]

    return {
        "scan_params": {
            "tickers": tickers,
            "lookback_days": lookback_days,
            "material_only": material_only,
            "top_n": top_n,
            "as_of": today_d.isoformat(),
        },
        "events": events,
        "skipped_tickers": skipped,
        "n_events": len(events),
        "n_tickers_with_events": len({e["ticker"] for e in events}),
        "generated_at": utcnow_iso(),
    }


def _format_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.1f}%"


def _humanize_bucket(bucket: str) -> str:
    return bucket.replace("_", " ")


def _humanize_flavor(flavor: str | None) -> str:
    if not flavor:
        return ""
    return flavor.replace("_", " ")


def render(payload: dict) -> str:
    params = payload["scan_params"]
    events = payload.get("events") or []
    skipped = payload.get("skipped_tickers") or []
    lines: list[str] = []

    header = (f"Corporate Actions Scanner — {params['as_of']}\n"
              f"Tickers: {', '.join(params['tickers'])} · "
              f"Lookback {params['lookback_days']}d · "
              f"{'material-only' if params['material_only'] else 'all-items'}")
    lines.append(header)
    lines.append("")

    if not events:
        lines.append("No material corporate actions surfaced in the window.")
        if skipped:
            lines.append("")
            lines.append("Skipped tickers:")
            for s in skipped:
                lines.append(f"  {s['ticker']}: {s['reason']}")
        return "\n".join(lines)

    lines.append(
        f"{payload['n_events']} events across "
        f"{payload['n_tickers_with_events']} tickers · ranked by |T+5 abn|"
    )
    lines.append("")

    for e in events:
        buckets_str = ", ".join(_humanize_bucket(b) for b in e["buckets"])
        flavor_str = _humanize_flavor(e.get("flavor"))
        header_line = (
            f"{e['ticker']}  {e['filing_date']}  "
            f"8-K item{'s' if len(e['items_raw']) > 1 else ''} "
            f"{', '.join(e['items_raw'])}  ·  {buckets_str}"
        )
        if flavor_str:
            header_line += f"  ·  flavor: {flavor_str}"
        lines.append(header_line)
        if e.get("headline"):
            src = e.get("news_source") or "?"
            lines.append(f"  HEADLINE ({src}): {e['headline'][:140]}")
        r1 = _format_pct(e.get("reaction_t1_pct"))
        r5 = _format_pct(e.get("reaction_t5_pct"))
        ab1 = e.get("abnormal_t1_pct")
        ab5 = e.get("abnormal_t5_pct")
        if ab1 is not None or ab5 is not None:
            ab1_str = _format_pct(ab1)
            ab5_str = _format_pct(ab5)
            lines.append(
                f"  REACTION: T+1 {r1} (abn {ab1_str})  ·  T+5 {r5} (abn {ab5_str})"
            )
        else:
            lines.append(f"  REACTION: T+1 {r1}  ·  T+5 {r5}")
        if e.get("news_url"):
            lines.append(f"  ↳ {e['news_url']}")
        lines.append("")

    if skipped:
        lines.append(
            f"Skipped ({len(skipped)}): "
            + ", ".join(f"{s['ticker']} ({s['reason']})" for s in skipped)
        )

    lines.append("")
    lines.append(
        "Caveats: 8-K item classification is deterministic; flavor is "
        "keyword-matched against Massive news within +/- 2 days of the "
        "filing (may miss if news post-dated by more than 2 days). "
        "Reactions are close-to-close; abn columns subtract same-window "
        "SPY return so the signal is name-specific. Some material "
        "actions (private placements, small M&A) surface with a lag."
    )
    return "\n".join(lines)
