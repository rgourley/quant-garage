#!/usr/bin/env python3
"""
Reference implementation of the rate-signal skill.

The rates-only deep dive. macro-basket is the broad cross-asset dashboard
(rates, credit, dollar, gold, commodities); rate-signal zooms into the
Treasury curve alone and decomposes it the way a rates desk does:

  - curve slope / 2s10s proxy   (SHY vs TLT, four-way bull/bear label)
  - real yields                 (TIP vs IEF)
  - break-evens (inflation)     (IEF vs TIP, nominal minus real)
  - momentum divergence         (TLT vs IEF, long end vs belly)

It pulls four liquid Treasury ETFs (SHY short end, IEF belly, TLT long
end, TIP inflation-protected), computes each read over the window, and
resolves the four into a single four-way curve label plus a confidence
score. Confidence is high when the curve label, the real-yield read, and
the momentum read agree, low when they conflict.

The take line reads like a one-sentence rates note:
"Bear flattening (high confidence): long rates rising faster than short,
real yields rising, TLT and IEF aligned."

Two output layers:
  Layer 1: canonical JSON matching skills/rate-signal/output-schema.json
  Layer 2: rendered instruments table + signals block + take

Usage:
    python3 examples/run-rate-signal.py
    python3 examples/run-rate-signal.py --window 90 --format both
    python3 examples/run-rate-signal.py --sleep 13   # Free Basic tier

Reads MASSIVE_API_KEY from env. Runs on any stocks tier (all four are
US-listed ETFs); on Free Basic use --sleep 13 to stay under the
5-calls/min cap on the 4-series pull.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import timezone, timedelta

# Make `lib.quant_garage` importable when running from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    FetchError,
    RateLimited,
    today,
    utcnow_iso,
    resolve_output_format,
    emit_to_stdout,
)

client = MassiveClient()
TODAY = today()

# The rates basket: four liquid, US-listed Treasury ETF proxies spanning
# the curve plus the inflation-protected leg.
BASKET: dict[str, str] = {
    "SHY": "1-3 Year Treasuries (short end)",
    "IEF": "7-10 Year Treasuries (belly)",
    "TLT": "20+ Year Treasuries (long end)",
    "TIP": "TIPS (inflation-protected)",
}

_AGGS_CACHE: dict[str, list[dict]] = {}
_RATE_LIMITED: set[str] = set()
_SLEEP_BETWEEN: float = 0.0
_RATE_LIMIT_COOLDOWN_SECONDS = 13

# Label thresholds (see references/methodology.md).
# Real yields come from TIP alone (absolute return), so the band is wider
# than the relative-return breakeven band to filter single-leg noise.
_REAL_YIELD_THRESH_BPS = 50.0
_BREAKEVEN_THRESH_BPS = 25.0
_MOMENTUM_NEUTRAL_BPS = 10.0


# ----- HTTP -----

def fetch_daily_aggs(ticker: str, calendar_days: int) -> list[dict]:
    """Daily {date, close} records for `ticker`, ascending. Cached per run.

    Detects rate limits specifically (they are FetchError subclasses that
    otherwise look like "no data"), cools down once, retries, and flags the
    ticker so the caller can caveat loudly rather than silently returning [].
    """
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]

    end = TODAY
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    try:
        doc, _ = client.get(path, params)
    except RateLimited:
        print(
            f"  WARN: rate limited on {ticker}; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            doc, _ = client.get(path, params)
        except FetchError as exc:
            print(f"  WARN: still failing for {ticker} after cooldown: {exc}",
                  file=sys.stderr)
            _RATE_LIMITED.add(ticker)
            _AGGS_CACHE[ticker] = []
            return []
    except FetchError as exc:
        print(f"  WARN: aggs for {ticker}: {exc}", file=sys.stderr)
        _AGGS_CACHE[ticker] = []
        return []
    finally:
        if _SLEEP_BETWEEN > 0:
            time.sleep(_SLEEP_BETWEEN)

    rows: list[dict] = []
    for r in doc.get("results") or []:
        ts_ms, close = r.get("t"), r.get("c")
        if ts_ms is None or close is None:
            continue
        from datetime import datetime as _dt
        d = _dt.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    _AGGS_CACHE[ticker] = rows
    return rows


# ----- Math helpers -----

def window_return(rows: list[dict], window_days: int) -> tuple[float | None, int]:
    """Total return over the last `window_days` trading bars."""
    if len(rows) < window_days + 1:
        return (None, len(rows))
    last = rows[-1]["close"]
    prior = rows[-(window_days + 1)]["close"]
    if prior <= 0 or last <= 0:
        return (None, len(rows))
    return ((last / prior) - 1.0, window_days)


def rel_return_bps(a: list[dict], b: list[dict], window: int) -> float | None:
    """(a_return - b_return) over `window` bars, in basis points."""
    ra, _ = window_return(a, window)
    rb, _ = window_return(b, window)
    if ra is None or rb is None:
        return None
    return (ra - rb) * 10_000.0


def _sign(x: float | None, neutral: float) -> int:
    """Sign of x with a neutral band: +1, -1, or 0."""
    if x is None:
        return 0
    if x > neutral:
        return 1
    if x < -neutral:
        return -1
    return 0


# ----- Derived signals -----

def compute_curve(rows: dict[str, list[dict]], window: int) -> dict:
    """Four-way bull/bear steepening/flattening label from SHY vs TLT.

    Short (SHY) outperforming long (TLT) means long yields rose more than
    short: steepening. Long outperforming short: flattening. TLT up means
    long rates falling (bull); TLT down means rising (bear).
    """
    tlt_ret, _ = window_return(rows.get("TLT", []), window)
    shy_vs_tlt = rel_return_bps(rows.get("SHY", []), rows.get("TLT", []), window)
    if shy_vs_tlt is None or tlt_ret is None:
        return {
            "label": "unknown",
            "rate_direction": "unknown",
            "slope": "unknown",
            "shy_minus_tlt_bps": round(shy_vs_tlt, 1) if shy_vs_tlt is not None else None,
            "tlt_return": round(tlt_ret, 4) if tlt_ret is not None else None,
            "window_days": window,
        }
    steepening = shy_vs_tlt > 0
    falling = tlt_ret > 0
    if falling and not steepening:
        label = "bull flattening"
    elif falling and steepening:
        label = "bull steepening"
    elif not falling and not steepening:
        label = "bear flattening"
    else:
        label = "bear steepening"
    return {
        "label": label,
        "rate_direction": "bull" if falling else "bear",
        "slope": "steepening" if steepening else "flattening",
        "shy_minus_tlt_bps": round(shy_vs_tlt, 1),
        "tlt_return": round(tlt_ret, 4),
        "window_days": window,
    }


def compute_real_yield(rows: dict[str, list[dict]], window: int) -> dict:
    """Real-yield direction from TIP alone.

    TIP is indexed to real yields, so its price moves inversely with the real
    rate: TIP up means real yields falling, TIP down means real yields rising.
    This isolates the real leg cleanly. Breakevens (the inflation leg) are a
    separate signal computed from TIP vs IEF, so the two reads are distinct
    rather than mirror images of the same spread.
    """
    tip_ret, _ = window_return(rows.get("TIP", []), window)
    tip_ret_bps = tip_ret * 10_000.0 if tip_ret is not None else None
    s = _sign(tip_ret_bps, _REAL_YIELD_THRESH_BPS)
    # TIP up (s=+1) => real yields FALLING; TIP down (s=-1) => rising.
    direction = {1: "falling", -1: "rising", 0: "stable"}.get(s, "unknown")
    if tip_ret is None:
        direction = "unknown"
    return {
        "label": direction,
        "direction": direction,
        "tip_return": round(tip_ret, 4) if tip_ret is not None else None,
        "window_days": window,
    }


def compute_breakeven(rows: dict[str, list[dict]], window: int) -> dict:
    """Inflation-expectations proxy: breakevens = nominal minus real (TIP vs IEF).

    Breakeven inflation is the nominal yield (IEF) minus the real yield (TIP).
    In return terms the change in breakevens is proportional to
    (TIP_return - IEF_return): TIP outperforming IEF means the real leg
    rallied relative to the nominal leg, i.e. breakevens widened and inflation
    expectations rose. IEF outperforming TIP means expectations fell.
    """
    tip_vs_ief = rel_return_bps(rows.get("TIP", []), rows.get("IEF", []), window)
    s = _sign(tip_vs_ief, _BREAKEVEN_THRESH_BPS)
    label = {
        1: "inflation expectations rising",
        -1: "inflation expectations falling",
        0: "inflation expectations stable",
    }.get(s, "unknown")
    if tip_vs_ief is None:
        label = "unknown"
    return {
        "label": label,
        "tip_minus_ief_bps": round(tip_vs_ief, 1) if tip_vs_ief is not None else None,
        "window_days": window,
    }


def compute_momentum(rows: dict[str, list[dict]], window: int, short_window: int) -> dict:
    """Long end (TLT) vs belly (IEF) momentum divergence.

    Compares the short sub-window direction of TLT and IEF. Aligned when
    both move the same way (curve shifting in parallel); divergent when the
    long end and the belly disagree, which muddies any curve read.
    """
    tlt_short, _ = window_return(rows.get("TLT", []), short_window)
    ief_short, _ = window_return(rows.get("IEF", []), short_window)
    tlt_long, _ = window_return(rows.get("TLT", []), window)
    ief_long, _ = window_return(rows.get("IEF", []), window)
    if tlt_short is None or ief_short is None:
        return {
            "label": "unknown",
            "tlt_short_return": round(tlt_short, 4) if tlt_short is not None else None,
            "ief_short_return": round(ief_short, 4) if ief_short is not None else None,
            "tlt_minus_ief_short_bps": None,
            "short_window_days": short_window,
            "long_window_days": window,
        }
    diff_bps = (tlt_short - ief_short) * 10_000.0
    st = _sign(tlt_short, 0.0)
    si = _sign(ief_short, 0.0)
    aligned = st == si and st != 0
    label = "aligned" if aligned else "divergent"
    return {
        "label": label,
        "tlt_short_return": round(tlt_short, 4),
        "ief_short_return": round(ief_short, 4),
        "tlt_long_return": round(tlt_long, 4) if tlt_long is not None else None,
        "ief_long_return": round(ief_long, 4) if ief_long is not None else None,
        "tlt_minus_ief_short_bps": round(diff_bps, 1),
        "short_window_days": short_window,
        "long_window_days": window,
    }


def compute_confidence(curve: dict, real_yield: dict, momentum: dict) -> dict:
    """Confidence in the curve read from cross-signal agreement.

    High when the real-yield read and the momentum read both agree with the
    curve's rate direction; low when they conflict; medium in between. In a
    bull (rates falling) regime real yields typically fall; in a bear (rates
    rising) regime they rise. Divergent long-end/belly momentum is a conflict.
    """
    agreements: list[str] = []
    conflicts: list[str] = []

    rate_dir = curve.get("rate_direction")
    ry_dir = real_yield.get("direction")
    if rate_dir in ("bull", "bear") and ry_dir in ("rising", "falling"):
        expected = "falling" if rate_dir == "bull" else "rising"
        if ry_dir == expected:
            agreements.append(
                f"real yields {ry_dir} consistent with {rate_dir} regime"
            )
        else:
            conflicts.append(
                f"real yields {ry_dir} conflict with {rate_dir} regime"
            )

    mom = momentum.get("label")
    if mom == "aligned":
        agreements.append("TLT and IEF momentum aligned")
    elif mom == "divergent":
        conflicts.append("TLT and IEF momentum divergent")

    if not conflicts and len(agreements) >= 2:
        level = "high"
    elif len(conflicts) >= 2 or (conflicts and not agreements):
        level = "low"
    else:
        level = "medium"

    if curve.get("label") == "unknown":
        level = "unknown"

    return {"level": level, "agreements": agreements, "conflicts": conflicts}


def compose_take(curve: dict, real_yield: dict, momentum: dict, confidence: dict) -> str:
    """One-sentence rates note from the curve label and confidence read."""
    label = curve.get("label", "unknown")
    if label == "unknown":
        return (
            "Curve read unavailable (insufficient history). See macro-basket "
            "for the broad cross-asset read."
        )
    curve_phrase = {
        "bear steepening": "long rates rising faster than short",
        "bear flattening": "long rates rising, curve flattening",
        "bull steepening": "rates falling, curve steepening",
        "bull flattening": "long rates falling faster than short",
    }.get(label, label)
    ry = real_yield.get("direction", "unknown")
    ry_phrase = {
        "rising": "real yields rising",
        "falling": "real yields falling",
        "stable": "real yields stable",
    }.get(ry, "real yields unclear")
    mom_phrase = {
        "aligned": "TLT and IEF aligned",
        "divergent": "TLT and IEF diverging",
    }.get(momentum.get("label"), "momentum unclear")
    headline = label[0].upper() + label[1:]
    return (
        f"{headline} ({confidence['level']} confidence): {curve_phrase}, "
        f"{ry_phrase}, {mom_phrase}. See macro-basket for the broad "
        f"cross-asset read."
    )


# ----- CLI -----

def build_payload(args: argparse.Namespace) -> dict:
    global _SLEEP_BETWEEN
    if args.sleep and args.sleep > 0:
        _SLEEP_BETWEEN = args.sleep

    window = int(args.window)
    if window <= 1:
        raise SystemExit("--window must be > 1")
    short_window = max(5, window // 4)
    calendar_days = int(max(window, 252) * 1.6) + 14

    rows: dict[str, list[dict]] = {}
    sources: list[dict] = []
    for t in BASKET:
        print(f"  Pulling daily aggs for {t}...", file=sys.stderr)
        rows[t] = fetch_daily_aggs(t, calendar_days)
        sources.append({
            "endpoint": f"/v2/aggs/ticker/{t}/range/1/day/{{from}}/{{to}}",
            "fetched_at": utcnow_iso(),
            "context": f"daily closes for {t}",
        })

    instruments: list[dict] = []
    for t, label in BASKET.items():
        tr = rows.get(t, [])
        ret, n = window_return(tr, window)
        instruments.append({
            "ticker": t,
            "label": label,
            "return_pct": round(ret, 4) if ret is not None else None,
            "n_obs": int(n),
        })

    curve = compute_curve(rows, window)
    real_yield = compute_real_yield(rows, window)
    breakeven = compute_breakeven(rows, window)
    momentum = compute_momentum(rows, window, short_window)
    confidence = compute_confidence(curve, real_yield, momentum)
    take = compose_take(curve, real_yield, momentum, confidence)

    tier_caveats: list[str] = []
    if _RATE_LIMITED:
        tier_caveats.append(
            f"RATE LIMIT: {len(_RATE_LIMITED)} series "
            f"({', '.join(sorted(_RATE_LIMITED))}) returned no data because the "
            f"API rate limit was hit, not because history is missing. Any signal "
            f"that uses them is UNKNOWN, not zero. Free Basic tier caps at 5 "
            f"calls/min: rerun with --sleep 13 or upgrade to Stocks Starter."
        )
    missing = [t for t in BASKET if not rows.get(t)]
    if missing:
        tier_caveats.append(
            f"Missing history for {', '.join(missing)}; signals that use them "
            f"are reported as unknown."
        )
    tier_caveats.append(
        "Every read is an ETF-return proxy, not the cash curve. SHY/IEF/TLT/TIP "
        "returns move inversely with yields but carry duration, roll, and "
        "tracking error. Directionally right, not a substitute for the actual "
        "2s10s in basis points."
    )

    return {
        "skill": "rate-signal",
        "as_of": TODAY.isoformat(),
        "fetched_at": utcnow_iso(),
        "window_days": window,
        "short_window_days": short_window,
        "n_instruments": len(BASKET),
        "instruments": instruments,
        "signals": {
            "curve": curve,
            "real_yield": real_yield,
            "breakeven": breakeven,
            "momentum": momentum,
        },
        "confidence": confidence,
        "take": take,
        "tier_caveats": tier_caveats,
        "sources": sources,
    }


# ----- Renderer -----

def render(payload: dict) -> str:
    lines: list[str] = []
    lines.append(f"Rate Signal (Treasury curve) - {payload['as_of']}")
    lines.append("")
    lines.append(
        f"{'Instrument':<34}{'Return':>10}{'Obs':>6}"
    )
    lines.append("-" * 50)
    for inst in payload["instruments"]:
        r = inst["return_pct"]
        r_s = f"{r * 100:+.2f}%" if r is not None else "n/a"
        name = f"{inst['ticker']} {inst['label']}"[:33]
        lines.append(f"{name:<34}{r_s:>10}{inst['n_obs']:>6}")

    s = payload["signals"]
    w = payload["window_days"]
    lines.append("")
    lines.append(f"Signals ({w}d window):")
    lines.append(f"  Curve:      {s['curve']['label']}")
    lines.append(f"  Real yield: {s['real_yield']['label']}")
    lines.append(f"  Breakevens: {s['breakeven']['label']}")
    lines.append(
        f"  Momentum:   {s['momentum']['label']} "
        f"(TLT vs IEF, {s['momentum']['short_window_days']}d)"
    )

    conf = payload["confidence"]
    lines.append("")
    lines.append(f"Confidence: {conf['level']}")
    for a in conf.get("agreements", []):
        lines.append(f"  + {a}")
    for c in conf.get("conflicts", []):
        lines.append(f"  - {c}")

    lines.append("")
    lines.append("Take: " + payload["take"])

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"  - {c}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="rate-signal reference")
    ap.add_argument("--window", type=int, default=60,
                    help="Lookback window in trading days for the curve reads. "
                         "Default 60.")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between aggregate calls. Free Basic caps at 5 "
                         "calls/min and this pulls 4 series; use --sleep 13.")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT.")
    args = ap.parse_args()
    if args.sleep < 0:
        raise SystemExit("--sleep cannot be negative")

    fmt = resolve_output_format(args.format)
    payload = build_payload(args)
    emit_to_stdout(render(payload), payload, fmt)


if __name__ == "__main__":
    main()
