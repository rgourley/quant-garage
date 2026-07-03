"""
macro-event-calendar as an importable library function.

Sibling to earnings-blackout. earnings-blackout is single-name and
forward-looking; this is macro-level and covers the release calendar
that reprices the whole book (FOMC, CPI, PPI, NFP, ISM manufacturing/
services, GDP, PCE, JOLTS, jobless claims, retail sales, Consumer
Confidence, Michigan Sentiment).

For each upcoming release, reports the scheduled date + time, the
event type, and the historical average |SPY 1-day move| on that
release type over the last ~24 months. Flags "crowded" windows where
multiple releases cluster on the same day.

    from quant_garage.skills.macro_event_calendar import run, render
    payload = run(window_days=30)

Data provenance:
- Recurring events: date pattern generator (see PATTERN_RULES).
  Approximate: BLS/BEA/ISM release dates follow published patterns
  but the actual dates vary by 1-2 days from the pattern.
- FOMC: hardcoded 2026 meeting schedule (published by the FOMC in
  advance and updated once a year).
- SPY reactions: computed live from Massive daily aggs.

Limitations:
- No prior / consensus values in v1. Add later via FRED integration.
- 2026 schedule only. Regenerate at year-end.
"""
from __future__ import annotations

import calendar
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
)


# ---------- Event metadata ----------

# Each event key maps to a metadata block. impact tiers:
#   very_high — moves SPY 1-2% on average
#   high      — moves SPY 0.6-1% on average
#   medium    — moves SPY 0.3-0.6% on average
#   low       — reference only
EVENT_META: dict[str, dict] = {
    "FOMC": {
        "name": "FOMC Rate Decision",
        "release_time_et": "14:00",
        "impact": "very_high",
        "issuer": "Federal Reserve",
        "pattern": "hardcoded",
    },
    "FOMC_MINUTES": {
        "name": "FOMC Minutes",
        "release_time_et": "14:00",
        "impact": "high",
        "issuer": "Federal Reserve",
        "pattern": "fomc_plus_21_days",
    },
    "CPI": {
        "name": "CPI",
        "release_time_et": "08:30",
        "impact": "very_high",
        "issuer": "BLS",
        "pattern": "nth_weekday",
        "pattern_args": {"n": 2, "weekday": 2},  # 2nd Wednesday, approx
    },
    "PPI": {
        "name": "PPI",
        "release_time_et": "08:30",
        "impact": "medium",
        "issuer": "BLS",
        "pattern": "nth_weekday",
        "pattern_args": {"n": 2, "weekday": 3},  # day after CPI approx
    },
    "NFP": {
        "name": "Non-Farm Payrolls",
        "release_time_et": "08:30",
        "impact": "very_high",
        "issuer": "BLS",
        "pattern": "nth_weekday",
        "pattern_args": {"n": 1, "weekday": 4},  # 1st Friday
    },
    "UNEMPLOYMENT_CLAIMS": {
        "name": "Initial Jobless Claims",
        "release_time_et": "08:30",
        "impact": "medium",
        "issuer": "DOL",
        "pattern": "weekly",
        "pattern_args": {"weekday": 3},  # Every Thursday
    },
    "ISM_MFG": {
        "name": "ISM Manufacturing PMI",
        "release_time_et": "10:00",
        "impact": "high",
        "issuer": "ISM",
        "pattern": "nth_business_day",
        "pattern_args": {"n": 1},
    },
    "ISM_SVC": {
        "name": "ISM Services PMI",
        "release_time_et": "10:00",
        "impact": "high",
        "issuer": "ISM",
        "pattern": "nth_business_day",
        "pattern_args": {"n": 3},
    },
    "RETAIL_SALES": {
        "name": "Retail Sales",
        "release_time_et": "08:30",
        "impact": "high",
        "issuer": "Census Bureau",
        "pattern": "mid_month",
    },
    "GDP": {
        "name": "GDP",
        "release_time_et": "08:30",
        "impact": "high",
        "issuer": "BEA",
        "pattern": "quarterly_advance",
    },
    "PCE": {
        "name": "PCE Price Index",
        "release_time_et": "08:30",
        "impact": "high",
        "issuer": "BEA",
        "pattern": "last_business_day_minus_5",
    },
    "JOLTS": {
        "name": "JOLTS Job Openings",
        "release_time_et": "10:00",
        "impact": "medium",
        "issuer": "BLS",
        "pattern": "nth_business_day",
        "pattern_args": {"n": 7},
    },
    "CONSUMER_CONFIDENCE": {
        "name": "Consumer Confidence",
        "release_time_et": "10:00",
        "impact": "medium",
        "issuer": "Conference Board",
        "pattern": "last_tuesday",
    },
    "MICHIGAN_SENTIMENT_PRELIM": {
        "name": "Michigan Sentiment (Preliminary)",
        "release_time_et": "10:00",
        "impact": "medium",
        "issuer": "University of Michigan",
        "pattern": "nth_weekday",
        "pattern_args": {"n": 2, "weekday": 4},  # 2nd Friday
    },
}


# Hardcoded 2026 FOMC schedule. Verify at year end and update.
# Source: Federal Reserve official calendar.
FOMC_2026_MEETINGS: list[str] = [
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-11-04",
    "2026-12-16",
]


# ---------- Date pattern generators ----------

def _nth_weekday(year: int, month: int, n: int, weekday: int) -> date:
    """nth (1-indexed) weekday of the month. weekday: 0=Mon, 6=Sun."""
    first = date(year, month, 1)
    days_ahead = (weekday - first.weekday()) % 7
    return first + timedelta(days=days_ahead + 7 * (n - 1))


def _nth_business_day(year: int, month: int, n: int) -> date:
    """nth business day of the month (Mon-Fri, ignoring holidays)."""
    d = date(year, month, 1)
    count = 0
    while True:
        if d.weekday() < 5:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
        if d.month != month:
            return date(year, month, calendar.monthrange(year, month)[1])


def _last_tuesday(year: int, month: int) -> date:
    """Last Tuesday of the month."""
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    while last_day.weekday() != 1:
        last_day -= timedelta(days=1)
    return last_day


def _last_business_day_offset(year: int, month: int, offset_back: int) -> date:
    """N business days before the last business day of the month."""
    last = date(year, month, calendar.monthrange(year, month)[1])
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    count = 0
    while count < offset_back:
        last -= timedelta(days=1)
        if last.weekday() < 5:
            count += 1
    return last


def _mid_month(year: int, month: int) -> date:
    """Around the 15th, snap to nearest weekday."""
    d = date(year, month, 15)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _weekly_dates(from_date: date, to_date: date, weekday: int) -> list[date]:
    """All dates in [from, to] matching weekday (0=Mon)."""
    out = []
    d = from_date
    while d.weekday() != weekday:
        d += timedelta(days=1)
        if d > to_date:
            return out
    while d <= to_date:
        out.append(d)
        d += timedelta(days=7)
    return out


def _generate_event_dates(
    event_key: str, from_date: date, to_date: date,
) -> list[date]:
    """Generate expected release dates for an event type over a range."""
    meta = EVENT_META[event_key]
    pattern = meta["pattern"]
    pargs = meta.get("pattern_args", {})
    out: list[date] = []

    if pattern == "hardcoded":
        if event_key == "FOMC":
            for s in FOMC_2026_MEETINGS:
                d = date.fromisoformat(s)
                if from_date <= d <= to_date:
                    out.append(d)
        return out

    if pattern == "fomc_plus_21_days":
        for s in FOMC_2026_MEETINGS:
            d = date.fromisoformat(s) + timedelta(days=21)
            if from_date <= d <= to_date:
                out.append(d)
        return out

    if pattern == "weekly":
        return _weekly_dates(from_date, to_date, pargs["weekday"])

    if pattern == "quarterly_advance":
        # Advance GDP: ~last business day of Jan/Apr/Jul/Oct
        for m in (1, 4, 7, 10):
            for y in (from_date.year, to_date.year):
                d = _last_business_day_offset(y, m, 0)
                # Advance is typically 25-30 days after quarter end
                # Roughly last week of the release month
                if from_date <= d <= to_date:
                    out.append(d)
        return sorted(set(out))

    # Monthly patterns: iterate month by month across the window
    cur_y, cur_m = from_date.year, from_date.month
    end_y, end_m = to_date.year, to_date.month
    while (cur_y, cur_m) <= (end_y, end_m):
        try:
            if pattern == "nth_weekday":
                d = _nth_weekday(cur_y, cur_m, pargs["n"], pargs["weekday"])
            elif pattern == "nth_business_day":
                d = _nth_business_day(cur_y, cur_m, pargs["n"])
            elif pattern == "mid_month":
                d = _mid_month(cur_y, cur_m)
            elif pattern == "last_tuesday":
                d = _last_tuesday(cur_y, cur_m)
            elif pattern == "last_business_day_minus_5":
                d = _last_business_day_offset(cur_y, cur_m, 5)
            else:
                d = None
        except ValueError:
            d = None
        if d and from_date <= d <= to_date:
            out.append(d)
        if cur_m == 12:
            cur_y, cur_m = cur_y + 1, 1
        else:
            cur_m += 1
    return sorted(set(out))


# ---------- SPY reaction ----------

def _fetch_spy_bars(
    client: MassiveClient, benchmark: str, from_date: date, to_date: date,
) -> dict[date, dict]:
    """Return {date: bar_dict} for the benchmark over the range."""
    try:
        body, _ = client.get(
            f"/v2/aggs/ticker/{benchmark}/range/1/day/"
            f"{from_date.isoformat()}/{to_date.isoformat()}",
            {"adjusted": "true", "sort": "asc", "limit": 5000},
        )
    except FetchError:
        return {}
    results = body.get("results") or []
    out: dict[date, dict] = {}
    for b in results:
        d = datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc).date()
        out[d] = b
    return out


def _compute_release_reaction(
    bars_by_date: dict[date, dict], release_date: date,
) -> float | None:
    """|1-day close-to-close SPY move| on the release date, expressed
    as a percentage. Uses the release-day close vs the prior close.
    Returns None if either bar is missing."""
    if release_date not in bars_by_date:
        # Fall through to previous trading day
        return None
    dates_sorted = sorted(bars_by_date.keys())
    try:
        idx = dates_sorted.index(release_date)
    except ValueError:
        return None
    if idx == 0:
        return None
    today_close = bars_by_date[release_date]["c"]
    prior_close = bars_by_date[dates_sorted[idx - 1]]["c"]
    if prior_close == 0:
        return None
    return abs(today_close - prior_close) / prior_close * 100


def _release_reaction_stats(
    bars_by_date: dict[date, dict],
    event_key: str,
    lookback_start: date,
    lookback_end: date,
) -> dict:
    """Compute historical |1-day SPY move| stats for an event type."""
    dates = _generate_event_dates(event_key, lookback_start, lookback_end)
    moves: list[float] = []
    for d in dates:
        m = _compute_release_reaction(bars_by_date, d)
        if m is not None:
            moves.append(m)
    if not moves:
        return {
            "n_samples": 0,
            "mean_abs_move_pct": None,
            "median_abs_move_pct": None,
            "p90_abs_move_pct": None,
        }
    moves_sorted = sorted(moves)
    return {
        "n_samples": len(moves),
        "mean_abs_move_pct": round(sum(moves) / len(moves), 3),
        "median_abs_move_pct": round(moves_sorted[len(moves) // 2], 3),
        "p90_abs_move_pct": round(
            moves_sorted[int(0.9 * len(moves))] if len(moves) > 1
            else moves_sorted[-1], 3
        ),
    }


# ---------- Main entry ----------

def run(
    window_days: int = 30,
    events: Iterable[str] | str | None = None,
    benchmark: str = "SPY",
    history_days: int = 730,
    client: MassiveClient | None = None,
) -> dict:
    """Return the upcoming macro release calendar with historical reactions.

    Args:
        window_days: forward window in calendar days. Default 30.
        events: optional filter to specific event keys (e.g. "FOMC,CPI,NFP").
        benchmark: reaction benchmark. Default SPY.
        history_days: how far back to compute historical |1-day move|
            for each release type. Default 730.
        client: reuse an existing MassiveClient.
    """
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    if history_days < 90:
        raise ValueError("history_days must be >= 90")

    client = client or MassiveClient()
    today_d = today()
    window_end = today_d + timedelta(days=window_days)
    history_start = today_d - timedelta(days=history_days)

    if isinstance(events, str):
        event_filter: set[str] | None = set(
            e.strip().upper() for e in events.split(",") if e.strip()
        )
    elif events is not None:
        event_filter = set(e.strip().upper() for e in events)
    else:
        event_filter = None

    # Fetch SPY bars once
    print(f"Fetching {benchmark} bars for reaction history...", file=sys.stderr)
    bars = _fetch_spy_bars(client, benchmark, history_start, today_d)
    if not bars:
        raise RuntimeError(
            f"failed to fetch {benchmark} history; check MASSIVE_API_KEY"
        )

    # Compute historical reaction stats per event type (cache)
    reaction_stats: dict[str, dict] = {}
    for event_key in EVENT_META:
        if event_filter and event_key not in event_filter:
            continue
        reaction_stats[event_key] = _release_reaction_stats(
            bars, event_key, history_start, today_d
        )

    # Generate upcoming schedule
    upcoming: list[dict] = []
    for event_key, meta in EVENT_META.items():
        if event_filter and event_key not in event_filter:
            continue
        dates = _generate_event_dates(event_key, today_d, window_end)
        for d in dates:
            days_out = (d - today_d).days
            stats = reaction_stats.get(event_key, {})
            upcoming.append({
                "event_key": event_key,
                "event_name": meta["name"],
                "date": d.isoformat(),
                "release_time_et": meta["release_time_et"],
                "days_out": days_out,
                "impact_tier": meta["impact"],
                "issuer": meta["issuer"],
                "historical_mean_abs_spy_move_pct":
                    stats.get("mean_abs_move_pct"),
                "historical_median_abs_spy_move_pct":
                    stats.get("median_abs_move_pct"),
                "historical_p90_abs_spy_move_pct":
                    stats.get("p90_abs_move_pct"),
                "historical_n_samples": stats.get("n_samples"),
                "pattern_derived": meta["pattern"] not in ("hardcoded",),
            })

    upcoming.sort(key=lambda e: (e["date"], -_impact_rank(e["impact_tier"])))

    # Identify crowded days (>= 2 events on the same date)
    by_date: dict[str, list[dict]] = {}
    for e in upcoming:
        by_date.setdefault(e["date"], []).append(e)
    crowded_days = [
        {"date": d, "n_events": len(evs),
         "events": [{"key": e["event_key"], "name": e["event_name"]} for e in evs]}
        for d, evs in by_date.items() if len(evs) >= 2
    ]

    return {
        "scan_params": {
            "window_days": window_days,
            "history_days": history_days,
            "benchmark": benchmark,
            "as_of": today_d.isoformat(),
            "event_filter": (sorted(event_filter) if event_filter else None),
        },
        "upcoming": upcoming,
        "crowded_days": crowded_days,
        "n_events": len(upcoming),
        "n_dates": len({e["date"] for e in upcoming}),
        "generated_at": utcnow_iso(),
        "caveats": [
            "Recurring event dates are pattern-derived (e.g. NFP = 1st "
            "Friday, CPI = 2nd Wednesday). Actual BLS/BEA/ISM dates vary "
            "1-2 days from the pattern. Verify against the official "
            "release calendar before trading around a specific print.",
            "FOMC dates are hardcoded from the 2026 published schedule; "
            "regenerate at year-end.",
            "Historical reactions are unconditional |1-day SPY move|. "
            "Actual reactions are regime-dependent (CPI hits harder in "
            "inflation regimes).",
            "No prior / consensus values in v1. Add via FRED integration "
            "for a future release.",
        ],
    }


def _impact_rank(tier: str) -> int:
    return {"very_high": 4, "high": 3, "medium": 2, "low": 1}.get(tier, 0)


def _impact_label(tier: str) -> str:
    return {
        "very_high": "★★★★",
        "high":      "★★★",
        "medium":    "★★",
        "low":       "★",
    }.get(tier, "?")


def render(payload: dict) -> str:
    params = payload["scan_params"]
    upcoming = payload["upcoming"]
    crowded = payload.get("crowded_days") or []
    lines: list[str] = []

    lines.append(
        f"Macro Event Calendar — {params['as_of']}\n"
        f"Forward window: {params['window_days']}d · "
        f"Benchmark {params['benchmark']} · "
        f"Historical reactions: {params['history_days']}d lookback"
    )
    lines.append("")

    if not upcoming:
        lines.append("No macro events in the window (or filter excluded all).")
        return "\n".join(lines)

    lines.append(
        f"{payload['n_events']} events across {payload['n_dates']} dates"
    )
    lines.append("")

    # Header row
    lines.append(
        f"{'Date':<12}{'ET':>7}  {'Event':<28}"
        f"{'Impact':>7}  {'Mean|Δ|':>8}  {'Median':>7}  {'p90':>6}  {'n':>4}"
    )
    lines.append("-" * 84)

    for e in upcoming:
        d = e["date"]
        t = e["release_time_et"]
        name = e["event_name"][:28]
        impact = _impact_label(e["impact_tier"])
        mean_m = e.get("historical_mean_abs_spy_move_pct")
        med_m = e.get("historical_median_abs_spy_move_pct")
        p90 = e.get("historical_p90_abs_spy_move_pct")
        n = e.get("historical_n_samples") or 0
        mean_s = f"{mean_m:.2f}%" if mean_m is not None else "n/a"
        med_s = f"{med_m:.2f}%" if med_m is not None else "n/a"
        p90_s = f"{p90:.2f}%" if p90 is not None else "n/a"
        marker = "~" if e.get("pattern_derived") else " "
        lines.append(
            f"{d}{marker}{t:>6}  {name:<28}{impact:>7}  "
            f"{mean_s:>8}  {med_s:>7}  {p90_s:>6}  {n:>4}"
        )

    lines.append("")
    lines.append("~ = pattern-derived date; verify against official calendar")

    if crowded:
        lines.append("")
        lines.append(f"Crowded days ({len(crowded)}):")
        for cd in crowded:
            names = ", ".join(e["name"] for e in cd["events"])
            lines.append(f"  {cd['date']}: {cd['n_events']} events — {names}")

    lines.append("")
    lines.append("Caveats:")
    for c in payload.get("caveats", []):
        lines.append(f"- {c}")

    return "\n".join(lines)
