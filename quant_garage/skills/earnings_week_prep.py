"""
earnings-week-prep: Sunday-night prep for the week's earnings prints.

Chains earnings-blackout across the watchlist to find who prints,
then earnings-drilldown + technical-briefing per imminent print
(capped to top_n_drilldown to control cost).

    from quant_garage.skills.earnings_week_prep import run, render
    payload = run(watchlist="NVDA,ALLO,SOFI,QCOM")
"""
from __future__ import annotations

import sys
from typing import Iterable

from .. import MassiveClient, today, utcnow_iso
from . import earnings_blackout, earnings_drilldown, technical_briefing


def run(
    watchlist: str | Iterable[str],
    window_days: int = 7,
    top_n_drilldown: int = 5,
    include_technicals: bool = True,
    client: MassiveClient | None = None,
) -> dict:
    """Sunday-night briefing on the week's earnings prints.

    Args:
        watchlist: comma-separated string or iterable of tickers.
        window_days: forward earnings window. Default 7.
        top_n_drilldown: how many imminent prints to drilldown on. Default 5.
        include_technicals: also run technical-briefing per print. Default True.
        client: reuse an existing MassiveClient.
    """
    if isinstance(watchlist, str):
        tickers = [t.strip().upper() for t in watchlist.split(",") if t.strip()]
    else:
        tickers = [t.strip().upper() for t in watchlist if t and t.strip()]
    if not tickers:
        raise ValueError("watchlist must contain at least one ticker")

    client = client or MassiveClient()
    errors: list[dict] = []

    # 1) Earnings blackout on the whole watchlist
    print(f"[1/2] earnings-blackout ({len(tickers)} tickers)...", file=sys.stderr)
    try:
        eb = earnings_blackout.run(
            watchlist=tickers, window_days=window_days,
            include_past_days=3, client=client,
        )
    except Exception as exc:
        errors.append({"section": "earnings_blackout", "error": str(exc)})
        eb = None

    # 2) Determine imminent prints (in window, forward)
    imminent: list[dict] = []
    if eb:
        for r in (eb.get("results") or []):
            nd = r.get("next_earnings_date")
            days = r.get("days_until")
            if nd and days is not None and 0 <= days <= window_days:
                imminent.append({
                    "ticker": r.get("ticker"),
                    "date": nd,
                    "days_until": days,
                    "session": r.get("expected_release_time"),
                })
    imminent.sort(key=lambda e: e["days_until"])
    imminent_top = imminent[:top_n_drilldown]

    # 3) Drilldown + technicals per imminent print
    print(f"[2/2] drilldown + technicals ({len(imminent_top)} names)...",
          file=sys.stderr)
    prep: list[dict] = []
    for entry in imminent_top:
        t = entry["ticker"]
        row: dict = {"ticker": t, "date": entry["date"],
                     "days_until": entry["days_until"],
                     "session": entry.get("session")}
        try:
            row["drilldown"] = earnings_drilldown.run(t, client_=client)
        except Exception as exc:
            errors.append({"section": f"drilldown:{t}", "error": str(exc)})
            row["drilldown"] = None
        if include_technicals:
            try:
                row["technical"] = technical_briefing.run(t, client=client)
            except Exception as exc:
                errors.append({"section": f"technical:{t}",
                                "error": str(exc)})
                row["technical"] = None
        prep.append(row)

    return {
        "scan_params": {
            "tickers": tickers,
            "window_days": window_days,
            "top_n_drilldown": top_n_drilldown,
            "include_technicals": include_technicals,
            "as_of": today().isoformat(),
        },
        "earnings_blackout": eb,
        "imminent_prints": imminent,
        "prep": prep,
        "errors": errors,
        "generated_at": utcnow_iso(),
    }


def render(payload: dict) -> str:
    params = payload["scan_params"]
    imminent = payload["imminent_prints"]
    prep = payload["prep"]
    eb = payload["earnings_blackout"]
    lines: list[str] = []

    lines.append(f"Earnings Week Prep — {params['as_of']}")
    lines.append(
        f"Watchlist: {len(params['tickers'])} tickers · "
        f"Forward window: {params['window_days']}d · "
        f"Drilldown top {params['top_n_drilldown']}"
    )
    lines.append("")

    lines.append("HEADLINE")
    lines.append("─" * 60)
    if not imminent:
        lines.append(
            f"No watchlist earnings in the next {params['window_days']}d."
        )
    else:
        lines.append(f"{len(imminent)} prints this window:")
        for e in imminent:
            lines.append(
                f"  {e['ticker']:<8} {e['date']} "
                f"{(e.get('session') or ''):<3} ({e['days_until']}d)"
            )
    lines.append("")

    if eb:
        lines.append("EARNINGS BLACKOUT (watchlist)")
        lines.append("═" * 60)
        try:
            lines.append(earnings_blackout.render(eb))
        except Exception as exc:
            lines.append(f"(render error: {exc})")
        lines.append("")

    for row in prep:
        header = (f"{row['ticker']} · {row['date']} "
                  f"{(row.get('session') or '')} ({row['days_until']}d out)")
        lines.append(header)
        lines.append("═" * 60)
        dd = row.get("drilldown")
        if dd:
            try:
                lines.append(earnings_drilldown.render(dd))
            except Exception as exc:
                lines.append(f"(drilldown render error: {exc})")
        else:
            lines.append("(drilldown unavailable)")
        tech = row.get("technical")
        if tech:
            lines.append("")
            lines.append(f"--- Technical read for {row['ticker']} ---")
            try:
                lines.append(technical_briefing.render(tech))
            except Exception as exc:
                lines.append(f"(technical render error: {exc})")
        lines.append("")

    errors = payload.get("errors") or []
    if errors:
        lines.append("ERRORS")
        lines.append("─" * 60)
        for e in errors:
            lines.append(f"  {e['section']}: {e['error']}")

    return "\n".join(lines)
