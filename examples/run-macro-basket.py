#!/usr/bin/env python3
"""
Reference implementation of the macro-basket skill.

The cross-asset companion to market-regime. market-regime reads equities
(SPY trend, breadth, sector leadership). macro-basket reads everything
else the tape is pricing: rates, credit, the dollar, gold, and broad
commodities. It pulls a fixed basket of liquid macro ETFs, ranks each by
relative strength versus SPY across several windows, and derives the
handful of cross-asset signals a macro desk actually watches:

  - rates direction        (TLT: long-duration Treasuries)
  - curve shape            (SHY vs TLT: 2s10s proxy)
  - real yields            (TIP vs IEF)
  - credit stress          (HYG vs LQD)
  - dollar direction       (UUP)
  - commodity carry        (DBC)
  - gold/silver ratio      (GLD vs SLV: risk appetite in metals)
  - gold vs dollar beta    (GLD vs UUP, rolling 60d)

The take line reads like a one-sentence macro summary:
"Tightening rates, strong dollar, tight credit, commodity carry off."

Two output layers:
  Layer 1: canonical JSON matching skills/macro-basket/output-schema.json
  Layer 2: rendered table (instruments) + a derived-signals block + take

Usage:
    python3 examples/run-macro-basket.py
    python3 examples/run-macro-basket.py --windows 5,20,60,120 --format both
    python3 examples/run-macro-basket.py --sleep 13   # Free Basic tier

Reads MASSIVE_API_KEY from env. Runs on any stocks tier (the basket is
all US-listed ETFs); on Free Basic use --sleep 13 to stay under the
5-calls/min cap on the 12-series pull.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import timezone, timedelta

import numpy as np

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
    percentile_rank,
)

client = MassiveClient()
TODAY = today()

# The macro basket: liquid, US-listed ETF proxies for each asset class.
BASKET: dict[str, str] = {
    "TLT": "20+ Year Treasuries (long duration)",
    "IEF": "7-10 Year Treasuries (belly)",
    "SHY": "1-3 Year Treasuries (short end)",
    "HYG": "High Yield Credit",
    "LQD": "Investment Grade Credit",
    "TIP": "TIPS (inflation-protected)",
    "BND": "Total Bond Market",
    "GLD": "Gold",
    "SLV": "Silver",
    "UUP": "US Dollar Index (bullish)",
    "DBC": "Broad Commodities",
}

_AGGS_CACHE: dict[str, list[dict]] = {}
_RATE_LIMITED: set[str] = set()
_SLEEP_BETWEEN: float = 0.0
_RATE_LIMIT_COOLDOWN_SECONDS = 13


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


def curve_position(rows: list[dict], lookback: int) -> float | None:
    """Percentile (0-100) of the latest close within its trailing window.

    A cheap read on where an instrument sits in its own recent range: 90
    means near the top of the lookback, 10 near the bottom.
    """
    if len(rows) < 20:
        return None
    closes = [r["close"] for r in rows[-lookback:]]
    return percentile_rank(closes[-1], closes)


def aligned_returns(a: list[dict], b: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Daily simple returns of a and b over their common dates, aligned."""
    amap = {r["date"]: r["close"] for r in a}
    bmap = {r["date"]: r["close"] for r in b}
    common = sorted(set(amap) & set(bmap))
    if len(common) < 3:
        return np.array([]), np.array([])
    av = np.array([amap[d] for d in common], dtype=float)
    bv = np.array([bmap[d] for d in common], dtype=float)
    ra = np.diff(av) / av[:-1]
    rb = np.diff(bv) / bv[:-1]
    return ra, rb


def trend_label(rs_by_window: dict[int, float | None]) -> str:
    """Five-bucket trend from RS across windows (shared with relative-strength)."""
    keys = sorted(rs_by_window.keys())
    vals = [rs_by_window[k] for k in keys]
    if any(v is None for v in vals) or len(vals) < 2:
        return "mixed"
    head = vals[: min(3, len(vals))]
    improving = all(head[i] > head[i + 1] for i in range(len(head) - 1))
    deteriorating = all(head[i] < head[i + 1] for i in range(len(head) - 1))
    all_pos = all(v > 0 for v in vals)
    all_neg = all(v < 0 for v in vals)
    if improving and not all_neg:
        return "improving"
    if deteriorating and not all_pos:
        return "deteriorating"
    if all_pos:
        return "stable_leader"
    if all_neg:
        return "stable_laggard"
    return "mixed"


def rel_return_bps(a: list[dict], b: list[dict], window: int) -> float | None:
    """(a_return - b_return) over `window` bars, in basis points."""
    ra, _ = window_return(a, window)
    rb, _ = window_return(b, window)
    if ra is None or rb is None:
        return None
    return (ra - rb) * 10_000.0


# ----- Derived signals -----

def sign_label(x: float | None, up: str, down: str, flat: str, thresh: float) -> str:
    if x is None:
        return "unknown"
    if x > thresh:
        return up
    if x < -thresh:
        return down
    return flat


def compute_derived_signals(rows: dict[str, list[dict]], signal_window: int) -> dict:
    """The cross-asset reads a macro desk watches, from specific ETF pairs."""
    sig: dict = {}

    # Rates direction: TLT up => long rates falling (easing), TLT down => rising.
    tlt_ret, _ = window_return(rows.get("TLT", []), signal_window)
    sig["rates"] = {
        "label": sign_label(tlt_ret, "easing (long rates falling)",
                            "tightening (long rates rising)", "stable", 0.01),
        "tlt_return": round(tlt_ret, 4) if tlt_ret is not None else None,
        "window_days": signal_window,
    }

    # Curve: SHY (short) vs TLT (long). Short outperforming long => long yields
    # rose more => steepening; long outperforming => flattening. Combined with
    # the rates direction gives the bull/bear steepening/flattening taxonomy.
    shy_vs_tlt = rel_return_bps(rows.get("SHY", []), rows.get("TLT", []), signal_window)
    if shy_vs_tlt is None or tlt_ret is None:
        curve_label = "unknown"
    else:
        steepening = shy_vs_tlt > 0
        falling = tlt_ret > 0
        if falling and not steepening:
            curve_label = "bull flattening"
        elif falling and steepening:
            curve_label = "bull steepening"
        elif not falling and not steepening:
            curve_label = "bear flattening"
        else:
            curve_label = "bear steepening"
    sig["curve"] = {
        "label": curve_label,
        "shy_minus_tlt_bps": round(shy_vs_tlt, 1) if shy_vs_tlt is not None else None,
        "window_days": signal_window,
    }

    # Real yields: TIP vs IEF. TIP outperforming => real yields falling or
    # breakevens widening (inflation expectations up); IEF outperforming =>
    # real yields rising.
    tip_vs_ief = rel_return_bps(rows.get("TIP", []), rows.get("IEF", []), signal_window)
    sig["real_yield"] = {
        "label": sign_label(tip_vs_ief, "falling (or breakevens widening)",
                            "rising", "stable", 25.0),
        "tip_minus_ief_bps": round(tip_vs_ief, 1) if tip_vs_ief is not None else None,
        "window_days": signal_window,
    }

    # Credit: HYG vs LQD. HY underperforming IG => spreads widening => stress.
    hyg_vs_lqd = rel_return_bps(rows.get("HYG", []), rows.get("LQD", []), signal_window)
    sig["credit"] = {
        "label": sign_label(hyg_vs_lqd, "tight (risk appetite)",
                            "widening (stress)", "stable", 25.0),
        "hyg_minus_lqd_bps": round(hyg_vs_lqd, 1) if hyg_vs_lqd is not None else None,
        "window_days": signal_window,
    }

    # Dollar: UUP direction.
    uup_ret, _ = window_return(rows.get("UUP", []), signal_window)
    sig["dollar"] = {
        "label": sign_label(uup_ret, "strong", "weak", "stable", 0.01),
        "uup_return": round(uup_ret, 4) if uup_ret is not None else None,
        "window_days": signal_window,
    }

    # Commodity carry: DBC direction.
    dbc_ret, _ = window_return(rows.get("DBC", []), signal_window)
    sig["commodity"] = {
        "label": sign_label(dbc_ret, "carry on (rising)", "carry off (falling)",
                            "stable", 0.01),
        "dbc_return": round(dbc_ret, 4) if dbc_ret is not None else None,
        "window_days": signal_window,
    }

    # Gold/silver ratio: rising ratio = gold leading silver = defensive metals.
    gld = rows.get("GLD", [])
    slv = rows.get("SLV", [])
    if gld and slv and gld[-1]["close"] > 0 and slv[-1]["close"] > 0:
        ratio_series = []
        gmap = {r["date"]: r["close"] for r in gld}
        smap = {r["date"]: r["close"] for r in slv}
        for d in sorted(set(gmap) & set(smap)):
            if smap[d] > 0:
                ratio_series.append(gmap[d] / smap[d])
        current_ratio = ratio_series[-1] if ratio_series else None
        rank = percentile_rank(current_ratio, ratio_series[-252:]) if ratio_series else None
        sig["gold_silver_ratio"] = {
            "value": round(current_ratio, 2) if current_ratio is not None else None,
            "percentile_rank": round(rank, 1) if rank is not None else None,
            "label": ("defensive (gold leading)" if rank is not None and rank >= 60
                      else "risk-seeking (silver leading)" if rank is not None and rank <= 40
                      else "neutral"),
        }
    else:
        sig["gold_silver_ratio"] = {"value": None, "percentile_rank": None, "label": "unknown"}

    # Gold vs dollar beta: rolling 60d beta of GLD daily returns to UUP daily
    # returns. Normally negative (gold falls when the dollar rallies).
    ra, rb = aligned_returns(gld, rows.get("UUP", []))
    if ra.size >= 60 and rb.size >= 60:
        ga, ua = ra[-60:], rb[-60:]
        var_u = float(np.var(ua))
        beta = float(np.cov(ga, ua)[0, 1] / var_u) if var_u > 0 else None
    else:
        beta = None
    sig["gold_dxy_beta"] = {
        "beta_60d": round(beta, 2) if beta is not None else None,
        "label": ("dollar-sensitive" if beta is not None and beta <= -0.5
                  else "decoupled from dollar" if beta is not None and beta > -0.2
                  else "moderately dollar-sensitive" if beta is not None else "unknown"),
    }
    return sig


def compose_take(sig: dict) -> str:
    """One-sentence macro summary from the derived signals."""
    rates = sig["rates"]["label"].split(" ")[0]
    dollar = sig["dollar"]["label"]
    credit = sig["credit"]["label"].split(" ")[0]
    commodity = "commodity carry on" if "on" in sig["commodity"]["label"] else \
                "commodity carry off" if "off" in sig["commodity"]["label"] else \
                "commodity carry stable"
    curve = sig["curve"]["label"]
    lead = {
        "easing": "Rates easing",
        "tightening": "Rates tightening",
        "stable": "Rates stable",
        "unknown": "Rates read unavailable",
    }.get(rates, "Rates " + rates)
    return (
        f"{lead} ({curve}), {dollar} dollar, {credit} credit, {commodity}. "
        f"Cross-reference with market-regime for the equity side."
    )


# ----- CLI -----

def parse_windows(raw: str) -> list[int]:
    out = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        v = int(chunk)
        if v <= 0:
            raise SystemExit(f"--windows entry must be > 0: {chunk!r}")
        out.append(v)
    if not out:
        raise SystemExit("--windows requires at least one value")
    return sorted(set(out))


def build_payload(args: argparse.Namespace) -> dict:
    global _SLEEP_BETWEEN
    if args.sleep and args.sleep > 0:
        _SLEEP_BETWEEN = args.sleep

    benchmark = args.benchmark.strip().upper()
    windows = parse_windows(args.windows)
    max_window = max(windows)
    signal_window = args.signal_window if args.signal_window in windows else (
        60 if 60 in windows else max_window
    )
    calendar_days = int(max(max_window, 252) * 1.6) + 14

    to_pull = list(BASKET.keys()) + [benchmark]
    rows: dict[str, list[dict]] = {}
    sources: list[dict] = []
    for t in to_pull:
        print(f"  Pulling daily aggs for {t}...", file=sys.stderr)
        rows[t] = fetch_daily_aggs(t, calendar_days)
        sources.append({
            "endpoint": f"/v2/aggs/ticker/{t}/range/1/day/{{from}}/{{to}}",
            "fetched_at": utcnow_iso(),
            "context": f"daily closes for {t}",
        })

    spy_rows = rows.get(benchmark, [])
    spy_ret = {w: window_return(spy_rows, w)[0] for w in windows}

    tier_caveats: list[str] = []
    if _RATE_LIMITED:
        tier_caveats.append(
            f"RATE LIMIT: {len(_RATE_LIMITED)} series "
            f"({', '.join(sorted(_RATE_LIMITED))}) returned no data because the "
            f"API rate limit was hit, not because history is missing. Their RS "
            f"and any derived signal that uses them is UNKNOWN, not zero. Free "
            f"Basic tier caps at 5 calls/min: rerun with --sleep 13 or upgrade "
            f"to Stocks Starter."
        )

    instruments: list[dict] = []
    for t, label in BASKET.items():
        tr = rows.get(t, [])
        rs_w: dict[int, float | None] = {}
        ret_w: dict[int, float | None] = {}
        n_w: dict[int, int] = {}
        for w in windows:
            ret, n = window_return(tr, w)
            br = spy_ret.get(w)
            rs = (ret - br) * 10_000.0 if (ret is not None and br is not None) else None
            rs_w[w] = rs
            ret_w[w] = ret
            n_w[w] = n
        instruments.append({
            "ticker": t,
            "label": label,
            "rs_by_window": {f"{w}d_bps": (round(rs_w[w], 1) if rs_w[w] is not None else None) for w in windows},
            "return_by_window": {f"{w}d_pct": (round(ret_w[w], 4) if ret_w[w] is not None else None) for w in windows},
            "trend_label": trend_label(rs_w),
            "curve_position_pct": (lambda c: round(c, 1) if c is not None else None)(curve_position(tr, max_window)),
            "n_obs_per_window": {f"{w}d": int(n_w[w]) for w in windows},
        })

    # Rank instruments by the longest-window RS for the rendered table.
    longest = max(windows)
    def _key(inst: dict) -> float:
        v = inst["rs_by_window"].get(f"{longest}d_bps")
        return v if v is not None else -1e18
    instruments.sort(key=_key, reverse=True)

    derived = compute_derived_signals(rows, signal_window)
    take = compose_take(derived)

    missing_bench = [w for w in windows if spy_ret.get(w) is None]
    if missing_bench:
        tier_caveats.append(
            f"Benchmark {benchmark} missing history for windows {missing_bench}; "
            f"RS in those windows reported as null."
        )
    tier_caveats.append(
        "Derived signals are ETF-return proxies for their asset class, not the "
        "underlying rate, spread, or index. Directionally right, not a "
        "substitute for the cash market."
    )

    return {
        "skill": "macro-basket",
        "as_of": TODAY.isoformat(),
        "fetched_at": utcnow_iso(),
        "benchmark": benchmark,
        "windows_days": windows,
        "signal_window_days": signal_window,
        "n_instruments": len(BASKET),
        "instruments": instruments,
        "derived_signals": derived,
        "take": take,
        "tier_caveats": tier_caveats,
        "sources": sources,
    }


# ----- Renderer -----

def render(payload: dict) -> str:
    wins = payload["windows_days"]
    win_cols = "  ".join(f"{w}d".rjust(8) for w in wins)
    lines: list[str] = []
    lines.append(f"Macro Basket vs {payload['benchmark']} — {payload['as_of']}")
    lines.append("")
    lines.append(f"{'Instrument':<24}{win_cols}   {'Trend':<15}{'Range%':>7}")
    lines.append("-" * (24 + len(win_cols) + 25))
    for inst in payload["instruments"]:
        rs = inst["rs_by_window"]
        cells = "  ".join(
            (f"{rs[f'{w}d_bps']:+.0f}" if rs.get(f"{w}d_bps") is not None else "n/a").rjust(8)
            for w in wins
        )
        rng = inst["curve_position_pct"]
        rng_s = f"{rng:.0f}" if rng is not None else "n/a"
        name = f"{inst['ticker']} {inst['label']}"[:23]
        lines.append(f"{name:<24}{cells}   {inst['trend_label']:<15}{rng_s:>7}")

    s = payload["derived_signals"]
    lines.append("")
    lines.append(f"Derived signals ({payload['signal_window_days']}d):")
    lines.append(f"  Rates:      {s['rates']['label']}")
    lines.append(f"  Curve:      {s['curve']['label']}")
    lines.append(f"  Real yield: {s['real_yield']['label']}")
    lines.append(f"  Credit:     {s['credit']['label']}")
    lines.append(f"  Dollar:     {s['dollar']['label']}")
    lines.append(f"  Commodity:  {s['commodity']['label']}")
    gsr = s["gold_silver_ratio"]
    if gsr["value"] is not None:
        lines.append(f"  Gold/Silver ratio: {gsr['value']} ({gsr['label']})")
    gdb = s["gold_dxy_beta"]
    if gdb["beta_60d"] is not None:
        lines.append(f"  Gold vs dollar beta (60d): {gdb['beta_60d']} ({gdb['label']})")

    lines.append("")
    lines.append("Take: " + payload["take"])
    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"  - {c}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="macro-basket reference")
    ap.add_argument("--benchmark", default="SPY",
                    help="Benchmark for RS denominator. Default SPY.")
    ap.add_argument("--windows", default="5,20,60,120",
                    help="Comma-separated lookback windows in trading days.")
    ap.add_argument("--signal-window", type=int, default=60,
                    help="Window (trading days) for the derived cross-asset "
                         "signals. Must be one of --windows or falls back to 60. "
                         "Default 60.")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between aggregate calls. Free Basic caps at 5 "
                         "calls/min and this pulls 12 series; use --sleep 13.")
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
