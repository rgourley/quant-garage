#!/usr/bin/env python3
"""
Reference implementation of the earnings-blackout skill.

Lightweight watchlist scanner. Takes a comma-separated list of tickers and
a forward window, returns each ticker's earnings status (blackout_imminent,
blackout_soon, blackout_extended, just_printed, recent_print, clear,
unresolved) plus the next/most-recent earnings date and consensus EPS where
available.

CLI:

  python3 examples/run-earnings-blackout.py \\
    --watchlist NVDA,TSLA,AMZN,GOOGL,META,AAPL,MSFT \\
    --window-days 7 \\
    --include-past-days 3 \\
    --format render

Reads MASSIVE_API_KEY from env. Writes JSON + rendered markdown to
examples/earnings-blackout-output.md.

The resolver mirrors the wave-13 pattern from run-event-study.py:
  Tier A: /benzinga/v1/earnings (consensus EPS / surprise %)
  Tier B: SEC EDGAR 8-K items 2.02 / 7.01 / 8.01 (date-only)
CIK lookup is Massive primary -> SEC ticker.txt fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Make `lib.quant_garage` importable when running this script from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    today,
    utcnow_iso,
    resolve_output_format,
    emit_to_stdout,
)
from lib.quant_garage.timezones import utc_to_et

# SEC EDGAR is NOT a Massive endpoint, so it stays on raw urllib with the
# personal User-Agent required by SEC's fair-use policy.
SEC_HEADERS = {"User-Agent": "Rob Gourley rgourley@gmail.com"}
TODAY = today()

client = MassiveClient()


# -------- HTTP helpers --------


def fetch_all(path: str, params: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], str]:
    """Collect every page from the Massive client and return (results, last_fetched_at)."""
    out: list[dict[str, Any]] = []
    last_fetched = utcnow_iso()
    for page, fetched_at in client.paginate(path, params):
        out.extend(page)
        last_fetched = fetched_at
    return out, last_fetched


def fetch_sec(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=SEC_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


# -------- Provenance --------

_sources: list[dict[str, str]] = []


def record_source(endpoint: str, fetched_at: str, context: str) -> None:
    _sources.append({
        "endpoint": endpoint,
        "fetched_at": fetched_at,
        "context": context,
    })


# -------- Resolver helpers --------
#
# Lifted from examples/run-event-study.py wave 13 (PEAR/EVENT).
# If event-study changes its resolver, mirror here. Kept inline so this
# script stays independent of event-study's module surface.

_benzinga_cache: dict[str, list[dict[str, Any]]] = {}
_edgar_cache: dict[str, list[dict[str, Any]]] = {}
_cik_cache: dict[str, str | None] = {}

_SEC_TICKER_MAP_CACHE: dict[str, str] | None = None
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

# 8-K items accepted as earnings-class signals. 2.02 is the standard
# "Results of Operations and Financial Condition" item. 7.01 (Reg FD)
# and 8.01 (Other Events) are softer signals biotechs and small caps
# sometimes use for earnings-equivalent disclosures.
EDGAR_EARNINGS_ITEMS = ("2.02", "7.01", "8.01")
EDGAR_STRONG_ITEMS = ("2.02",)


def fetch_sec_ticker_map() -> dict[str, str]:
    """Return TICKER -> 10-digit zero-padded CIK from SEC's public mapping."""
    global _SEC_TICKER_MAP_CACHE
    if _SEC_TICKER_MAP_CACHE is not None:
        return _SEC_TICKER_MAP_CACHE
    try:
        doc = fetch_sec(SEC_TICKER_MAP_URL)
        record_source(
            SEC_TICKER_MAP_URL,
            utcnow_iso(),
            "SEC canonical ticker->CIK map (fallback for Massive)",
        )
    except Exception as e:
        print(f"  SEC ticker map: {e}", file=sys.stderr)
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


def get_cik(ticker: str) -> str | None:
    """Pull the zero-padded 10-digit CIK for a ticker.

    Primary: Massive /v3/reference/tickers/{T}.cik. Fallback: SEC's free
    canonical company_tickers.json when Massive returns no cik.
    """
    if ticker in _cik_cache:
        return _cik_cache[ticker]
    massive_ok = False
    try:
        body, fetched_at = client.get(f"/v3/reference/tickers/{ticker}")
        record_source(
            f"/v3/reference/tickers/{ticker}",
            fetched_at,
            f"Ticker metadata for {ticker} (CIK lookup)",
        )
        massive_ok = True
    except Exception as e:
        print(f"  ticker reference {ticker}: {e}", file=sys.stderr)
        body = {}
    results = (body.get("results") or {}) if isinstance(body, dict) else {}
    cik_raw = results.get("cik") if isinstance(results, dict) else None
    if cik_raw:
        padded = str(cik_raw).zfill(10)
        _cik_cache[ticker] = padded
        return padded
    sec_map = fetch_sec_ticker_map()
    fallback = sec_map.get(ticker.upper())
    if fallback:
        print(
            f"  CIK fallback: {ticker} -> {fallback} via SEC ticker map "
            f"(massive_ok={massive_ok})",
            file=sys.stderr,
        )
        _cik_cache[ticker] = fallback
        return fallback
    _cik_cache[ticker] = None
    return None


def benzinga_earnings(ticker: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
    """Return Benzinga earnings rows for a ticker within [date_from, date_to].

    Returns empty list (and the caller will fall back to EDGAR) if the
    Benzinga add-on is missing or the endpoint is unavailable.
    """
    cache_key = f"{ticker}:{date_from}:{date_to}"
    if cache_key in _benzinga_cache:
        return _benzinga_cache[cache_key]
    print(f"  Benzinga earnings {ticker} {date_from}..{date_to}", file=sys.stderr)
    try:
        rows, fetched_at = fetch_all(
            "/benzinga/v1/earnings",
            {
                "ticker": ticker,
                "limit": 40,
                "order": "asc",
                "sort": "date",
                "date.gte": date_from,
                "date.lte": date_to,
            },
        )
        record_source(
            "/benzinga/v1/earnings?ticker={ticker}",
            fetched_at,
            f"Benzinga earnings for {ticker} ({date_from}..{date_to})",
        )
    except Exception as e:
        print(f"  Benzinga earnings {ticker}: {e}", file=sys.stderr)
        rows = []
    _benzinga_cache[cache_key] = rows
    return rows


def edgar_earnings(ticker: str) -> list[dict[str, Any]]:
    """Return earnings prints sourced from SEC EDGAR 8-K filings.

    Accepts items 2.02 (standard earnings release), 7.01 (Reg FD), and
    8.01 (Other Events). Each row is tagged with item_code +
    signal_strength ("strong" for 2.02, "soft" for 7.01/8.01).
    EDGAR is past-only (no future earnings calendar).
    """
    if ticker in _edgar_cache:
        return _edgar_cache[ticker]
    cik_padded = get_cik(ticker)
    if not cik_padded:
        _edgar_cache[ticker] = []
        return []
    print(f"  SEC EDGAR 8-Ks {ticker} (CIK {cik_padded})", file=sys.stderr)
    sec_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    try:
        doc = fetch_sec(sec_url)
        record_source(
            sec_url,
            utcnow_iso(),
            f"SEC EDGAR submissions for {ticker} (8-K items "
            + "/".join(EDGAR_EARNINGS_ITEMS) + ")",
        )
    except Exception as e:
        print(f"  SEC EDGAR {ticker}: {e}", file=sys.stderr)
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
            "date": et_date,
            "session": session,
            "company_name": company_name,
            "consensus_eps": None,
            "consensus_revenue": None,
            "item_code": matched_item,
            "signal_strength": signal_strength,
            "_source": "edgar_8k",
        })
    out.sort(key=lambda r: r["date"], reverse=True)
    _edgar_cache[ticker] = out
    return out


# -------- Status classifier --------


STATUS_BLACKOUT_IMMINENT = "blackout_imminent"
STATUS_BLACKOUT_SOON = "blackout_soon"
STATUS_BLACKOUT_EXTENDED = "blackout_extended"
STATUS_JUST_PRINTED = "just_printed"
STATUS_RECENT_PRINT = "recent_print"
STATUS_CLEAR = "clear"
STATUS_UNRESOLVED = "unresolved"


def classify_status(
    next_date_iso: str | None,
    past_date_iso: str | None,
    today_iso: str,
    window_days_forward: int,
    window_days_past: int,
) -> tuple[str, int | None]:
    """Pick the dominant status + days_until for a ticker.

    Forward earnings beat past earnings: if a ticker has both a recent
    print and an upcoming one within the windows, the upcoming one
    determines the status (you care more about what's coming).
    """
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
        days_until = (pd_ - today_d).days  # negative
        if -3 <= days_until <= 0:
            return STATUS_JUST_PRINTED, days_until
        if -window_days_past <= days_until < -3:
            return STATUS_RECENT_PRINT, days_until
    return STATUS_CLEAR, None


# -------- Per-ticker resolver --------


def resolve_ticker(
    ticker: str,
    today_iso: str,
    window_days_forward: int,
    window_days_past: int,
) -> dict[str, Any]:
    """Return the per-ticker result dict.

    Pulls Benzinga first across [today-past, today+forward]. If empty,
    falls back to EDGAR (past-only). Picks the earliest forward date as
    `next_earnings_date` and the latest past date as
    `most_recent_earnings_date`.
    """
    today_d = date.fromisoformat(today_iso)
    date_from = (today_d - timedelta(days=window_days_past)).isoformat()
    date_to = (today_d + timedelta(days=window_days_forward)).isoformat()

    bz_rows = benzinga_earnings(ticker, date_from, date_to)
    if bz_rows:
        source = "benzinga"
        # Filter to confirmed prints + dated rows
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
        edgar_rows = edgar_earnings(ticker)
        # Filter EDGAR to the past window only; EDGAR has no future calendar.
        rows = [
            r for r in edgar_rows
            if date_from <= r["date"] <= today_iso
        ]

    if not rows and not bz_rows:
        # Resolver returned nothing from either source.
        return {
            "ticker": ticker,
            "status": STATUS_UNRESOLVED,
            "next_earnings_date": None,
            "most_recent_earnings_date": None,
            "days_until": None,
            "expected_release_time": None,
            "consensus_eps": None,
            "consensus_revenue": None,
            "item_code": None,
            "signal_strength": None,
            "source": source,
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
    status, days_until = classify_status(
        next_date, past_date, today_iso, window_days_forward, window_days_past,
    )

    # Choose which row supplies the metadata payload: forward if any,
    # otherwise the past row.
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


def _session_from_benzinga(row: dict[str, Any]) -> str:
    """Benzinga uses 'time' field HH:MM:SS ET. Map to BMO/AMC/DMH."""
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


# -------- Tier detection --------


def detect_tier(results: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Inspect per-ticker sources to decide overall tier + caveats."""
    sources_used = {r["source"] for r in results}
    benzinga_hits = any(r["source"] == "benzinga" and r["status"] != STATUS_UNRESOLVED
                        for r in results)
    if benzinga_hits:
        tier = "A"
        caveats: list[str] = []
        if "edgar_8k" in sources_used:
            caveats.append(
                "Mixed sources: some tickers resolved via Benzinga, others "
                "fell back to SEC EDGAR 8-K (date-only, no consensus EPS)."
            )
        return tier, caveats
    return "B", [
        "Benzinga not available on this key; all tickers resolved via SEC "
        "EDGAR 8-K items 2.02 / 7.01 / 8.01. Forward earnings calendar is "
        "unavailable (EDGAR is past-only), so future prints will surface "
        "as 'unresolved' until they file.",
    ]


# -------- Rendering --------


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


def render(payload: dict[str, Any]) -> str:
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
            # sort by absolute days_until (smallest first)
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


# -------- Main runner --------


def run(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    tickers = [t.strip().upper() for t in args.watchlist.split(",") if t.strip()]
    today_iso = TODAY.isoformat()

    results: list[dict[str, Any]] = []
    for t in tickers:
        results.append(
            resolve_ticker(t, today_iso, args.window_days, args.include_past_days)
        )

    tier, tier_caveats = detect_tier(results)

    summary = {
        "n_blackout_imminent": sum(1 for r in results if r["status"] == STATUS_BLACKOUT_IMMINENT),
        "n_blackout_soon": sum(1 for r in results if r["status"] == STATUS_BLACKOUT_SOON),
        "n_blackout_extended": sum(1 for r in results if r["status"] == STATUS_BLACKOUT_EXTENDED),
        "n_just_printed": sum(1 for r in results if r["status"] == STATUS_JUST_PRINTED),
        "n_recent_print": sum(1 for r in results if r["status"] == STATUS_RECENT_PRINT),
        "n_clear": sum(1 for r in results if r["status"] == STATUS_CLEAR),
        "n_unresolved": sum(1 for r in results if r["status"] == STATUS_UNRESOLVED),
    }

    payload: dict[str, Any] = {
        "skill": "earnings-blackout",
        "as_of": today_iso,
        "fetched_at": utcnow_iso(),
        "window_days_forward": args.window_days,
        "window_days_past": args.include_past_days,
        "watchlist": tickers,
        "n_tickers": len(tickers),
        "tier": tier,
        "tier_caveats": tier_caveats,
        "results": results,
        "summary": summary,
        "sources": list(_sources),
    }

    rendered = render(payload)
    return payload, rendered


def main() -> None:
    parser = argparse.ArgumentParser(
        description="earnings-blackout skill reference run"
    )
    parser.add_argument(
        "--watchlist",
        required=True,
        help="Comma-separated tickers (e.g. NVDA,TSLA,AMZN,GOOGL,META,AAPL,MSFT)",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="Forward window in calendar days (default: 7).",
    )
    parser.add_argument(
        "--include-past-days",
        type=int,
        default=3,
        help="Also include earnings dates within the past N days (default: 3).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output markdown path (default: examples/earnings-blackout-output.md).",
    )
    parser.add_argument(
        "--format",
        choices=["render", "json", "both"],
        default=None,
        help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT. Default: render.",
    )
    args = parser.parse_args()
    fmt = resolve_output_format(args.format)

    payload, rendered = run(args)

    out_path = args.out or os.path.join(
        os.path.dirname(__file__), "earnings-blackout-output.md"
    )
    with open(out_path, "w") as f:
        f.write("# earnings-blackout run\n\n")
        f.write(f"Generated: {utcnow_iso()}\n")
        f.write(f"Watchlist: {payload['n_tickers']} tickers · Tier: {payload['tier']}\n\n")
        f.write("## Layer 1: canonical JSON\n\n```json\n")
        f.write(json.dumps(payload, indent=2, default=str))
        f.write("\n```\n\n")
        f.write("## Layer 2: rendered output\n\n```\n")
        f.write(rendered)
        f.write("\n```\n")

    print(f"\nDONE. Output -> {out_path}", file=sys.stderr)
    emit_to_stdout(rendered, payload, fmt)


if __name__ == "__main__":
    main()
