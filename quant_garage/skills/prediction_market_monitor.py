"""
prediction-market-monitor as an importable library function.

Pulls Kalshi prediction market prices for Fed decisions, CPI, GDP,
NFP, and other macro / market events. Reports the implied
probability of each outcome, open interest, 24h volume, and a
consolidated cross-strike probability distribution when the market
is a laddered strike-type (like KXFED-27APR-T4.25, T4.00, T3.75...).

Motivated by 2025-26 growth of prediction markets as leading
indicators: Kalshi contracts on Fed decisions now clear high volume
and reflect a real market-implied policy path, often diverging
meaningfully from surveyed economist consensus.

    from quant_garage.skills.prediction_market_monitor import run, render
    payload = run(series="KXFED")

Kalshi's public read-only API does not require authentication. Uses
urllib directly instead of the MassiveClient. Rate-limits at 10 req/s
per Kalshi's documentation, so runs are throttled at ~1 req per 150ms.
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from typing import Iterable

from .. import today, utcnow_iso


KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_UA = "quant-garage/prediction-market-monitor"


# Curated series shortcuts for common financial market queries.
COMMON_SERIES: dict[str, str] = {
    "fed": "KXFED",                           # Fed funds upper-bound level after meeting
    "fed_decision": "KXFEDDECISION",         # Fed rate change (hike / hold / cut) at meeting
    "cpi": "KXCPI",                           # CPI month-over-month
    "core_cpi": "KXCORECPI",                  # Core CPI m/m
    "cpi_yoy": "KXCPIYOY",                    # CPI year-over-year
    "ppi": "KXPPI",                           # PPI m/m
    "nfp": "KXNFP",                           # Nonfarm payrolls
    "unemployment": "KXUNEMP",                # Unemployment rate
    "gdp": "KXGDP",                           # GDP
    "jobless_claims": "KXICSA",               # Initial jobless claims
    "recession": "KXRECESSIONYEAR",           # Recession year
    "spx_close": "KXSPX",                     # SPX close
    "btc_close": "KXBTCD",                    # BTC close daily
    "housing_starts": "KXHOUSTAR",            # Housing starts
    "retail_sales": "KXRETAIL",               # Retail sales m/m
    "ism_manufacturing": "KXISM",             # ISM manufacturing
    "consumer_confidence": "KXCONSCONF",      # Consumer confidence
}


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


def _http_get(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": KALSHI_UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Kalshi HTTP {e.code} on {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Kalshi network error on {url}: {e}") from e


def _fetch_markets(
    series: str | None,
    keyword: str | None,
    event_ticker: str | None,
    max_pages: int = 3,
    sources: _Sources | None = None,
) -> list[dict]:
    """Paginate through Kalshi markets with optional filters."""
    params: dict[str, str] = {
        "status": "open",
        "limit": "200",
    }
    if series:
        params["series_ticker"] = series
    if event_ticker:
        params["event_ticker"] = event_ticker

    rows: list[dict] = []
    cursor = ""
    for page in range(max_pages):
        if cursor:
            params["cursor"] = cursor
        qs = urllib.parse.urlencode(params)
        url = f"{KALSHI_BASE}/markets?{qs}"
        d = _http_get(url)
        if sources is not None:
            sources.record(url, utcnow_iso(), f"Kalshi markets page {page + 1}")
        page_rows = d.get("markets") or []
        rows.extend(page_rows)
        cursor = d.get("cursor") or ""
        if not cursor:
            break
        time.sleep(0.15)  # rate limit

    if keyword:
        lk = keyword.lower()
        rows = [
            r for r in rows
            if lk in (r.get("title") or "").lower()
            or lk in (r.get("ticker") or "").lower()
            or lk in (r.get("event_ticker") or "").lower()
        ]
    return rows


def _implied_probability(row: dict) -> float | None:
    """Midpoint of yes bid/ask as implied probability."""
    bid = row.get("yes_bid_dollars")
    ask = row.get("yes_ask_dollars")
    try:
        b = float(bid) if bid is not None else None
        a = float(ask) if ask is not None else None
    except (TypeError, ValueError):
        return None
    if b is not None and a is not None and 0 <= b <= 1 and 0 <= a <= 1:
        return (b + a) / 2.0
    if a is not None and 0 <= a <= 1:
        return a
    if b is not None and 0 <= b <= 1:
        return b
    last = row.get("last_price_dollars")
    try:
        lp = float(last) if last is not None else None
    except (TypeError, ValueError):
        return None
    if lp is not None and 0 <= lp <= 1:
        return lp
    return None


def _group_by_event(rows: list[dict]) -> dict[str, list[dict]]:
    """Group markets by their event_ticker so laddered strike sets can render together."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        evt = row.get("event_ticker") or "UNGROUPED"
        grouped.setdefault(evt, []).append(row)
    return grouped


def _implied_distribution(event_rows: list[dict]) -> dict:
    """
    For a laddered strike event (KXFED-27APR-T4.25, T4.00, T3.75...):
    - Sort by floor_strike
    - Use yes-mid at each threshold as P(rate > threshold)
    - Derive P(rate in bucket) by adjacent differences
    """
    with_strike = []
    for r in event_rows:
        f = r.get("floor_strike")
        try:
            strike = float(f) if f is not None else None
        except (TypeError, ValueError):
            strike = None
        p = _implied_probability(r)
        if strike is not None and p is not None:
            with_strike.append({"strike": strike, "p_above": p, "ticker": r.get("ticker")})
    if len(with_strike) < 2:
        return {"has_ladder": False, "buckets": []}
    with_strike.sort(key=lambda x: x["strike"])
    # P(above lowest strike) and P(above highest strike) bound the range.
    # Bucket probability = P(above strike[i]) - P(above strike[i+1]) walking up.
    buckets: list[dict] = []
    for i, entry in enumerate(with_strike):
        if i + 1 < len(with_strike):
            nxt = with_strike[i + 1]
            bucket_p = max(0.0, entry["p_above"] - nxt["p_above"])
            buckets.append({
                "lower_strike": entry["strike"],
                "upper_strike": nxt["strike"],
                "p_in_bucket": round(bucket_p, 4),
                "cumulative_p_above_lower": round(entry["p_above"], 4),
            })
        else:
            buckets.append({
                "lower_strike": entry["strike"],
                "upper_strike": None,
                "p_in_bucket": round(entry["p_above"], 4),
                "cumulative_p_above_lower": round(entry["p_above"], 4),
            })
    modal = max(buckets, key=lambda b: b["p_in_bucket"])
    # Expected value using bucket midpoints (upper strike = None means unbounded, treat as +0.25 from lower)
    ev = 0.0
    for b in buckets:
        if b["upper_strike"] is None:
            mid = b["lower_strike"] + 0.125
        else:
            mid = (b["lower_strike"] + b["upper_strike"]) / 2.0
        ev += mid * b["p_in_bucket"]
    return {
        "has_ladder": True,
        "buckets": buckets,
        "modal_bucket": modal,
        "expected_value": round(ev, 4),
        "n_strikes": len(with_strike),
    }


def run(
    series: str | None = None,
    keyword: str | None = None,
    event_ticker: str | None = None,
    max_events: int = 5,
) -> dict:
    """Query Kalshi for prediction markets and summarize.

    Args:
        series: Kalshi series ticker (e.g. KXFED, KXCPI, KXFEDDECISION).
            May also be a shortcut key from COMMON_SERIES.
        keyword: keyword filter applied client-side on market titles.
        event_ticker: pin a specific event (like KXFED-27APR).
        max_events: cap events shown in the note.
    """
    resolved_series = None
    if series:
        s_lower = series.strip().lower()
        resolved_series = COMMON_SERIES.get(s_lower, series.strip())

    if not resolved_series and not keyword and not event_ticker:
        raise ValueError("provide series, keyword, or event_ticker")

    sources = _Sources()

    rows = _fetch_markets(
        series=resolved_series,
        keyword=keyword,
        event_ticker=event_ticker,
        sources=sources,
    )

    events = _group_by_event(rows)

    per_event: list[dict] = []
    for event_ticker_key, event_rows in list(events.items())[:max_events]:
        event_rows_sorted = sorted(
            event_rows,
            key=lambda r: (r.get("floor_strike") or 0),
        )
        implied_dist = _implied_distribution(event_rows_sorted)
        markets_out = []
        for r in event_rows_sorted:
            markets_out.append({
                "ticker": r.get("ticker"),
                "title": r.get("title"),
                "yes_bid": r.get("yes_bid_dollars"),
                "yes_ask": r.get("yes_ask_dollars"),
                "last_price": r.get("last_price_dollars"),
                "implied_probability": _implied_probability(r),
                "floor_strike": r.get("floor_strike"),
                "close_time": r.get("close_time"),
                "volume_24h": r.get("volume_24h_fp"),
                "open_interest": r.get("open_interest_fp"),
                "notional_value": r.get("notional_value_dollars"),
            })
        first_row = event_rows_sorted[0] if event_rows_sorted else {}
        per_event.append({
            "event_ticker": event_ticker_key,
            "title": first_row.get("title"),
            "close_time": first_row.get("close_time"),
            "n_markets": len(event_rows),
            "markets": markets_out,
            "implied_distribution": implied_dist,
        })

    tier_caveats: list[str] = [
        "Kalshi markets are thinly traded on off-consensus outcomes; "
        "implied probability may reflect market-maker spread rather than "
        "genuine informed conviction. Check open_interest and 24h volume.",
        "Prediction market prices are risk-neutral, not physical probabilities. "
        "Risk-averse participants may bid up tail-outcome markets.",
    ]
    if not per_event:
        tier_caveats.append(
            f"No open markets for series={resolved_series or 'n/a'}, "
            f"keyword={keyword or 'n/a'}, event_ticker={event_ticker or 'n/a'}."
        )

    return {
        "skill": "prediction-market-monitor",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "source": "kalshi",
        "series_ticker": resolved_series,
        "keyword": keyword,
        "event_ticker_filter": event_ticker,
        "n_events": len(events),
        "n_events_reported": len(per_event),
        "n_markets_total": len(rows),
        "events": per_event,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _fmt_pct(p: float | None) -> str:
    if p is None:
        return "n/a"
    return f"{p * 100:.1f}%"


def _fmt_usd(x) -> str:
    if x is None:
        return "n/a"
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "n/a"
    if x >= 1000:
        return f"{x/1000:.1f}k"
    return f"{x:.0f}"


def _fmt_close(iso_str: str | None) -> str:
    if not iso_str:
        return "n/a"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%MZ")
    except ValueError:
        return iso_str[:16]


def render(payload: dict) -> str:
    lines: list[str] = []
    src = payload.get("source", "kalshi").capitalize()
    series = payload.get("series_ticker")
    kw = payload.get("keyword")
    evt = payload.get("event_ticker_filter")
    n_ev = payload["n_events"]
    n_m = payload["n_markets_total"]

    query_bits = []
    if series:
        query_bits.append(f"series={series}")
    if kw:
        query_bits.append(f"keyword='{kw}'")
    if evt:
        query_bits.append(f"event={evt}")

    lines.append(
        f"Prediction market monitor ({src}): {' · '.join(query_bits) or 'no filter'} · "
        f"{n_ev} event(s) · {n_m} markets"
    )
    lines.append("")

    if not payload["events"]:
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    for event in payload["events"]:
        title = event.get("title") or event["event_ticker"]
        close_t = _fmt_close(event.get("close_time"))
        lines.append(f"[{event['event_ticker']}] closes {close_t} · {event['n_markets']} strikes")
        lines.append(f"  {title[:110]}")
        dist = event.get("implied_distribution") or {}
        if dist.get("has_ladder"):
            modal = dist.get("modal_bucket") or {}
            ev = dist.get("expected_value")
            lo = modal.get("lower_strike")
            hi = modal.get("upper_strike")
            lo_str = f"{lo:.2f}" if lo is not None else "?"
            hi_str = f"{hi:.2f}" if hi is not None else "∞"
            lines.append(
                f"  Modal outcome: {lo_str}-{hi_str} @ P={_fmt_pct(modal.get('p_in_bucket'))}"
                + (f" · Expected: {ev:.3f}" if ev is not None else "")
            )
            lines.append("  Bucket distribution:")
            for b in dist["buckets"]:
                lo_str = f"{b['lower_strike']:.2f}"
                hi_str = f"{b['upper_strike']:.2f}" if b["upper_strike"] is not None else "∞  "
                bar_len = int(round(b["p_in_bucket"] * 40))
                bar = "█" * bar_len
                lines.append(
                    f"    {lo_str} - {hi_str}  {_fmt_pct(b['p_in_bucket']):>6}  {bar}"
                )
        else:
            lines.append("  Markets:")
            for m in event["markets"][:10]:
                p = m.get("implied_probability")
                lines.append(
                    f"    · {m['ticker']}  P={_fmt_pct(p)}  "
                    f"vol24h={_fmt_usd(m.get('volume_24h'))}  OI={_fmt_usd(m.get('open_interest'))}"
                )
        lines.append("")

    # Take
    take_parts: list[str] = []
    ladder_events = [e for e in payload["events"] if e.get("implied_distribution", {}).get("has_ladder")]
    if ladder_events:
        first = ladder_events[0]
        modal = first["implied_distribution"]["modal_bucket"]
        lo = modal.get("lower_strike")
        hi = modal.get("upper_strike")
        lo_str = f"{lo:.2f}" if lo is not None else "?"
        hi_str = f"{hi:.2f}" if hi is not None else "∞"
        ev = first["implied_distribution"].get("expected_value")
        take_parts.append(
            f"Modal implied outcome for {first['event_ticker']}: {lo_str}-{hi_str} "
            f"({_fmt_pct(modal.get('p_in_bucket'))})"
            + (f", expected {ev:.3f}" if ev is not None else "")
            + "."
        )
    else:
        take_parts.append(f"{n_ev} events surfaced; check per-market open interest for signal quality.")
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
