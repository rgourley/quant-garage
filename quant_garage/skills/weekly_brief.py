"""
weekly-brief: Sunday-night prep for the week ahead.

Watchlist-focused, not position-focused. Composes market-regime,
sector-rotation-signal, macro-event-calendar (7d), and earnings-
blackout (7d) into a briefing that sets up the week.

Different from portfolio-review: this is what's HAPPENING this week
across your watchlist, not what you should DO with your positions.

    from quant_garage.skills.weekly_brief import run, render
    payload = run(watchlist="NVDA,ALLO,SOFI,HOOD")
"""
from __future__ import annotations

import sys
from typing import Iterable

from .. import MassiveClient, today, utcnow_iso
from . import (
    market_regime,
    sector_rotation_signal,
    macro_event_calendar,
    earnings_blackout,
)


def run(
    watchlist: str | Iterable[str],
    window_days: int = 7,
    rotation_window_days: int = 30,
    client: MassiveClient | None = None,
) -> dict:
    """Sunday-night briefing for the week ahead.

    Args:
        watchlist: comma-separated string or iterable of tickers.
        window_days: forward window for calendar + earnings. Default 7.
        rotation_window_days: sector-rotation lookback. Default 30.
        client: reuse an existing MassiveClient.
    """
    if isinstance(watchlist, str):
        tickers = [t.strip().upper() for t in watchlist.split(",") if t.strip()]
    else:
        tickers = [t.strip().upper() for t in watchlist if t and t.strip()]
    if not tickers:
        raise ValueError("watchlist must contain at least one ticker")

    client = client or MassiveClient()
    sections: dict = {}
    errors: list[dict] = []

    def _try(name, fn):
        try:
            sections[name] = fn()
        except Exception as exc:
            errors.append({"section": name, "error": str(exc)})

    print("[1/4] market-regime...", file=sys.stderr)
    _try("market_regime", lambda: market_regime.run(client=client))

    print("[2/4] sector-rotation-signal...", file=sys.stderr)
    _try("sector_rotation", lambda: sector_rotation_signal.run(
        rotation_window=rotation_window_days, client=client,
    ))

    print(f"[3/4] macro-event-calendar ({window_days}d)...", file=sys.stderr)
    _try("macro_calendar", lambda: macro_event_calendar.run(
        window_days=window_days, client=client,
    ))

    print(f"[4/4] earnings-blackout ({len(tickers)} tickers)...",
          file=sys.stderr)
    _try("earnings_blackout", lambda: earnings_blackout.run(
        watchlist=tickers, window_days=window_days, client=client,
    ))

    headline = _build_headline(sections, tickers, window_days)

    return {
        "scan_params": {
            "tickers": tickers,
            "window_days": window_days,
            "rotation_window_days": rotation_window_days,
            "as_of": today().isoformat(),
        },
        "headline": headline,
        "sections": sections,
        "errors": errors,
        "generated_at": utcnow_iso(),
    }


def _build_headline(sections: dict, tickers: list[str], window_days: int) -> dict:
    hl: dict = {
        "regime": None,
        "rotation_theme": None,
        "top_macro": [],
        "prints_this_week": [],
        "n_watchlist": len(tickers),
    }

    mr = sections.get("market_regime")
    if mr:
        hl["regime"] = (mr.get("composite_regime") or {}).get("label")

    sr = sections.get("sector_rotation")
    if sr:
        hl["rotation_theme"] = sr.get("theme_read")

    mc = sections.get("macro_calendar")
    if mc:
        upcoming = mc.get("upcoming") or []
        # Top 3 by impact tier
        very_high = [e for e in upcoming if e.get("impact_tier") == "very_high"]
        high = [e for e in upcoming if e.get("impact_tier") == "high"]
        for e in (very_high + high)[:3]:
            hl["top_macro"].append({
                "name": e.get("event_name"),
                "date": e.get("date"),
                "days_out": e.get("days_out"),
                "impact": e.get("impact_tier"),
            })

    eb = sections.get("earnings_blackout")
    if eb:
        results = eb.get("results") or []
        for r in results:
            nd = r.get("next_earnings_date")
            days = r.get("days_until")
            if nd and days is not None and 0 <= days <= window_days:
                hl["prints_this_week"].append({
                    "ticker": r.get("ticker"),
                    "date": nd,
                    "days_out": days,
                    "session": r.get("expected_release_time"),
                })
        hl["prints_this_week"].sort(key=lambda e: e["days_out"])

    return hl


def _fmt_dollar(x):
    if x is None:
        return "n/a"
    return f"${abs(x):,.0f}" if abs(x) >= 1000 else f"${abs(x):,.2f}"


def render(payload: dict) -> str:
    params = payload["scan_params"]
    hl = payload["headline"]
    sections = payload["sections"]
    lines: list[str] = []

    lines.append(
        f"Weekly Brief — week of {params['as_of']}\n"
        f"Watchlist: {hl['n_watchlist']} tickers · "
        f"Forward window: {params['window_days']}d"
    )
    lines.append("")

    lines.append("HEADLINE")
    lines.append("─" * 60)
    if hl.get("regime"):
        lines.append(f"Regime:        {hl['regime'].upper()}")
    if hl.get("rotation_theme"):
        lines.append(f"Rotation:      {hl['rotation_theme']}")
    if hl.get("top_macro"):
        events_str = ", ".join(
            f"{e['name']} ({e['date']}, {e['days_out']}d, {e['impact']})"
            for e in hl["top_macro"]
        )
        lines.append(f"This week:     {events_str}")
    if hl.get("prints_this_week"):
        prints_str = ", ".join(
            f"{e['ticker']} ({e['date']}, {e['days_out']}d)"
            for e in hl["prints_this_week"]
        )
        lines.append(f"Prints ({len(hl['prints_this_week'])}): {prints_str}")
    else:
        lines.append(
            f"Prints (0):    no watchlist earnings in the next "
            f"{params['window_days']}d"
        )
    lines.append("")

    render_map = [
        ("market_regime", "MACRO REGIME", market_regime.render),
        ("sector_rotation", "SECTOR ROTATION", sector_rotation_signal.render),
        ("macro_calendar",
         f"MACRO CALENDAR ({params['window_days']}d)",
         macro_event_calendar.render),
        ("earnings_blackout",
         f"WATCHLIST EARNINGS ({params['window_days']}d)",
         earnings_blackout.render),
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
        lines.append("")

    return "\n".join(lines)
