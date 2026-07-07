"""
portfolio-review: composite skill that chains the 6-tool workflow.

Runs market-regime + sector-rotation-signal + risk-report + earnings-
blackout + macro-event-calendar + corporate-actions-scanner + portfolio-
rebalancer on a positions map, then stitches the outputs into a single
briefing. Turns the manual 6-command sequence into one call.

Motivated by the 2026-07-02 review, which required six separate CLI
invocations to answer "what's on my book and what should I do." This
skill runs them all in the right order, threads the shared context
(book value, tickers, weights) through, and emits a hybrid output with
both a headline summary and per-section detail.

    from quant_garage.skills.portfolio_review import run, render
    payload = run(
        positions="JEPI=0.305,ALLO=0.183,BRK.B=0.163,...",
        book_value=650000,
    )
    print(render(payload))

Design note: composed skill. Calls run() on each underlying tool and
carries their JSON forward. No shelling out. Uses individual skill
render() helpers to build the per-section blocks; adds a headline
block that summarizes the crossover.
"""
from __future__ import annotations

import sys
from typing import Iterable

from .. import MassiveClient, today, utcnow_iso
from . import (
    market_regime,
    sector_rotation_signal,
    risk_report,
    earnings_blackout,
    macro_event_calendar,
    corporate_actions_scanner,
    portfolio_rebalancer,
    historical_analog_finder,
)


# ETFs and cash-like symbols skipped by scanners that only run on
# single-name equities (earnings-blackout, corporate-actions-scanner).
# corporate-actions-scanner would 404 on these; earnings-blackout would
# either return null or waste API calls.
ETF_TICKERS = {
    "JEPI", "VTI", "GLD", "SPY", "QQQ", "DIA", "IWM", "XLK", "XLF",
    "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC",
    "TLT", "HYG", "LQD", "TIP", "SHY", "AGG", "BND", "SGOV", "BIL",
    "VOO", "IVV", "VEA", "VWO", "EEM", "EFA", "SCHW",
}


def _parse_positions(positions: str | dict[str, float]) -> dict[str, float]:
    if isinstance(positions, dict):
        return {k.upper(): float(v) for k, v in positions.items()}
    out: dict[str, float] = {}
    for chunk in positions.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        t, v = chunk.split("=", 1)
        out[t.strip().upper()] = float(v.strip())
    return out


def _split_equities_from_etfs(tickers: list[str]) -> tuple[list[str], list[str]]:
    equities = [t for t in tickers if t not in ETF_TICKERS]
    etfs = [t for t in tickers if t in ETF_TICKERS]
    return equities, etfs


def run(
    positions: str | dict[str, float],
    book_value: float = 100000.0,
    lookback_days: int = 252,
    max_variance_share: float = 0.25,
    max_weight: float = 0.15,
    max_churn: float = 0.10,
    corp_actions_lookback_days: int = 180,
    earnings_window_days: int = 30,
    macro_window_days: int = 30,
    rotation_window_days: int = 30,
    analog_k: int = 20,
    analog_horizons_days: list[int] | None = None,
    include_rebalance: bool = True,
    include_historical_analog: bool = True,
    client: MassiveClient | None = None,
) -> dict:
    """Run the composite portfolio review.

    Args:
        positions: comma-separated 'TICKER=WEIGHT' string or dict.
        book_value: dollar value of the book. Default 100,000.
        lookback_days: shared history for risk-report + rotations.
        max_variance_share: rebalancer's variance-share cap.
        max_weight: rebalancer's per-name weight cap.
        max_churn: rebalancer's max one-way turnover per rebalance.
        corp_actions_lookback_days: how far back to scan 8-Ks. Default 180.
        earnings_window_days: forward earnings-blackout window. Default 30.
        macro_window_days: forward macro-calendar window. Default 30.
        rotation_window_days: sector-rotation lookback. Default 30.
        analog_k: nearest-neighbor count for historical-analog-finder. Default 20.
        analog_horizons_days: forward horizons for analog forecast.
            Default [30, 60, 90, 252].
        include_rebalance: if False, skip the rebalancer.
        include_historical_analog: if False, skip the analog forecast.
        client: reuse an existing MassiveClient.
    """
    if analog_horizons_days is None:
        analog_horizons_days = [30, 60, 90, 252]
    weights = _parse_positions(positions)
    if not weights:
        raise ValueError("positions must contain at least one ticker=weight pair")

    client = client or MassiveClient()
    tickers = sorted(weights.keys())
    equities, etfs = _split_equities_from_etfs(tickers)

    sections: dict[str, dict] = {}
    errors: list[dict] = []

    def _try(section_name: str, fn):
        try:
            sections[section_name] = fn()
        except Exception as exc:
            errors.append({"section": section_name, "error": str(exc)})

    # 1) Macro regime
    print("[1/8] market-regime...", file=sys.stderr)
    _try("market_regime", lambda: market_regime.run(client=client))

    # 2) Sector rotation
    print("[2/8] sector-rotation-signal...", file=sys.stderr)
    _try("sector_rotation", lambda: sector_rotation_signal.run(
        rotation_window=rotation_window_days,
        client=client,
    ))

    # 3) Historical analog (regime-conditional forward distribution)
    if include_historical_analog:
        print("[3/8] historical-analog-finder...", file=sys.stderr)
        _try("historical_analog", lambda: historical_analog_finder.run(
            k=analog_k,
            horizon_days=analog_horizons_days,
            client=client,
        ))
    else:
        sections["historical_analog"] = None

    # 4) Risk report
    print("[4/8] risk-report...", file=sys.stderr)
    pos_str = ",".join(f"{t}={weights[t]}" for t in tickers)
    _try("risk_report", lambda: risk_report.run(
        positions=pos_str,
        lookback_days=lookback_days,
        client_=client,
    ))

    # 5) Earnings blackout (equities only — ETFs don't have earnings)
    if equities:
        print(f"[5/8] earnings-blackout ({len(equities)} equities)...",
              file=sys.stderr)
        _try("earnings_blackout", lambda: earnings_blackout.run(
            watchlist=list(equities),
            window_days=earnings_window_days,
            client=client,
        ))
    else:
        sections["earnings_blackout"] = None

    # 6) Macro calendar
    print("[6/8] macro-event-calendar...", file=sys.stderr)
    _try("macro_calendar", lambda: macro_event_calendar.run(
        window_days=macro_window_days,
        client=client,
    ))

    # 7) Corporate actions (equities only — ETFs don't file 8-Ks)
    if equities:
        print(f"[7/8] corporate-actions-scanner ({len(equities)} equities)...",
              file=sys.stderr)
        _try("corporate_actions", lambda: corporate_actions_scanner.run(
            watchlist=",".join(equities),
            lookback_days=corp_actions_lookback_days,
            client=client,
        ))
    else:
        sections["corporate_actions"] = None

    # 8) Rebalancer
    if include_rebalance:
        print("[8/8] portfolio-rebalancer...", file=sys.stderr)
        _try("rebalancer", lambda: portfolio_rebalancer.run(
            positions=pos_str,
            book_value=book_value,
            max_variance_share=max_variance_share,
            max_weight=max_weight,
            max_churn=max_churn,
            lookback_days=lookback_days,
            client=client,
        ))
    else:
        sections["rebalancer"] = None

    # Headline summary: pull the most decision-relevant fact from each
    # section into a top-level headline block.
    headline = _build_headline(sections, book_value, tickers, equities, etfs)

    return {
        "scan_params": {
            "tickers": tickers,
            "book_value": book_value,
            "n_positions": len(tickers),
            "n_equities": len(equities),
            "n_etfs": len(etfs),
            "as_of": today().isoformat(),
        },
        "headline": headline,
        "sections": sections,
        "errors": errors,
        "generated_at": utcnow_iso(),
    }


def _build_headline(
    sections: dict[str, dict],
    book_value: float,
    tickers: list[str],
    equities: list[str],
    etfs: list[str],
) -> dict:
    """Distill the most decision-relevant fact from each section into
    a top-level summary block."""
    hl: dict = {
        "regime": None,
        "rotation_theme": None,
        "analog_read": None,
        "portfolio_vol": None,
        "max_variance_share_name": None,
        "max_variance_share_pct": None,
        "top_earnings": [],
        "next_macro_event": None,
        "top_corp_action": None,
        "rebalance_summary": None,
    }

    mr = sections.get("market_regime")
    if mr:
        hl["regime"] = (mr.get("composite_regime") or {}).get("label")

    sr = sections.get("sector_rotation")
    if sr:
        hl["rotation_theme"] = sr.get("theme_read")

    ha = sections.get("historical_analog")
    if ha:
        # Pick the 90d horizon (or the middle horizon requested) as the
        # summary. Report median forward return + hit rate.
        dists = ha.get("forward_return_distributions") or {}
        horizons = ha.get("scan_params", {}).get("horizons_days") or []
        # Prefer 90d, else middle horizon
        preferred = 90 if 90 in horizons else (
            horizons[len(horizons) // 2] if horizons else None
        )
        if preferred is not None:
            key = f"{preferred}d"
            row = dists.get(key) or {}
            hl["analog_read"] = {
                "horizon_days": preferred,
                "n_analogs": ha.get("n_analogs"),
                "median": row.get("median"),
                "p25": row.get("p25"),
                "p75": row.get("p75"),
                "hit_rate_above_zero": row.get("hit_rate_above_zero"),
            }

    rr = sections.get("risk_report")
    if rr:
        stats = rr.get("stats") or {}
        # stats.annualized_vol is decimal (0.213). Convert to display %.
        vol_dec = stats.get("annualized_vol")
        hl["portfolio_vol"] = (vol_dec * 100) if vol_dec is not None else None
        # position_risk is a dict keyed by ticker; pick the max variance
        # contributor
        pos_risk = rr.get("position_risk") or {}
        if pos_risk:
            top_t, top_row = max(
                pos_risk.items(),
                key=lambda kv: kv[1].get("variance_contribution_pct", 0.0),
            )
            hl["max_variance_share_name"] = top_t
            # variance_contribution_pct is decimal (0.663). Convert to %.
            hl["max_variance_share_pct"] = (
                top_row.get("variance_contribution_pct", 0.0) * 100
            )

    eb = sections.get("earnings_blackout")
    if eb:
        # earnings_blackout returns a "results" list, one row per ticker,
        # with next_date + days_to_next_date. Filter to future prints
        # with a real date and rank by proximity.
        results = eb.get("results") or []
        upcoming = []
        for r in results:
            nd = r.get("next_earnings_date")
            days = r.get("days_until")
            if not nd or days is None or days < 0:
                continue
            upcoming.append({
                "ticker": r.get("ticker"),
                "date": nd,
                "days_out": days,
                "session": r.get("expected_release_time"),
            })
        upcoming.sort(key=lambda e: e["days_out"])
        hl["top_earnings"] = upcoming[:3]

    mc = sections.get("macro_calendar")
    if mc:
        upcoming = mc.get("upcoming") or []
        # Find first very_high / high impact event
        for e in upcoming:
            if e.get("impact_tier") in ("very_high", "high"):
                hl["next_macro_event"] = {
                    "name": e.get("event_name"),
                    "date": e.get("date"),
                    "days_out": e.get("days_out"),
                    "impact": e.get("impact_tier"),
                }
                break

    ca = sections.get("corporate_actions")
    if ca:
        events = ca.get("events") or []
        # Events are already ranked by |abn T+5|; take the top one
        if events:
            top = events[0]
            hl["top_corp_action"] = {
                "ticker": top.get("ticker"),
                "date": top.get("filing_date"),
                "flavor": top.get("flavor"),
                "headline": top.get("headline"),
                "abnormal_t5_pct": top.get("abnormal_t5_pct"),
            }

    rb = sections.get("rebalancer")
    if rb:
        before = rb.get("portfolio_before") or {}
        after = rb.get("portfolio_after") or {}
        trades = rb.get("trades") or []
        biggest_trim = None
        for t in trades:
            if t.get("action") == "sell":
                biggest_trim = t
                break
        hl["rebalance_summary"] = {
            "vol_before": before.get("annualized_vol"),
            "vol_after": after.get("annualized_vol"),
            "n_trades": rb.get("n_trades"),
            "biggest_trim": (
                {"ticker": biggest_trim["ticker"],
                 "delta_dollar": biggest_trim["delta_dollar"],
                 "weight_before": biggest_trim["weight_before"],
                 "weight_after": biggest_trim["weight_after"]}
                if biggest_trim else None
            ),
        }

    return hl


# ---------- Renderer ----------

def _fmt_pct(x, decimals=1, signed=False):
    if x is None:
        return "n/a"
    sign = "+" if signed and x >= 0 else ""
    return f"{sign}{x * 100:.{decimals}f}%" if x < 1 else f"{sign}{x:.{decimals}f}%"


def _fmt_pct_decimal(x, decimals=1):
    """For values already in decimal form (0.213 = 21.3%)."""
    if x is None:
        return "n/a"
    return f"{x * 100:.{decimals}f}%"


def _fmt_dollar(x):
    if x is None:
        return "n/a"
    ax = abs(x)
    sign = "-" if x < 0 else ""
    if ax >= 1000:
        return f"{sign}${ax:,.0f}"
    return f"{sign}${ax:,.2f}"


def render(payload: dict) -> str:
    params = payload["scan_params"]
    hl = payload["headline"]
    sections = payload["sections"]
    lines: list[str] = []

    # ----- Header -----
    lines.append(
        f"Portfolio Review — {params['as_of']}\n"
        f"Book: {_fmt_dollar(params['book_value'])} across "
        f"{params['n_positions']} positions "
        f"({params['n_equities']} equities, {params['n_etfs']} ETFs)"
    )
    lines.append("")

    # ----- Headline -----
    lines.append("HEADLINE")
    lines.append("─" * 60)
    if hl.get("regime"):
        lines.append(f"Regime:        {hl['regime'].upper()}")
    if hl.get("rotation_theme"):
        lines.append(f"Rotation:      {hl['rotation_theme']}")
    if hl.get("analog_read"):
        ar = hl["analog_read"]
        median = ar.get("median")
        p25 = ar.get("p25")
        p75 = ar.get("p75")
        hit = ar.get("hit_rate_above_zero")
        median_str = _fmt_pct_decimal(median) if median is not None else "n/a"
        iqr_str = (
            f"[{_fmt_pct_decimal(p25)}, {_fmt_pct_decimal(p75)}]"
            if p25 is not None and p75 is not None else ""
        )
        hit_str = f", {hit * 100:.0f}% > 0" if hit is not None else ""
        lines.append(
            f"Analog {ar['horizon_days']}d:    median SPY "
            f"{median_str}{hit_str} across {ar.get('n_analogs')} analogs"
            + (f" · IQR {iqr_str}" if iqr_str else "")
        )
    if hl.get("portfolio_vol") is not None:
        line = f"Portfolio vol: {hl['portfolio_vol']:.1f}%"
        if (hl.get("max_variance_share_name")
                and hl.get("max_variance_share_pct") is not None):
            line += (
                f" · {hl['max_variance_share_name']} drives "
                f"{hl['max_variance_share_pct']:.0f}% of variance"
            )
        lines.append(line)
    if hl.get("top_earnings"):
        earn_str = ", ".join(
            f"{e['ticker']} ({e.get('date')}, {e.get('days_out')}d)"
            for e in hl["top_earnings"]
        )
        lines.append(f"Next earnings: {earn_str}")
    if hl.get("next_macro_event"):
        ev = hl["next_macro_event"]
        lines.append(
            f"Next macro:    {ev['name']} on {ev['date']} "
            f"({ev['days_out']}d, {ev['impact']})"
        )
    if hl.get("top_corp_action"):
        ca = hl["top_corp_action"]
        flavor = f" · {ca['flavor']}" if ca.get("flavor") else ""
        abn = ca.get("abnormal_t5_pct")
        abn_str = f" · abn T+5 {abn:+.1f}%" if abn is not None else ""
        head = ca.get("headline") or ""
        lines.append(
            f"Top 8-K:       {ca['ticker']} {ca['date']}{flavor}{abn_str}"
        )
        if head:
            lines.append(f"               {head[:100]}")
    if hl.get("rebalance_summary"):
        rs = hl["rebalance_summary"]
        vol_before = rs.get("vol_before")
        vol_after = rs.get("vol_after")
        if vol_before is not None and vol_after is not None:
            lines.append(
                f"Rebalance:     vol {_fmt_pct_decimal(vol_before)} -> "
                f"{_fmt_pct_decimal(vol_after)} · "
                f"{rs.get('n_trades', 0)} trades"
            )
        bt = rs.get("biggest_trim")
        if bt:
            lines.append(
                f"               Biggest trim: {bt['ticker']} "
                f"{_fmt_dollar(bt['delta_dollar'])} "
                f"({_fmt_pct_decimal(bt['weight_before'])} -> "
                f"{_fmt_pct_decimal(bt['weight_after'])})"
            )
    lines.append("")

    # ----- Section renders -----
    render_map = [
        ("market_regime", "MACRO REGIME", market_regime.render),
        ("sector_rotation", "SECTOR ROTATION", sector_rotation_signal.render),
        ("historical_analog", "HISTORICAL ANALOGS",
         historical_analog_finder.render),
        ("risk_report", "PORTFOLIO RISK", risk_report.render),
        ("earnings_blackout", "EARNINGS CALENDAR (30d)",
         earnings_blackout.render),
        ("macro_calendar", "MACRO CALENDAR (30d)",
         macro_event_calendar.render),
        ("corporate_actions", "CORPORATE ACTIONS (180d)",
         corporate_actions_scanner.render),
        ("rebalancer", "REBALANCE RECOMMENDATION",
         portfolio_rebalancer.render),
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

    # ----- Errors -----
    errors = payload.get("errors") or []
    if errors:
        lines.append("ERRORS")
        lines.append("─" * 60)
        for e in errors:
            lines.append(f"  {e['section']}: {e['error']}")
        lines.append("")

    return "\n".join(lines)
