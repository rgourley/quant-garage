"""
morning-brief: 60-second daily open briefing.

Lighter and shorter-horizon than weekly-brief. Chains market-regime,
today's macro events, and last-N news per watchlist ticker.

    from quant_garage.skills.morning_brief import run, render
    payload = run(watchlist="NVDA,ALLO,SOFI")
"""
from __future__ import annotations

import sys
from typing import Iterable

from .. import MassiveClient, today, utcnow_iso
from . import market_regime, macro_event_calendar, news_scanner


def run(
    watchlist: str | Iterable[str] | None = None,
    news_last_n: int = 3,
    client: MassiveClient | None = None,
) -> dict:
    """Daily open briefing.

    Args:
        watchlist: optional. If provided, per-ticker news is included.
        news_last_n: N most recent articles per ticker. Default 3.
        client: reuse an existing MassiveClient.
    """
    if watchlist is not None:
        if isinstance(watchlist, str):
            tickers = [t.strip().upper() for t in watchlist.split(",") if t.strip()]
        else:
            tickers = [t.strip().upper() for t in watchlist if t and t.strip()]
    else:
        tickers = []

    client = client or MassiveClient()
    sections: dict = {}
    errors: list[dict] = []

    def _try(name, fn):
        try:
            sections[name] = fn()
        except Exception as exc:
            errors.append({"section": name, "error": str(exc)})

    print("[1/3] market-regime...", file=sys.stderr)
    _try("market_regime", lambda: market_regime.run(client=client))

    print("[2/3] macro-event-calendar (2d)...", file=sys.stderr)
    _try("macro_calendar", lambda: macro_event_calendar.run(
        window_days=2, client=client,
    ))

    if tickers:
        print(f"[3/3] news-scanner (last-{news_last_n} per ticker)...",
              file=sys.stderr)
        _try("news_scanner", lambda: news_scanner.run(
            watchlist=tickers, last_n=news_last_n, top_n=len(tickers) * news_last_n,
            client=client,
        ))
    else:
        sections["news_scanner"] = None

    headline = _build_headline(sections, tickers)

    return {
        "scan_params": {
            "tickers": tickers,
            "news_last_n": news_last_n,
            "as_of": today().isoformat(),
        },
        "headline": headline,
        "sections": sections,
        "errors": errors,
        "generated_at": utcnow_iso(),
    }


def _build_headline(sections: dict, tickers: list[str]) -> dict:
    hl: dict = {
        "regime": None,
        "today_events": [],
        "top_news": [],
    }
    mr = sections.get("market_regime")
    if mr:
        hl["regime"] = (mr.get("composite_regime") or {}).get("label")

    mc = sections.get("macro_calendar")
    if mc:
        today_iso = today().isoformat()
        for e in (mc.get("upcoming") or []):
            if e.get("date") == today_iso:
                hl["today_events"].append({
                    "name": e.get("event_name"),
                    "impact": e.get("impact_tier"),
                    "time_et": e.get("release_time_et"),
                })

    ns = sections.get("news_scanner")
    if ns:
        events = ns.get("events") or []
        # Top 5 by impact_percentile if available
        events_sorted = sorted(
            events,
            key=lambda e: -(e.get("impact_percentile") or 0),
        )
        for e in events_sorted[:5]:
            hl["top_news"].append({
                "ticker": e.get("ticker"),
                "headline": (e.get("title") or "")[:100],
                "sentiment": e.get("sentiment_score"),
            })

    return hl


def render(payload: dict) -> str:
    params = payload["scan_params"]
    hl = payload["headline"]
    sections = payload["sections"]
    lines: list[str] = []

    lines.append(f"Morning Brief — {params['as_of']}")
    if params["tickers"]:
        lines.append(f"Watchlist: {len(params['tickers'])} tickers")
    lines.append("")

    lines.append("HEADLINE")
    lines.append("─" * 60)
    if hl.get("regime"):
        lines.append(f"Regime:     {hl['regime'].upper()}")
    if hl.get("today_events"):
        ev_str = ", ".join(
            f"{e['name']} ({e['time_et']} ET, {e['impact']})"
            for e in hl["today_events"]
        )
        lines.append(f"Today:      {ev_str}")
    else:
        lines.append("Today:      no scheduled macro releases")
    if hl.get("top_news"):
        for n in hl["top_news"][:5]:
            sent = n.get("sentiment")
            sent_str = f" ({sent:+.2f})" if sent is not None else ""
            lines.append(
                f"  {n['ticker']}: {n['headline']}{sent_str}"
            )
    lines.append("")

    render_map = [
        ("market_regime", "MACRO REGIME", market_regime.render),
        ("macro_calendar", "MACRO CALENDAR (2d)", macro_event_calendar.render),
        ("news_scanner", "WATCHLIST NEWS", news_scanner.render),
    ]
    for key, title, render_fn in render_map:
        sub = sections.get(key)
        if sub is None:
            continue
        lines.append(title)
        lines.append("═" * 60)
        try:
            lines.append(render_fn(sub))
        except Exception as exc:
            lines.append(f"(render error: {exc})")
        lines.append("")

    errors = payload.get("errors") or []
    if errors:
        lines.append("ERRORS")
        lines.append("─" * 60)
        for e in errors:
            lines.append(f"  {e['section']}: {e['error']}")

    return "\n".join(lines)
