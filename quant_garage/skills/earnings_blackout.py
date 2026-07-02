"""
earnings-blackout as an importable library function.

Watchlist scanner. Returns each ticker's earnings status
(blackout_imminent, blackout_soon, blackout_extended, just_printed,
recent_print, clear, unresolved) plus next/most-recent earnings date and
consensus EPS where available.

Resolver:
  Tier A: /benzinga/v1/earnings   (consensus EPS + surprise %, past + future)
  Tier B: SEC EDGAR 8-K items 2.02 / 7.01 / 8.01  (date-only, past-only)
CIK lookup: Massive primary → SEC ticker.txt fallback.

Callers:
    from quant_garage.skills.earnings_blackout import run, render
    payload = run(["NVDA", "AAPL"], window_days=7, include_past_days=3)
    print(render(payload))
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .. import MassiveClient, today, utcnow_iso
from ..timezones import utc_to_et


# SEC EDGAR is NOT a Massive endpoint. Personal User-Agent per SEC fair-use policy.
SEC_HEADERS = {"User-Agent": "Rob Gourley rgourley@gmail.com"}
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

# 8-K items accepted as earnings-class signals. 2.02 is the standard
# "Results of Operations and Financial Condition" item. 7.01 (Reg FD)
# and 8.01 (Other Events) are softer signals biotechs sometimes use.
EDGAR_EARNINGS_ITEMS = ("2.02", "7.01", "8.01")
EDGAR_STRONG_ITEMS = ("2.02",)


STATUS_BLACKOUT_IMMINENT = "blackout_imminent"
STATUS_BLACKOUT_SOON = "blackout_soon"
STATUS_BLACKOUT_EXTENDED = "blackout_extended"
STATUS_JUST_PRINTED = "just_printed"
STATUS_RECENT_PRINT = "recent_print"
STATUS_CLEAR = "clear"
STATUS_UNRESOLVED = "unresolved"


# Module-level caches — safe to reuse across calls in the same process.
_benzinga_cache: dict[str, list[dict[str, Any]]] = {}
_edgar_cache: dict[str, list[dict[str, Any]]] = {}
_cik_cache: dict[str, str | None] = {}
_SEC_TICKER_MAP_CACHE: dict[str, str] | None = None


class _Sources:
    """Per-run provenance recorder. Not a global — avoids cross-call bleeding."""
    def __init__(self) -> None:
        self._items: list[dict[str, str]] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict[str, str]]:
        return list(self._items)


# -------- HTTP helpers --------

def _fetch_sec(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=SEC_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _paginate(client: MassiveClient, path: str, params: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], str]:
    out: list[dict[str, Any]] = []
    last_fetched = utcnow_iso()
    for page, fetched_at in client.paginate(path, params):
        out.extend(page)
        last_fetched = fetched_at
    return out, last_fetched


def _fetch_sec_ticker_map(sources: _Sources) -> dict[str, str]:
    global _SEC_TICKER_MAP_CACHE
    if _SEC_TICKER_MAP_CACHE is not None:
        return _SEC_TICKER_MAP_CACHE
    try:
        doc = _fetch_sec(SEC_TICKER_MAP_URL)
        sources.record(SEC_TICKER_MAP_URL, utcnow_iso(),
                       "SEC canonical ticker->CIK map (fallback for Massive)")
    except Exception:
        _SEC_TICKER_MAP_CACHE = {}
        return _SEC_TICKER_MAP_CACHE
    out: dict[str, str] = {}
    for _, row in doc.items():
        t = str(row.get("ticker", "")).upper()
        cik = row.get("cik_str")
        if t and cik is not None:
            out[t] = str(cik).zfill(10)
    _SEC_TICKER_MAP_CACHE = out
    return out


def _get_cik(client: MassiveClient, ticker: str, sources: _Sources) -> str | None:
    if ticker in _cik_cache:
        return _cik_cache[ticker]
    try:
        body, fetched_at = client.get(f"/v3/reference/tickers/{ticker}")
        sources.record(f"/v3/reference/tickers/{ticker}", fetched_at,
                       f"Ticker metadata for {ticker} (CIK lookup)")
    except Exception:
        body = {}
    results = (body.get("results") or {}) if isinstance(body, dict) else {}
    cik_raw = results.get("cik") if isinstance(results, dict) else None
    if cik_raw:
        padded = str(cik_raw).zfill(10)
        _cik_cache[ticker] = padded
        return padded
    sec_map = _fetch_sec_ticker_map(sources)
    fallback = sec_map.get(ticker.upper())
    _cik_cache[ticker] = fallback
    return fallback


def _benzinga_earnings(
    client: MassiveClient, ticker: str, date_from: str, date_to: str, sources: _Sources,
) -> list[dict[str, Any]]:
    cache_key = f"{ticker}:{date_from}:{date_to}"
    if cache_key in _benzinga_cache:
        return _benzinga_cache[cache_key]
    try:
        rows, fetched_at = _paginate(client, "/benzinga/v1/earnings", {
            "ticker": ticker, "limit": 40, "order": "asc", "sort": "date",
            "date.gte": date_from, "date.lte": date_to,
        })
        sources.record("/benzinga/v1/earnings?ticker={ticker}", fetched_at,
                       f"Benzinga earnings for {ticker} ({date_from}..{date_to})")
    except Exception:
        rows = []
    _benzinga_cache[cache_key] = rows
    return rows


def _edgar_earnings(client: MassiveClient, ticker: str, sources: _Sources) -> list[dict[str, Any]]:
    if ticker in _edgar_cache:
        return _edgar_cache[ticker]
    cik_padded = _get_cik(client, ticker, sources)
    if not cik_padded:
        _edgar_cache[ticker] = []
        return []
    sec_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    try:
        doc = _fetch_sec(sec_url)
        sources.record(sec_url, utcnow_iso(),
                       f"SEC EDGAR submissions for {ticker} (8-K items "
                       + "/".join(EDGAR_EARNINGS_ITEMS) + ")")
    except Exception:
        _edgar_cache[ticker] = []
        return []
    recent = (doc.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    items_col = recent.get("items") or [""] * len(forms)
    acc_col = recent.get("acceptanceDateTime") or [""] * len(forms)
    company_name = doc.get("name")

    out: list[dict[str, Any]] = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        items_list = [it.strip() for it in (items_col[i] or "").split(",") if it.strip()]
        matched_item = next((c for c in items_list if c in EDGAR_EARNINGS_ITEMS), None)
        if matched_item is None:
            continue
        acc_dt = acc_col[i]
        if not acc_dt:
            continue
        utc_iso = acc_dt if acc_dt.endswith("Z") else acc_dt + "Z"
        iso = utc_iso[:-1] + "+00:00"
        dt_utc = datetime.fromisoformat(iso)
        dt_et = utc_to_et(dt_utc)
        et_date = dt_et.date().isoformat()
        minutes = dt_et.hour * 60 + dt_et.minute
        if minutes < 9 * 60 + 30:
            session = "BMO"
        elif minutes >= 16 * 60:
            session = "AMC"
        else:
            session = "DMH"
        signal_strength = "strong" if matched_item in EDGAR_STRONG_ITEMS else "soft"
        out.append({
            "date": et_date, "session": session, "company_name": company_name,
            "consensus_eps": None, "consensus_revenue": None,
            "item_code": matched_item, "signal_strength": signal_strength,
            "_source": "edgar_8k",
        })
    out.sort(key=lambda r: r["date"], reverse=True)
    _edgar_cache[ticker] = out
    return out


# -------- Classifier --------

def _classify_status(
    next_date_iso: str | None, past_date_iso: str | None,
    today_iso: str, window_days_forward: int, window_days_past: int,
) -> tuple[str, int | None]:
    today_d = date.fromisoformat(today_iso)
    if next_date_iso:
        nd = date.fromisoformat(next_date_iso)
        days_until = (nd - today_d).days
        if 0 <= days_until <= 3:
            return STATUS_BLACKOUT_IMMINENT, days_until
        if 4 <= days_until <= 7:
            return STATUS_BLACKOUT_SOON, days_until
        if days_until >= 8:
            return STATUS_BLACKOUT_EXTENDED, days_until
    if past_date_iso:
        pd_ = date.fromisoformat(past_date_iso)
        days_until = (pd_ - today_d).days
        if -3 <= days_until <= 0:
            return STATUS_JUST_PRINTED, days_until
        if -window_days_past <= days_until < -3:
            return STATUS_RECENT_PRINT, days_until
    return STATUS_CLEAR, None


def _session_from_benzinga(row: dict[str, Any]) -> str:
    t = row.get("time")
    if not t:
        return "unknown"
    try:
        hh, mm, _ = str(t).split(":")
        minutes = int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        return "unknown"
    if minutes < 9 * 60 + 30:
        return "BMO"
    if minutes >= 16 * 60:
        return "AMC"
    return "DMH"


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _resolve_ticker(
    client: MassiveClient, ticker: str, today_iso: str,
    window_days_forward: int, window_days_past: int, sources: _Sources,
) -> dict[str, Any]:
    today_d = date.fromisoformat(today_iso)
    date_from = (today_d - timedelta(days=window_days_past)).isoformat()
    date_to = (today_d + timedelta(days=window_days_forward)).isoformat()

    bz_rows = _benzinga_earnings(client, ticker, date_from, date_to, sources)
    if bz_rows:
        source = "benzinga"
        rows = []
        for r in bz_rows:
            d = r.get("date")
            if not d:
                continue
            rows.append({
                "date": d,
                "session": _session_from_benzinga(r),
                "consensus_eps": _to_float(r.get("eps_est")),
                "consensus_revenue": _to_float(r.get("revenue_est")),
                "item_code": None,
                "signal_strength": None,
            })
    else:
        source = "edgar_8k"
        edgar_rows = _edgar_earnings(client, ticker, sources)
        rows = [r for r in edgar_rows if date_from <= r["date"] <= today_iso]

    if not rows and not bz_rows:
        return {
            "ticker": ticker, "status": STATUS_UNRESOLVED,
            "next_earnings_date": None, "most_recent_earnings_date": None,
            "days_until": None, "expected_release_time": None,
            "consensus_eps": None, "consensus_revenue": None,
            "item_code": None, "signal_strength": None, "source": source,
        }

    next_row = None
    past_row = None
    for r in rows:
        d = r["date"]
        if d > today_iso:
            if next_row is None or d < next_row["date"]:
                next_row = r
        elif d <= today_iso:
            if past_row is None or d > past_row["date"]:
                past_row = r

    next_date = next_row["date"] if next_row else None
    past_date = past_row["date"] if past_row else None
    status, days_until = _classify_status(
        next_date, past_date, today_iso, window_days_forward, window_days_past,
    )

    primary = next_row if next_row else past_row
    return {
        "ticker": ticker,
        "status": status,
        "next_earnings_date": next_date,
        "most_recent_earnings_date": past_date,
        "days_until": days_until,
        "expected_release_time": primary.get("session") if primary else None,
        "consensus_eps": primary.get("consensus_eps") if primary else None,
        "consensus_revenue": primary.get("consensus_revenue") if primary else None,
        "item_code": primary.get("item_code") if primary else None,
        "signal_strength": primary.get("signal_strength") if primary else None,
        "source": source,
    }


def _detect_tier(results: list[dict[str, Any]]) -> tuple[str, list[str]]:
    sources_used = {r["source"] for r in results}
    benzinga_hits = any(r["source"] == "benzinga" and r["status"] != STATUS_UNRESOLVED
                        for r in results)
    if benzinga_hits:
        caveats: list[str] = []
        if "edgar_8k" in sources_used:
            caveats.append(
                "Mixed sources: some tickers resolved via Benzinga, others "
                "fell back to SEC EDGAR 8-K (date-only, no consensus EPS)."
            )
        return "A", caveats
    return "B", [
        "Benzinga not available on this key; all tickers resolved via SEC "
        "EDGAR 8-K items 2.02 / 7.01 / 8.01. Forward earnings calendar is "
        "unavailable (EDGAR is past-only), so future prints will surface "
        "as 'unresolved' until they file.",
    ]


# -------- Public API --------

def run(
    watchlist: Iterable[str] | str,
    window_days: int = 7,
    include_past_days: int = 3,
    client: MassiveClient | None = None,
) -> dict:
    """Scan `watchlist` for earnings within `window_days` forward /
    `include_past_days` back. Returns the canonical earnings-blackout payload.

    `watchlist` accepts a comma-separated string or any iterable of tickers.
    """
    if isinstance(watchlist, str):
        tickers = [t.strip().upper() for t in watchlist.split(",") if t.strip()]
    else:
        tickers = [t.strip().upper() for t in watchlist if t and t.strip()]
    if not tickers:
        raise ValueError("watchlist must contain at least one ticker")

    client = client or MassiveClient()
    sources = _Sources()
    today_iso = today().isoformat()

    results = [
        _resolve_ticker(client, t, today_iso, window_days, include_past_days, sources)
        for t in tickers
    ]
    tier, tier_caveats = _detect_tier(results)

    summary = {
        "n_blackout_imminent": sum(1 for r in results if r["status"] == STATUS_BLACKOUT_IMMINENT),
        "n_blackout_soon":     sum(1 for r in results if r["status"] == STATUS_BLACKOUT_SOON),
        "n_blackout_extended": sum(1 for r in results if r["status"] == STATUS_BLACKOUT_EXTENDED),
        "n_just_printed":      sum(1 for r in results if r["status"] == STATUS_JUST_PRINTED),
        "n_recent_print":      sum(1 for r in results if r["status"] == STATUS_RECENT_PRINT),
        "n_clear":             sum(1 for r in results if r["status"] == STATUS_CLEAR),
        "n_unresolved":        sum(1 for r in results if r["status"] == STATUS_UNRESOLVED),
    }

    return {
        "skill": "earnings-blackout",
        "as_of": today_iso,
        "fetched_at": utcnow_iso(),
        "window_days_forward": window_days,
        "window_days_past": include_past_days,
        "watchlist": tickers,
        "n_tickers": len(tickers),
        "tier": tier,
        "tier_caveats": tier_caveats,
        "results": results,
        "summary": summary,
        "sources": sources.to_list(),
    }


# -------- Renderer --------

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _weekday(d_iso: str) -> str:
    return WEEKDAYS[date.fromisoformat(d_iso).weekday()]


def _fmt_consensus(row: dict[str, Any]) -> str:
    parts: list[str] = []
    if row.get("consensus_eps") is not None:
        parts.append(f"consensus EPS ${row['consensus_eps']:.2f}")
    if row.get("consensus_revenue") is not None:
        rev = row["consensus_revenue"]
        if abs(rev) >= 1e9:
            parts.append(f"rev ${rev / 1e9:.1f}B")
        elif abs(rev) >= 1e6:
            parts.append(f"rev ${rev / 1e6:.0f}M")
        else:
            parts.append(f"rev ${rev:,.0f}")
    if not parts and row.get("signal_strength"):
        parts.append(f"signal_strength: {row['signal_strength']}")
    return ", ".join(parts)


def render(payload: dict) -> str:
    lines: list[str] = []
    lines.append(f"Earnings Blackout Scan — {payload['as_of']}")
    lines.append(
        f"Watchlist: {payload['n_tickers']} tickers · Forward window "
        f"{payload['window_days_forward']} days · Past window "
        f"{payload['window_days_past']} days"
    )
    lines.append("")

    by_status: dict[str, list[dict[str, Any]]] = {}
    for r in payload["results"]:
        by_status.setdefault(r["status"], []).append(r)

    def _bucket(title: str, status: str, render_detail: bool = True) -> None:
        rows = by_status.get(status, [])
        lines.append(title)
        if not rows:
            lines.append("  (none)")
        elif render_detail:
            rows_sorted = sorted(
                rows,
                key=lambda r: abs(r["days_until"]) if r["days_until"] is not None else 999,
            )
            for r in rows_sorted:
                d = r.get("next_earnings_date") or r.get("most_recent_earnings_date")
                wd = _weekday(d) if d else "???"
                sess = r.get("expected_release_time") or ""
                sess_str = f" {sess}" if sess and sess != "unknown" else ""
                du = r.get("days_until")
                du_str = ""
                if du is not None:
                    if du >= 0:
                        du_str = f"  ({du} day{'s' if du != 1 else ''})"
                    else:
                        du_str = f"  ({abs(du)} day{'s' if abs(du) != 1 else ''} ago)"
                consensus = _fmt_consensus(r)
                consensus_str = f"  {consensus}" if consensus else ""
                lines.append(f"  {r['ticker']:<6} {wd} {d}{sess_str}{du_str}{consensus_str}")
        else:
            tickers = ", ".join(sorted(r["ticker"] for r in rows))
            lines.append(f"  {tickers}")
        lines.append("")

    _bucket("BLACKOUT IMMINENT (0-3 days forward):", STATUS_BLACKOUT_IMMINENT)
    _bucket("BLACKOUT SOON (4-7 days forward):", STATUS_BLACKOUT_SOON)
    if payload["window_days_forward"] > 7:
        _bucket("BLACKOUT EXTENDED (8+ days forward):", STATUS_BLACKOUT_EXTENDED)
    _bucket("JUST PRINTED (within past 3 days):", STATUS_JUST_PRINTED)
    if payload["window_days_past"] > 3:
        _bucket("RECENT PRINT (4-7 days past):", STATUS_RECENT_PRINT)
    _bucket("CLEAR (no earnings in window):", STATUS_CLEAR, render_detail=False)

    unresolved = by_status.get(STATUS_UNRESOLVED, [])
    if unresolved:
        lines.append("UNRESOLVED:")
        for r in unresolved:
            lines.append(
                f"  {r['ticker']:<6} resolver returned no events "
                f"(source attempted: {r['source']})"
            )
        lines.append("")

    if payload.get("tier_caveats"):
        lines.append(f"Tier {payload['tier']} caveats")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")

    return "\n".join(lines).rstrip()
