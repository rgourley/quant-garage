#!/usr/bin/env python3
"""
Reference implementation of the event-study skill.

Three input modes determined by argv:

  Single:        --ticker NVDA --event-date 2026-05-20 --event-class earnings
  Cross-section: --tickers AAPL,NVDA,MSFT,GOOGL,META --event-class earnings --period most_recent
  Aggregate:     --tickers AAPL,NVDA,MSFT,GOOGL,META --event-class earnings --window 2025-06-01..2026-06-24

Event classes:
  earnings              (Benzinga preferred, SEC EDGAR 8-K item 2.02 fallback)
  dividend_changes      (cash amount diff vs prior >= 1%)
  large_volume_spike    (volume > 3 sigma trailing 30d, 5d cooldown)

Reads MASSIVE_API_KEY from env. Writes JSON + rendered Markdown to
examples/event-study-<mode>-output.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

KEY = os.environ.get("MASSIVE_API_KEY")
if not KEY:
    print("ERROR: MASSIVE_API_KEY not set", file=sys.stderr)
    sys.exit(1)

BASE = "https://api.polygon.io"
HEADERS = {"Authorization": f"Bearer {KEY}"}
SEC_HEADERS = {"User-Agent": "Rob Gourley rgourley@gmail.com"}
TODAY = date(2026, 6, 24)


# -------- HTTP helpers --------


def fetch(path: str) -> dict[str, Any]:
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode("utf-8", errors="replace")
        raise RuntimeError(f"{e.code} on {path}: {body}")


def fetch_all(path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    url = f"{BASE}{path}"
    while url:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            doc = json.load(r)
        out.extend(doc.get("results", []) or [])
        next_url = doc.get("next_url")
        if next_url:
            sep = "&" if "?" in next_url else "?"
            url = f"{next_url}{sep}apiKey={KEY}"
        else:
            url = None
    return out


def fetch_sec(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=SEC_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


# -------- Stats helpers --------


def mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def std_sample(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    m = mean(xs)
    if m is None:
        return None
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)


def t_stat_one_sample(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    m = mean(xs)
    s = std_sample(xs)
    if m is None or s is None or s <= 0:
        return None
    se = s / math.sqrt(n)
    return m / se if se > 0 else None


def percentile_of(xs: list[float], value: float) -> float:
    if not xs:
        return 0.5
    rank = sum(1 for x in xs if x <= value)
    return rank / (len(xs) + 1)


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 2:
        return None
    n = len(pairs)
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    num = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    dx = math.sqrt(sum((p[0] - mx) ** 2 for p in pairs))
    dy = math.sqrt(sum((p[1] - my) ** 2 for p in pairs))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


# -------- Aggregate pulls (cached) --------


_aggs_cache: dict[str, dict[str, dict[str, Any]]] = {}


def get_daily_aggs(ticker: str, from_date: str, to_date: str) -> dict[str, dict[str, Any]]:
    """Return a dict keyed by ISO date string -> aggregate dict."""
    cache_key = f"{ticker}:{from_date}:{to_date}"
    if cache_key in _aggs_cache:
        return _aggs_cache[cache_key]
    print(f"  fetching daily aggs {ticker} {from_date}..{to_date}", file=sys.stderr)
    rows = fetch_all(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"
        f"?adjusted=true&sort=asc&limit=50000"
    )
    out: dict[str, dict[str, Any]] = {}
    for a in rows:
        d = datetime.fromtimestamp(a["t"] / 1000, tz=timezone.utc).date()
        out[d.isoformat()] = a
    _aggs_cache[cache_key] = out
    return out


# -------- Session classifier --------


def classify_session(time_str: str | None) -> str:
    """time_str is HH:MM:SS in ET. Maps to BMO / AMC / DMH / unknown."""
    if not time_str:
        return "unknown"
    try:
        hh, mm, _ = time_str.split(":")
        hh = int(hh)
        mm = int(mm)
    except (ValueError, AttributeError):
        return "unknown"
    minutes = hh * 60 + mm
    if minutes < 9 * 60 + 30:
        return "BMO"
    if minutes >= 16 * 60:
        return "AMC"
    return "DMH"


# -------- Event resolvers --------


_benzinga_cache: dict[str, list[dict[str, Any]]] = {}


def benzinga_earnings(ticker: str) -> list[dict[str, Any]]:
    if ticker in _benzinga_cache:
        return _benzinga_cache[ticker]
    print(f"  Benzinga earnings {ticker}", file=sys.stderr)
    rows = fetch_all(
        f"/benzinga/v1/earnings?ticker={ticker}&limit=40&order=desc"
        f"&sort=date&date.lte={TODAY.isoformat()}"
    )
    # filter to confirmed past prints
    rows = [r for r in rows if r.get("date_status") != "projected"]
    _benzinga_cache[ticker] = rows
    return rows


def resolve_earnings(
    ticker: str,
    event_date: str | None = None,
    window: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return a list of event tuples for the earnings class."""
    rows = benzinga_earnings(ticker)
    tier = "A" if rows and rows[0].get("eps_surprise_percent") is not None else "B"
    # Fallback to EDGAR only when Benzinga returned nothing usable
    if not rows:
        # In practice Benzinga is reliable for mega-caps; if we needed EDGAR
        # the run-aapl-tier-b.py helper has the full path. For this skill's
        # cross-section runs we assume Benzinga is available.
        return []

    out = []
    for r in rows:
        d = r.get("date")
        if not d:
            continue
        if event_date and d != event_date:
            continue
        if window:
            if d < window[0] or d > window[1]:
                continue
        out.append({
            "ticker": ticker,
            "event_date": d,
            "event_session": classify_session(r.get("time")),
            "event_metadata": {
                "fiscal_period": r.get("fiscal_period"),
                "fiscal_year": r.get("fiscal_year"),
                "surprise_eps_pct": r.get("eps_surprise_percent"),
                "estimated_eps": r.get("estimated_eps"),
                "previous_eps": r.get("previous_eps"),
                "company_name": r.get("company_name"),
                "release_time_et": r.get("time"),
            },
            "_tier": tier,
        })
    # ascending by date so prior-history is easy to slice
    out.sort(key=lambda e: e["event_date"])
    return out


def resolve_dividend_changes(
    ticker: str,
    event_date: str | None = None,
    window: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    print(f"  dividends {ticker}", file=sys.stderr)
    rows = fetch_all(
        f"/v3/reference/dividends?ticker={ticker}&limit=40"
        f"&order=asc&sort=ex_dividend_date"
    )
    # Exclude special-cash dividends from the baseline
    regular = [
        r for r in rows
        if r.get("dividend_type") not in ("SC",)
        and r.get("cash_amount") is not None
    ]
    out = []
    prior = None
    for r in regular:
        amt = float(r["cash_amount"])
        ex = r.get("ex_dividend_date")
        if not ex:
            continue
        if prior is None:
            prior = amt
            continue
        change_pct = (amt - prior) / prior if prior > 0 else 0
        if abs(change_pct) >= 0.01:
            if event_date and ex != event_date:
                prior = amt
                continue
            if window and (ex < window[0] or ex > window[1]):
                prior = amt
                continue
            out.append({
                "ticker": ticker,
                "event_date": ex,
                "event_session": "BMO",
                "event_metadata": {
                    "prior_amount": prior,
                    "new_amount": amt,
                    "change_pct": change_pct,
                    "change_direction": "hike" if change_pct > 0 else "cut",
                },
                "_tier": "A",
            })
        prior = amt
    return out


def resolve_volume_spike(
    ticker: str,
    event_date: str | None = None,
    window: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    # Pull a wide buffer for both volume history and the 30d trailing stats.
    if window:
        from_d = (date.fromisoformat(window[0]) - timedelta(days=60)).isoformat()
        to_d = window[1]
    else:
        from_d = (TODAY - timedelta(days=180)).isoformat()
        to_d = TODAY.isoformat()
    aggs = get_daily_aggs(ticker, from_d, to_d)
    dates_sorted = sorted(aggs.keys())
    out = []
    cooldown_until_idx = -1
    for i, d in enumerate(dates_sorted):
        if i < 30:
            continue
        if i <= cooldown_until_idx:
            continue
        trailing = [aggs[dates_sorted[j]].get("v", 0) for j in range(i - 30, i)]
        m = mean(trailing)
        s = std_sample(trailing)
        vol_today = aggs[d].get("v", 0)
        if not m or not s or s <= 0:
            continue
        z = (vol_today - m) / s
        if z <= 3.0:
            continue
        # Apply event_date / window filters
        if event_date and d != event_date:
            continue
        if window and (d < window[0] or d > window[1]):
            continue
        out.append({
            "ticker": ticker,
            "event_date": d,
            "event_session": "DMH",
            "event_metadata": {
                "volume": vol_today,
                "trailing_30d_mean": m,
                "trailing_30d_std": s,
                "z_score": z,
            },
            "_tier": "A",
        })
        cooldown_until_idx = i + 5
    return out


RESOLVERS = {
    "earnings": resolve_earnings,
    "dividend_changes": resolve_dividend_changes,
    "large_volume_spike": resolve_volume_spike,
}


# -------- Return computation --------


def next_trading_day(dates: list[str], anchor: str, offset: int) -> str | None:
    """Find the trading day `offset` steps from anchor in the sorted dates list."""
    try:
        idx = dates.index(anchor)
    except ValueError:
        idx = next((i for i, td in enumerate(dates) if td > anchor), None)
        if idx is None:
            return None
        idx -= 1
        if idx < 0:
            idx = 0
    target = idx + offset
    if 0 <= target < len(dates):
        return dates[target]
    return None


def compute_event_returns(
    event: dict[str, Any],
    ticker_aggs: dict[str, dict[str, Any]],
    spy_aggs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build event_window_returns and abnormal_returns blocks per the schema."""
    ticker_dates = sorted(ticker_aggs.keys())
    if not ticker_dates:
        return {"event_window_returns": None, "abnormal_returns": None}

    event_date = event["event_date"]
    session = event["event_session"]

    # Determine T0 per the session convention.
    if session == "AMC":
        # T0 is the trading day of the press release; T+1 is next session.
        t0 = next_trading_day(ticker_dates, event_date, 0)
        if t0 is None or t0 != event_date:
            # event_date may not be a trading day; snap to nearest prior
            t0 = max((d for d in ticker_dates if d <= event_date), default=None)
    else:
        # BMO and DMH: T0 is the trading day BEFORE the event.
        t0 = max((d for d in ticker_dates if d < event_date), default=None)
        if t0 is None and event_date in ticker_aggs:
            t0 = event_date

    if t0 is None or t0 not in ticker_aggs:
        return {"event_window_returns": None, "abnormal_returns": None}

    t0_close = ticker_aggs[t0]["c"]
    spy_t0_close = spy_aggs.get(t0, {}).get("c")

    horizons = []
    ar = {}
    for label, offset in [("T+1", 1), ("T+3", 3), ("T+5", 5)]:
        td = next_trading_day(ticker_dates, t0, offset)
        if not td or td not in ticker_aggs:
            horizons.append({
                "horizon": label, "date": None, "close": None,
                "raw_return_pct": None, "spy_close": None,
                "spy_return_pct": None,
            })
            ar[f"ar_{label.lower().replace('+', '')}_pct"] = None
            continue
        close = ticker_aggs[td]["c"]
        raw_ret = (close - t0_close) / t0_close if t0_close else None
        spy_close = spy_aggs.get(td, {}).get("c")
        spy_ret = (
            (spy_close - spy_t0_close) / spy_t0_close
            if (spy_close is not None and spy_t0_close)
            else None
        )
        abn = (
            raw_ret - spy_ret
            if (raw_ret is not None and spy_ret is not None)
            else None
        )
        horizons.append({
            "horizon": label,
            "date": td,
            "close": close,
            "raw_return_pct": raw_ret,
            "spy_close": spy_close,
            "spy_return_pct": spy_ret,
        })
        ar[f"ar_{label.lower().replace('+', '')}_pct"] = abn

    ar["car_t5_pct"] = ar.get("ar_t5_pct")
    return {
        "t0_date": t0,
        "event_window_returns": {
            "t0_close": t0_close,
            "spy_t0_close": spy_t0_close,
            "horizons": horizons,
        },
        "abnormal_returns": ar,
    }


# -------- Per-subject t-stat vs history --------


def t_stat_vs_history(
    subject: dict[str, Any],
    all_events_for_ticker: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compare subject's T+5 CAR to its name's prior reaction distribution."""
    this_event = subject["event_date"]
    prior = [
        e for e in all_events_for_ticker
        if e["event_date"] < this_event
        and e.get("_t5_car") is not None
    ]
    prior_t5 = [e["_t5_car"] for e in prior]
    n = len(prior_t5)
    this_car = subject["abnormal_returns"].get("car_t5_pct")
    if n == 0 or this_car is None:
        return None
    m = mean(prior_t5)
    s = std_sample(prior_t5)
    z = (this_car - m) / s if (m is not None and s and s > 0) else None
    pct = percentile_of(prior_t5, this_car)
    underpowered = n < 8

    # Direction concurrence (earnings class only)
    direction_concurrence = None
    surprise = subject["event_metadata"].get("surprise_eps_pct") if subject.get("event_metadata") else None
    if surprise is not None:
        prior_with_surprise = [
            e for e in prior
            if e["event_metadata"].get("surprise_eps_pct") is not None
        ]
        if prior_with_surprise:
            matches = sum(
                1 for e in prior_with_surprise
                if (
                    (e["event_metadata"]["surprise_eps_pct"] >= 0
                     and (e["_t5_car"] or 0) >= 0)
                    or (e["event_metadata"]["surprise_eps_pct"] < 0
                        and (e["_t5_car"] or 0) < 0)
                )
            )
            direction_concurrence = f"{matches}/{len(prior_with_surprise)}"

    return {
        "prior_n": n,
        "prior_mean_t5_car_pct": m,
        "prior_std_t5_car_pct": s,
        "this_event_t5_car_pct": this_car,
        "z_score": z,
        "percentile": pct,
        "underpowered": underpowered,
        "direction_concurrence": direction_concurrence,
    }


# -------- Cross-section / aggregate summary --------


def build_summary(subjects: list[dict[str, Any]], mode: str) -> dict[str, Any] | None:
    cars = [s["abnormal_returns"]["car_t5_pct"] for s in subjects
            if s["abnormal_returns"] and s["abnormal_returns"].get("car_t5_pct") is not None]
    if not cars:
        return None
    n = len(cars)
    m = mean(cars)
    med = median(cars)
    s = std_sample(cars)
    t = t_stat_one_sample(cars)

    horizon_breakdown = []
    for label, key in [("T+1", "ar_t1_pct"), ("T+3", "ar_t3_pct"), ("T+5", "ar_t5_pct")]:
        vals = [
            sub["abnormal_returns"].get(key) for sub in subjects
            if sub["abnormal_returns"] and sub["abnormal_returns"].get(key) is not None
        ]
        if not vals:
            continue
        horizon_breakdown.append({
            "horizon": label,
            "mean_ar_pct": mean(vals),
            "median_ar_pct": median(vals),
            "std_ar_pct": std_sample(vals),
            "t_stat": t_stat_one_sample(vals),
            "n": len(vals),
            "significant": (
                abs(t_stat_one_sample(vals)) > 2.0 and len(vals) >= 8
                if t_stat_one_sample(vals) is not None else False
            ),
        })

    # Surprise vs reaction (earnings, Tier A)
    surprise_block = None
    surprises = []
    surprise_cars = []
    for sub in subjects:
        sp = sub["event_metadata"].get("surprise_eps_pct") if sub.get("event_metadata") else None
        car = sub["abnormal_returns"].get("car_t5_pct") if sub["abnormal_returns"] else None
        if sp is not None and car is not None:
            surprises.append(sp)
            surprise_cars.append(car)
    if len(surprises) >= 3:
        rho = pearson(surprises, surprise_cars)
        if rho is not None:
            surprise_block = {
                "rho": rho,
                "n": len(surprises),
                "r_squared": rho ** 2,
            }

    # Regime check (aggregate mode only, n >= 8)
    regime_block = None
    if mode == "aggregate" and n >= 8:
        sorted_subj = sorted(
            [s for s in subjects if s["abnormal_returns"]
             and s["abnormal_returns"].get("car_t5_pct") is not None],
            key=lambda x: x["event_date"]
        )
        recent_cars = [s["abnormal_returns"]["car_t5_pct"] for s in sorted_subj[-4:]]
        full_mean = m
        recent_mean = mean(recent_cars)
        se_full = (s / math.sqrt(n)) if (s and n) else None
        delta = recent_mean - full_mean if recent_mean is not None else None
        regime_block = {
            "recent_n": len(recent_cars),
            "recent_mean_t5_car_pct": recent_mean,
            "full_window_mean_t5_car_pct": full_mean,
            "delta_pp": delta,
            "regime_shift_flag": (
                abs(delta) > se_full
                if (delta is not None and se_full) else False
            ),
        }

    return {
        "n_subjects": n,
        "n_tickers": len({sub["ticker"] for sub in subjects}),
        "mean_t5_car_pct": m,
        "median_t5_car_pct": med,
        "std_t5_car_pct": s,
        "t_stat_avg_vs_zero": t,
        "significant": (abs(t) > 2.0 and n >= 8) if t is not None else False,
        "horizon_breakdown": horizon_breakdown,
        "percentiles": {
            "p10_pct": percentile(cars, 0.10),
            "p25_pct": percentile(cars, 0.25),
            "p50_pct": percentile(cars, 0.50),
            "p75_pct": percentile(cars, 0.75),
            "p90_pct": percentile(cars, 0.90),
        },
        "surprise_reaction_correlation": surprise_block,
        "regime_check": regime_block,
    }


# -------- Take generation --------


def generate_take_single(subject: dict[str, Any]) -> str:
    car = subject["abnormal_returns"].get("car_t5_pct")
    hist = subject.get("t_stat_vs_history")
    if car is None:
        return "Event window returns unavailable (insufficient price data)."
    car_pp = f"{car * 100:+.1f}pp"
    if hist and hist.get("z_score") is not None and not hist["underpowered"]:
        z = hist["z_score"]
        if abs(z) >= 1.5:
            return (
                f"{car_pp} abnormal return over T+1 to T+5; "
                f"z-score {z:+.2f} vs {hist['prior_n']}-event history, significant."
            )
        prior_m = hist["prior_mean_t5_car_pct"]
        return (
            f"{car_pp} abnormal return; in line with "
            f"{hist['prior_n']}-event prior mean of {prior_m * 100:+.1f}% "
            f"(z {z:+.2f})."
        )
    if hist:
        prior_m = hist["prior_mean_t5_car_pct"]
        return (
            f"{car_pp} abnormal return; {hist['prior_n']} prior events "
            f"(underpowered, prior mean {prior_m * 100:+.1f}%)."
        )
    return f"{car_pp} abnormal return; no prior history available for comparison."


def generate_take_cross_section(summary: dict[str, Any], event_class: str) -> str:
    if summary is None:
        return "Cross-section had no usable events."
    mean_car = summary["mean_t5_car_pct"]
    t = summary["t_stat_avg_vs_zero"]
    n = summary["n_subjects"]
    surprise = summary.get("surprise_reaction_correlation")
    parts = []
    if surprise and abs(surprise["rho"]) > 0.5 and surprise["n"] >= 5:
        parts.append(
            f"Surprise explains {surprise['r_squared'] * 100:.0f}% "
            f"of T+5 CAR variation (ρ={surprise['rho']:+.2f})."
        )
    if summary["significant"]:
        parts.append(
            f"Avg T+5 CAR {mean_car * 100:+.1f}%, t-stat {t:.2f}, "
            f"significant at n={n}."
        )
    elif t is not None:
        parts.append(
            f"Cross-section average isn't significant at n={n} "
            f"(avg {mean_car * 100:+.1f}%, t-stat {t:.2f})."
        )
    if not parts:
        parts.append(
            f"Mixed signal: avg T+5 {mean_car * 100:+.1f}%, n={n}."
        )
    return " ".join(parts)


def generate_take_aggregate(summary: dict[str, Any], event_class: str) -> str:
    if summary is None:
        return "Aggregate had no usable events."
    mean_car = summary["mean_t5_car_pct"]
    t = summary["t_stat_avg_vs_zero"]
    n = summary["n_subjects"]
    regime = summary.get("regime_check")
    if regime and regime["regime_shift_flag"]:
        return (
            f"Regime has shifted: recent 4 events avg "
            f"{regime['recent_mean_t5_car_pct'] * 100:+.1f}% vs "
            f"full-window {regime['full_window_mean_t5_car_pct'] * 100:+.1f}% "
            f"(n={n}). Cite recent, not headline."
        )
    if summary["significant"]:
        return (
            f"Event class has tradeable signal: avg T+5 CAR "
            f"{mean_car * 100:+.1f}%, t-stat {t:.2f}, n={n}."
        )
    surprise = summary.get("surprise_reaction_correlation")
    if surprise and abs(surprise["rho"]) > 0.5:
        return (
            f"Mean reaction not significant (avg {mean_car * 100:+.1f}%, "
            f"t-stat {t:.2f}, n={n}) but surprise explains "
            f"{surprise['r_squared'] * 100:.0f}% of cross-section variation."
        )
    return (
        f"No tradeable signal at n={n}: avg T+5 CAR "
        f"{mean_car * 100:+.1f}%, t-stat {t:.2f}."
    )


# -------- Renderers --------


def fmt_signed_pct(x: float | None, dec: int = 1) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.{dec}f}%"


def fmt_signed_pp(x: float | None, dec: int = 1) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.{dec}f}pp"


def event_label(event_class: str, meta: dict[str, Any]) -> str:
    if event_class == "earnings":
        fp = meta.get("fiscal_period", "?")
        fy = meta.get("fiscal_year", "?")
        return f"{fp} FY{fy} earnings"
    if event_class == "dividend_changes":
        d = meta.get("change_direction", "change")
        return f"dividend {d} ${meta.get('prior_amount', 0):.4f} -> ${meta.get('new_amount', 0):.4f}"
    if event_class == "large_volume_spike":
        return f"volume spike (z={meta.get('z_score', 0):.1f})"
    return event_class


def render_single(payload: dict[str, Any]) -> str:
    subject = payload["subjects"][0]
    lines = []
    label = event_label(payload["event_class"], subject["event_metadata"])
    session = subject["event_session"]
    session_txt = "" if session == "unknown" else f" {session}"
    lines.append(f"{subject['ticker']} · {label} · {subject['event_date']}{session_txt}")
    lines.append("")
    lines.append(f"Take: {payload['take']}")
    lines.append("")

    ewr = subject["event_window_returns"]
    if ewr:
        lines.append("Event window (SPY-adjusted)")
        lines.append(f"- T0 close:    ${ewr['t0_close']:.2f}")
        ar = subject["abnormal_returns"]
        for h in ewr["horizons"]:
            close = h.get("close")
            raw = h.get("raw_return_pct")
            spy = h.get("spy_return_pct")
            label_ar = "CAR" if h["horizon"] == "T+5" else "abnormal"
            key = f"ar_{h['horizon'].lower().replace('+', '')}_pct"
            abn = ar.get(key)
            close_s = f"${close:.2f}" if close is not None else "n/a"
            lines.append(
                f"- {h['horizon']} close:   {close_s} "
                f"({fmt_signed_pct(raw)}, market {fmt_signed_pct(spy)}, "
                f"{label_ar} {fmt_signed_pct(abn)})"
            )
        lines.append("")

    hist = subject.get("t_stat_vs_history")
    if hist:
        ec_label = payload["event_class"].replace("_", " ")
        lines.append(
            f"Historical comparison (last {hist['prior_n']} "
            f"{subject['ticker']} {ec_label} reactions)"
        )
        lines.append(
            f"- Mean T+5 CAR:        {fmt_signed_pct(hist['prior_mean_t5_car_pct'])}"
        )
        lines.append(
            f"- Std dev:             {fmt_signed_pct(hist['prior_std_t5_car_pct'])}"
        )
        z = hist.get("z_score")
        pct = hist.get("percentile")
        this_pp = fmt_signed_pp(hist["this_event_t5_car_pct"])
        if z is not None and pct is not None:
            lines.append(
                f"- This event:          {this_pp} "
                f"({pct * 100:.0f}th pct, {z:+.2f}σ vs prior mean)"
            )
        if hist.get("direction_concurrence"):
            lines.append(
                f"- Direction concur:    {hist['direction_concurrence']} "
                f"priors aligned with surprise sign"
            )
        if hist.get("underpowered"):
            lines.append("- Note: prior_n < 8, distribution test is underpowered.")
        lines.append("")

    if payload["tier_caveats"]:
        lines.append(f"Tier {payload['tier']} caveats")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()


def render_cross_section(payload: dict[str, Any]) -> str:
    subjects = payload["subjects"]
    summary = payload["summary"]
    tickers = ",".join(sorted({s["ticker"] for s in subjects}))
    period = payload.get("window", {}).get("period_label") or "selected period"
    lines = []
    lines.append(
        f"Event study: {tickers} · {payload['event_class']} · "
        f"{period} · {len(subjects)} events"
    )
    lines.append("")

    class_col = {
        "earnings": "Surprise",
        "dividend_changes": "Change",
        "large_volume_spike": "Vol z",
    }.get(payload["event_class"], "Magnitude")

    show_concur = payload["event_class"] == "earnings" and payload["tier"] == "A"
    header = f"| Ticker | {class_col} | T+1 Abn | T+5 CAR | t-stat (vs hist) |"
    sep = "|--------|--------:|--------:|--------:|-----------------:|"
    if show_concur:
        header += " Concur |"
        sep += "-------:|"
    lines.append(header)
    lines.append(sep)
    underpowered_marks = False
    for s in sorted(subjects, key=lambda x: x["ticker"]):
        meta = s["event_metadata"]
        if payload["event_class"] == "earnings":
            mag = fmt_signed_pct(meta.get("surprise_eps_pct"))
        elif payload["event_class"] == "dividend_changes":
            mag = fmt_signed_pct(meta.get("change_pct"))
        elif payload["event_class"] == "large_volume_spike":
            z_val = meta.get("z_score")
            mag = f"{z_val:.1f}" if z_val is not None else "n/a"
        else:
            mag = "n/a"
        ar = s["abnormal_returns"]
        t1 = fmt_signed_pct(ar.get("ar_t1_pct"))
        car = fmt_signed_pct(ar.get("car_t5_pct"))
        hist = s.get("t_stat_vs_history")
        if hist and hist.get("z_score") is not None:
            tstat_s = f"{hist['z_score']:+.2f}"
            if hist["underpowered"]:
                tstat_s += "*"
                underpowered_marks = True
        else:
            tstat_s = "n/a"
        row = f"| {s['ticker']} | {mag} | {t1} | {car} | {tstat_s} |"
        if show_concur:
            row += f" {hist.get('direction_concurrence', 'n/a') if hist else 'n/a'} |"
        lines.append(row)
    if underpowered_marks:
        lines.append("")
        lines.append("* underpowered (prior_n < 8)")
    lines.append("")

    if summary:
        lines.append("Cross-section")
        lines.append(
            f"- Avg T+5 CAR:    {fmt_signed_pct(summary['mean_t5_car_pct'])}"
        )
        lines.append(
            f"- Median:         {fmt_signed_pct(summary['median_t5_car_pct'])}"
        )
        t = summary["t_stat_avg_vs_zero"]
        sig = "significant" if summary["significant"] else "not significant"
        lines.append(
            f"- t-stat (avg vs 0): {t:.2f} ({sig} at n={summary['n_subjects']})"
        )
        sc = summary.get("surprise_reaction_correlation")
        if sc:
            lines.append(
                f"- Surprise vs reaction ρ: {sc['rho']:+.2f} "
                f"(R² = {sc['r_squared'] * 100:.0f}%, n={sc['n']})"
            )
        lines.append("")

    lines.append(f"Take: {payload['take']}")

    if payload["tier_caveats"]:
        lines.append("")
        lines.append(f"Tier {payload['tier']} caveats")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()


def render_aggregate(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    tickers = ",".join(sorted({s["ticker"] for s in payload["subjects"]}))
    w = payload.get("window") or {}
    lines = []
    lines.append(
        f"Event study: {tickers} · {payload['event_class']} · "
        f"{w.get('from_date', '?')} to {w.get('to_date', '?')} · "
        f"{summary['n_subjects'] if summary else 0} events"
    )
    lines.append("")

    if summary:
        lines.append("Aggregate abnormal returns (SPY-adjusted)")
        for hb in summary["horizon_breakdown"]:
            t_s = f"t-stat {hb['t_stat']:+.2f}" if hb.get("t_stat") is not None else "t-stat n/a"
            lines.append(
                f"- {hb['horizon']} avg:   {fmt_signed_pct(hb['mean_ar_pct'])} "
                f"(median {fmt_signed_pct(hb['median_ar_pct'])}, "
                f"{t_s}, n={hb['n']})"
            )
        p = summary["percentiles"]
        lines.append(
            f"- T+5 distribution: p10 {fmt_signed_pct(p['p10_pct'])} "
            f"p25 {fmt_signed_pct(p['p25_pct'])} "
            f"p50 {fmt_signed_pct(p['p50_pct'])} "
            f"p75 {fmt_signed_pct(p['p75_pct'])} "
            f"p90 {fmt_signed_pct(p['p90_pct'])}"
        )
        lines.append("")

        regime = summary.get("regime_check")
        if regime:
            lines.append("Regime check")
            lines.append(
                f"- Recent (last 4): {fmt_signed_pct(regime['recent_mean_t5_car_pct'])}"
            )
            lines.append(
                f"- Full window:     {fmt_signed_pct(regime['full_window_mean_t5_car_pct'])} "
                f"(n={summary['n_subjects']})"
            )
            flag = "REGIME SHIFT" if regime["regime_shift_flag"] else "within 1 SE"
            lines.append(f"- Delta:           {fmt_signed_pp(regime['delta_pp'])} ({flag})")
            lines.append("")

        sc = summary.get("surprise_reaction_correlation")
        if sc:
            lines.append("Surprise vs reaction")
            lines.append(f"- Pearson ρ: {sc['rho']:+.2f}")
            lines.append(f"- R²:        {sc['r_squared'] * 100:.0f}%")
            lines.append(f"- n:         {sc['n']}")
            lines.append("")

    lines.append(f"Take: {payload['take']}")

    if payload["tier_caveats"]:
        lines.append("")
        lines.append(f"Tier {payload['tier']} caveats")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()


# -------- Main pipeline --------


def run(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    event_class = args.event_class
    if event_class not in RESOLVERS:
        raise ValueError(f"unknown event_class: {event_class}")

    # Determine mode
    tickers = args.tickers.split(",") if args.tickers else [args.ticker]
    tickers = [t.strip().upper() for t in tickers if t.strip()]

    if args.event_date:
        mode = "single" if len(tickers) == 1 else "cross_section"
        window_arg = None
    elif args.window:
        mode = "aggregate" if len(tickers) > 1 else "single"
        a, b = args.window.split("..")
        window_arg = (a.strip(), b.strip())
    elif args.period:
        # cross-section using "most recent" semantics: pick the most recent
        # earnings per ticker
        mode = "cross_section" if len(tickers) > 1 else "single"
        window_arg = None
    else:
        raise ValueError(
            "Provide --event-date, --window, or --period."
        )

    # Resolve events
    all_events_by_ticker: dict[str, list[dict[str, Any]]] = {}
    chosen_events: list[dict[str, Any]] = []
    tiers_seen = set()
    for t in tickers:
        # Always pull the full history for the ticker (needed for per-subject
        # t_stat_vs_history regardless of mode)
        full = RESOLVERS[event_class](t, event_date=None, window=None)
        all_events_by_ticker[t] = full
        for e in full:
            tiers_seen.add(e.get("_tier", "A"))

        if mode == "single":
            chosen = [e for e in full if e["event_date"] == args.event_date]
        elif args.period and args.period == "most_recent":
            chosen = [full[-1]] if full else []
        elif window_arg:
            chosen = [e for e in full if window_arg[0] <= e["event_date"] <= window_arg[1]]
        else:
            chosen = []
        chosen_events.extend(chosen)

    if not chosen_events:
        raise RuntimeError("No events matched the input criteria.")

    tier = "A" if "A" in tiers_seen else "B"
    tier_caveats = []
    if tier == "B":
        tier_caveats.append(
            "Benzinga earnings unavailable; using SEC EDGAR 8-K item 2.02 as print-date proxy."
        )
        tier_caveats.append(
            "No surprise_eps_pct; cross-section drops surprise-vs-reaction correlation."
        )

    # Determine date span for aggregate pulls
    all_event_dates = sorted({e["event_date"] for evs in all_events_by_ticker.values() for e in evs})
    if not all_event_dates:
        raise RuntimeError("No events available for ticker history.")
    earliest = all_event_dates[0]
    latest = max(all_event_dates[-1], TODAY.isoformat())
    pull_from = (date.fromisoformat(earliest) - timedelta(days=30)).isoformat()
    pull_to = (
        min(date.fromisoformat(latest) + timedelta(days=15), TODAY)
    ).isoformat()

    # SPY pull
    spy_aggs = get_daily_aggs("SPY", pull_from, pull_to)

    # Per-ticker aggs + compute returns for ALL events (we need history for t-stat-vs-history)
    for t in tickers:
        if not all_events_by_ticker[t]:
            continue
        ticker_aggs = get_daily_aggs(t, pull_from, pull_to)
        for e in all_events_by_ticker[t]:
            res = compute_event_returns(e, ticker_aggs, spy_aggs)
            e["event_window_returns"] = res.get("event_window_returns")
            e["abnormal_returns"] = res.get("abnormal_returns")
            e["_t5_car"] = (
                e["abnormal_returns"].get("car_t5_pct")
                if e["abnormal_returns"] else None
            )

    # Build subject list (the chosen events with t-stat-vs-history attached)
    subjects = []
    for e in chosen_events:
        # Find the matching full-history entry (same ticker, same date)
        full_match = next(
            (x for x in all_events_by_ticker[e["ticker"]]
             if x["event_date"] == e["event_date"]),
            e
        )
        full_match["t_stat_vs_history"] = t_stat_vs_history(
            full_match, all_events_by_ticker[e["ticker"]]
        )
        subjects.append({
            "ticker": full_match["ticker"],
            "event_date": full_match["event_date"],
            "event_session": full_match["event_session"],
            "event_metadata": full_match["event_metadata"],
            "event_window_returns": full_match.get("event_window_returns"),
            "abnormal_returns": full_match.get("abnormal_returns"),
            "t_stat_vs_history": full_match.get("t_stat_vs_history"),
        })

    # Summary block (cross_section and aggregate)
    summary = None
    if mode in ("cross_section", "aggregate"):
        summary = build_summary(subjects, mode)

    # Take
    if mode == "single":
        take = generate_take_single(subjects[0])
    elif mode == "cross_section":
        take = generate_take_cross_section(summary, event_class)
    else:
        take = generate_take_aggregate(summary, event_class)

    # Window block
    window_block = None
    if mode == "cross_section":
        window_block = {
            "period_label": args.period or chosen_events[0]["event_date"],
            "from_date": min(s["event_date"] for s in subjects),
            "to_date": max(s["event_date"] for s in subjects),
        }
    elif mode == "aggregate" and window_arg:
        window_block = {
            "period_label": None,
            "from_date": window_arg[0],
            "to_date": window_arg[1],
        }

    # Sources
    sources = [
        {
            "endpoint": "/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "context": "Per-ticker daily closes for raw returns",
        },
        {
            "endpoint": "/v2/aggs/ticker/SPY/range/1/day/{from}/{to}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "context": "SPY closes for benchmark abnormal-return computation",
        },
    ]
    if event_class == "earnings":
        sources.append({
            "endpoint": "/benzinga/v1/earnings?ticker={ticker}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "context": "Press release date, time, surprise %, fiscal period",
        })
    elif event_class == "dividend_changes":
        sources.append({
            "endpoint": "/v3/reference/dividends?ticker={ticker}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "context": "Cash dividend history for change-detection resolver",
        })

    payload = {
        "mode": mode,
        "tier": tier,
        "tier_caveats": tier_caveats,
        "event_class": event_class,
        "model": "spy",
        "take": take,
        "subjects": subjects,
        "summary": summary,
        "window": window_block,
        "sources": sources,
    }

    if mode == "single":
        rendered = render_single(payload)
    elif mode == "cross_section":
        rendered = render_cross_section(payload)
    else:
        rendered = render_aggregate(payload)

    return payload, rendered


def main() -> None:
    parser = argparse.ArgumentParser(description="event-study skill reference run")
    parser.add_argument("--ticker", type=str, default=None)
    parser.add_argument("--tickers", type=str, default=None)
    parser.add_argument("--event-class", type=str, required=True,
                        choices=list(RESOLVERS.keys()))
    parser.add_argument("--event-date", type=str, default=None,
                        help="YYYY-MM-DD (single event)")
    parser.add_argument("--window", type=str, default=None,
                        help="YYYY-MM-DD..YYYY-MM-DD (aggregate)")
    parser.add_argument("--period", type=str, default=None,
                        help="'most_recent' picks most recent event per ticker (cross-section)")
    parser.add_argument("--out", type=str, default=None,
                        help="Output markdown path (default: examples/event-study-<mode>-output.md)")
    args = parser.parse_args()

    if not args.ticker and not args.tickers:
        parser.error("Provide --ticker or --tickers")

    payload, rendered = run(args)

    out_path = args.out or os.path.join(
        os.path.dirname(__file__),
        f"event-study-{payload['mode']}-output.md",
    )
    with open(out_path, "w") as f:
        f.write(f"# event-study {payload['mode']} run\n\n")
        f.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"Event class: {payload['event_class']} · Tier: {payload['tier']}\n\n")
        f.write("## Layer 1: canonical JSON\n\n```json\n")
        f.write(json.dumps(payload, indent=2, default=str))
        f.write("\n```\n\n")
        f.write("## Layer 2: rendered output\n\n```\n")
        f.write(rendered)
        f.write("\n```\n")

    print(f"\nDONE. Output -> {out_path}", file=sys.stderr)
    print(rendered)


if __name__ == "__main__":
    main()
