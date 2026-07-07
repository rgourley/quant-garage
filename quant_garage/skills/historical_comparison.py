"""
historical-comparison: twin decision-support.

Chains event-study (what happened around a specific event) with
historical-analog-finder (what usually happens in setups like now).
The idea: use both name-specific event evidence and market-wide
regime analog as anchors before making a call.

Two modes:
- Event mode: pass ticker + event_class + optional event_date to
  study a specific event's reaction.
- Analog-only: skip the event side, just report the market analog
  distribution.

    from quant_garage.skills.historical_comparison import run, render
    payload = run(ticker="NVDA", event_class="earnings", period="most_recent")
"""
from __future__ import annotations

import sys

from .. import MassiveClient, today, utcnow_iso
from . import event_study, historical_analog_finder


def run(
    ticker: str | None = None,
    event_class: str = "earnings",
    event_date: str | None = None,
    period: str | None = None,
    analog_k: int = 20,
    analog_horizons_days: list[int] | None = None,
    include_event: bool = True,
    client: MassiveClient | None = None,
) -> dict:
    """Twin comparison: event-study + historical-analog-finder.

    Args:
        ticker: subject ticker for the event side. Required if include_event.
        event_class: earnings, dividend_changes, or large_volume_spike.
        event_date: YYYY-MM-DD for a specific single event.
        period: 'most_recent' picks the most recent event of that class.
        analog_k: nearest-neighbor count for the market analog. Default 20.
        analog_horizons_days: forward horizons for the analog forecast.
            Default [30, 60, 90, 252].
        include_event: if False, only run the analog side.
        client: reuse an existing MassiveClient.
    """
    if analog_horizons_days is None:
        analog_horizons_days = [30, 60, 90, 252]
    if include_event and not ticker:
        raise ValueError("ticker is required when include_event=True")

    client = client or MassiveClient()
    sections: dict = {}
    errors: list[dict] = []

    def _try(name, fn):
        try:
            sections[name] = fn()
        except Exception as exc:
            errors.append({"section": name, "error": str(exc)})

    # 1) Event side
    if include_event and ticker:
        ticker = ticker.strip().upper()
        print(f"[1/2] event-study {ticker} · {event_class}...", file=sys.stderr)
        _try("event_study", lambda: event_study.run(
            ticker=ticker, event_class=event_class,
            event_date=event_date, period=period, client_=client,
        ))
    else:
        sections["event_study"] = None

    # 2) Analog side (market-wide, not ticker-specific)
    print("[2/2] historical-analog-finder...", file=sys.stderr)
    _try("historical_analog", lambda: historical_analog_finder.run(
        k=analog_k, horizon_days=analog_horizons_days, client=client,
    ))

    headline = _build_headline(sections, ticker, event_class, analog_horizons_days)

    return {
        "scan_params": {
            "ticker": (ticker.strip().upper() if ticker else None),
            "event_class": event_class,
            "event_date": event_date,
            "period": period,
            "analog_k": analog_k,
            "analog_horizons_days": analog_horizons_days,
            "include_event": include_event,
            "as_of": today().isoformat(),
        },
        "headline": headline,
        "sections": sections,
        "errors": errors,
        "generated_at": utcnow_iso(),
    }


def _build_headline(
    sections: dict, ticker: str | None,
    event_class: str, horizons: list[int],
) -> dict:
    hl: dict = {
        "event_summary": None,
        "analog_read": None,
    }

    es = sections.get("event_study")
    if es and ticker:
        # event-study payload shape varies by mode; try single-event first
        single = es.get("event") or {}
        car5 = single.get("car_t5_pct")
        prior_mean = ((es.get("historical") or {})
                       .get("mean_car_t5_pct"))
        prior_pct = ((es.get("historical") or {}).get("percentile_of_current"))
        if car5 is not None:
            hl["event_summary"] = {
                "ticker": ticker,
                "class": event_class,
                "event_date": single.get("event_date"),
                "car_t5_pct": car5,
                "prior_mean_car_t5_pct": prior_mean,
                "percentile_of_current": prior_pct,
            }

    ha = sections.get("historical_analog")
    if ha:
        dists = ha.get("forward_return_distributions") or {}
        # Prefer 90d, fall back to first horizon
        h = 90 if 90 in horizons else (horizons[0] if horizons else None)
        if h is not None:
            row = dists.get(f"{h}d") or {}
            hl["analog_read"] = {
                "horizon_days": h,
                "n_analogs": ha.get("n_analogs"),
                "median": row.get("median"),
                "p25": row.get("p25"),
                "p75": row.get("p75"),
                "hit_rate_above_zero": row.get("hit_rate_above_zero"),
            }

    return hl


def _fmt_pct_decimal(x, decimals=1):
    if x is None:
        return "n/a"
    return f"{x * 100:+.{decimals}f}%"


def render(payload: dict) -> str:
    params = payload["scan_params"]
    hl = payload["headline"]
    sections = payload["sections"]
    lines: list[str] = []

    label = (f"{params['ticker']} · " if params["ticker"] else "")
    lines.append(f"Historical Comparison — {label}{params['as_of']}")
    lines.append("")

    lines.append("HEADLINE")
    lines.append("─" * 60)
    if hl.get("event_summary"):
        es = hl["event_summary"]
        car = es.get("car_t5_pct")
        prior_mean = es.get("prior_mean_car_t5_pct")
        prior_pct = es.get("percentile_of_current")
        car_str = f"{car:+.1f}%" if car is not None else "n/a"
        prior_str = (
            f"prior mean {prior_mean:+.1f}%"
            if prior_mean is not None else "no prior mean"
        )
        pct_str = (
            f", {prior_pct:.0f}th %ile" if prior_pct is not None else ""
        )
        lines.append(
            f"Event ({es['class']}, {es.get('event_date')}): "
            f"T+5 CAR {car_str} ({prior_str}{pct_str})"
        )
    if hl.get("analog_read"):
        ar = hl["analog_read"]
        median = ar.get("median")
        p25 = ar.get("p25")
        p75 = ar.get("p75")
        hit = ar.get("hit_rate_above_zero")
        median_str = _fmt_pct_decimal(median)
        iqr_str = (
            f"[{_fmt_pct_decimal(p25)}, {_fmt_pct_decimal(p75)}]"
            if p25 is not None and p75 is not None else ""
        )
        hit_str = f", {hit * 100:.0f}% > 0" if hit is not None else ""
        lines.append(
            f"Analog {ar['horizon_days']}d SPY: median {median_str}"
            f"{hit_str} across {ar.get('n_analogs')} analogs"
            + (f" · IQR {iqr_str}" if iqr_str else "")
        )
    lines.append("")

    render_map = [
        ("event_study", "EVENT STUDY", event_study.render),
        ("historical_analog", "HISTORICAL ANALOGS",
         historical_analog_finder.render),
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
