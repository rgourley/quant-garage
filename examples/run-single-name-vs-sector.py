#!/usr/bin/env python3
"""
Reference implementation of the single-name-vs-sector skill.

relative-strength ranks a name versus SPY. That answer conflates two very
different things: is the name strong because its whole sector is strong, or
is the name pulling away from (or falling behind) its own sector. This tool
splits them apart explicitly instead of making the reader eyeball two
separate relative-strength runs.

It surfaced when SOFI showed as a stable laggard (-4100bp vs SPY over 120d)
while its sector XLF was a leader (+377bp over 20d). The name was diverging
from its sector: the weakness was name-specific, not a financials problem.

For one ticker it maps the name to its SPDR sector ETF, then over each
window computes three relative strengths in basis points:

  - name vs sector      (name_return - sector_return) * 10000
  - sector vs benchmark (sector_return - spy_return) * 10000
  - name vs benchmark   (name_return - spy_return) * 10000

The divergence score is the name-vs-sector RS averaged across windows (how
far the name is running from its own sector), plus an unsigned composite.
The take line classifies the name into leading its sector, lagging its
sector, or diverging (name and sector pointing opposite ways), with the
magnitude and the windows driving it.

Two output layers:
  Layer 1: canonical JSON matching skills/single-name-vs-sector/output-schema.json
  Layer 2: rendered table (per-window RS) + a divergence block + take

Usage:
    python3 examples/run-single-name-vs-sector.py --ticker SOFI
    python3 examples/run-single-name-vs-sector.py --ticker NVDA --windows 5,20,60,120
    python3 examples/run-single-name-vs-sector.py --ticker ANET --sector XLK
    python3 examples/run-single-name-vs-sector.py --ticker SOFI --sleep 13

Reads MASSIVE_API_KEY from env. Pulls three series (name, sector ETF,
benchmark), so it runs comfortably on Free Basic; pass --sleep 13 only if
you are batching many names back to back.
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

# Ticker -> SPDR sector ETF. A reasonable set of large, liquid US names
# spanning all 11 sectors. Not exhaustive: any name not here can be run
# with an explicit --sector override. Classification follows GICS sector
# assignment as used by the SPDR select-sector funds (AMZN and TSLA sit in
# XLY Consumer Discretionary; GOOGL, META, and NFLX sit in XLC
# Communication Services).
TICKER_TO_SECTOR: dict[str, str] = {
    # XLK Technology
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AVGO": "XLK",
    "ORCL": "XLK", "CRM": "XLK", "ADBE": "XLK", "AMD": "XLK",
    "CSCO": "XLK", "ACN": "XLK", "INTC": "XLK", "QCOM": "XLK",
    "TXN": "XLK",
    # XLF Financials
    "JPM": "XLF", "BAC": "XLF", "WFC": "XLF", "GS": "XLF",
    "MS": "XLF", "C": "XLF", "BLK": "XLF", "SCHW": "XLF",
    "AXP": "XLF", "SOFI": "XLF", "COF": "XLF",
    # XLE Energy
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "SLB": "XLE",
    "EOG": "XLE", "OXY": "XLE",
    # XLV Health Care
    "UNH": "XLV", "JNJ": "XLV", "LLY": "XLV", "PFE": "XLV",
    "MRK": "XLV", "ABBV": "XLV", "TMO": "XLV", "ABT": "XLV",
    # XLI Industrials
    "CAT": "XLI", "BA": "XLI", "HON": "XLI", "GE": "XLI",
    "UPS": "XLI", "RTX": "XLI", "DE": "XLI", "LMT": "XLI",
    # XLY Consumer Discretionary
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "MCD": "XLY",
    "NKE": "XLY", "LOW": "XLY", "SBUX": "XLY", "BKNG": "XLY",
    # XLP Consumer Staples
    "PG": "XLP", "KO": "XLP", "PEP": "XLP", "COST": "XLP",
    "WMT": "XLP", "PM": "XLP", "MDLZ": "XLP",
    # XLU Utilities
    "NEE": "XLU", "DUK": "XLU", "SO": "XLU", "D": "XLU",
    # XLB Materials
    "LIN": "XLB", "APD": "XLB", "SHW": "XLB", "FCX": "XLB",
    "NEM": "XLB",
    # XLRE Real Estate
    "AMT": "XLRE", "PLD": "XLRE", "EQIX": "XLRE", "SPG": "XLRE",
    # XLC Communication Services
    "GOOGL": "XLC", "GOOG": "XLC", "META": "XLC", "NFLX": "XLC",
    "DIS": "XLC", "T": "XLC", "VZ": "XLC", "TMUS": "XLC",
}

SECTOR_LABELS: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

# Classification thresholds (basis points). Below these a leg reads as flat.
_DIVERGENCE_THRESHOLD_BPS = 25.0   # name vs sector must clear this to count
_SECTOR_THRESHOLD_BPS = 10.0       # sector vs benchmark must clear this

_AGGS_CACHE: dict[str, list[dict]] = {}
_RATE_LIMITED: set[str] = set()
_SLEEP_BETWEEN: float = 0.0
_RATE_LIMIT_COOLDOWN_SECONDS = 13


# ----- HTTP (reused verbatim from run-macro-basket.py) -----

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


# ----- Math helpers (reused from relative-strength / macro-basket) -----

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


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


# ----- Classification -----

def classify(name_vs_sector_avg: float | None,
             sector_vs_bench_avg: float | None) -> str:
    """One of leading its sector, lagging its sector, diverging.

    diverging is checked first: when the name's move versus its own sector
    points the opposite way to the sector's move versus the benchmark, the
    name is running away from (or falling behind) its sector regardless of
    the market. That is the signal this tool exists to catch, so it wins.
    Otherwise fall back to the plain name-vs-sector sign.
    """
    if name_vs_sector_avg is None:
        return "lagging its sector"
    if sector_vs_bench_avg is not None:
        opposite = (name_vs_sector_avg > 0) != (sector_vs_bench_avg > 0)
        if (opposite
                and abs(name_vs_sector_avg) >= _DIVERGENCE_THRESHOLD_BPS
                and abs(sector_vs_bench_avg) >= _SECTOR_THRESHOLD_BPS):
            return "diverging"
    return "leading its sector" if name_vs_sector_avg >= 0 else "lagging its sector"


def _pts(bps: float | None) -> float | None:
    """Basis points to percentage points (pp = bps / 100), rounded to 1 dp."""
    return round(bps / 100.0, 1) if bps is not None else None


def _driving_window(by_window: dict[int, float | None]) -> tuple[int | None, float | None]:
    """Window with the largest absolute value, and that value."""
    best_w: int | None = None
    best_v: float | None = None
    for w, v in by_window.items():
        if v is None:
            continue
        if best_v is None or abs(v) > abs(best_v):
            best_w, best_v = w, v
    return best_w, best_v


def compose_take(payload: dict,
                 name_vs_sector: dict[int, float | None],
                 sector_vs_bench: dict[int, float | None]) -> str:
    """One-sentence read: is the divergence name-specific or sector-driven."""
    ticker = payload["ticker"]
    sector = payload["sector"]
    benchmark = payload["benchmark"]
    cls = payload["classification"]

    ns_w, ns_v = _driving_window(name_vs_sector)
    sb_w, sb_v = _driving_window(sector_vs_bench)

    if ns_w is None:
        return (
            f"{ticker}: insufficient history to measure it against {sector}. "
            f"Rerun once more bars are available."
        )

    ns_pts = abs(_pts(ns_v))
    ns_dir = "leading" if ns_v >= 0 else "lagging"

    sector_clause = ""
    if sb_w is not None:
        sb_pts = abs(_pts(sb_v))
        sb_dir = "leads" if sb_v >= 0 else "lags"
        sector_clause = f"{sector} {sb_dir} {benchmark} by {sb_pts:.0f} pp/{sb_w}d"

    if cls == "diverging":
        specific = "weakness" if ns_v < 0 else "strength"
        joiner = "even as" if sector_clause else ""
        tail = f" {joiner} {sector_clause}" if sector_clause else ""
        return (
            f"{ticker} is diverging: {ns_dir} {sector} by {ns_pts:.0f} "
            f"pp/{ns_w}d{tail}. The {specific} is name-specific, not sector. "
            f"Cross-reference with relative-strength for the watchlist view."
        )

    if cls == "leading its sector":
        broad = ""
        if sb_v is not None:
            broad = (" The strength is broad-based (sector leading too)."
                     if sb_v > 0 else
                     " The strength is name-specific (sector lagging).")
        lead_sec = f" while {sector_clause}" if sector_clause else ""
        return (
            f"{ticker} is leading {sector} by {ns_pts:.0f} pp/{ns_w}d"
            f"{lead_sec}.{broad} "
            f"Cross-reference with relative-strength for the watchlist view."
        )

    # lagging its sector
    broad = ""
    if sb_v is not None:
        broad = (" The weakness is name-specific (sector leading)."
                 if sb_v > 0 else
                 " The weakness is broad-based (sector lagging too).")
    lag_sec = f" while {sector_clause}" if sector_clause else ""
    return (
        f"{ticker} is lagging {sector} by {ns_pts:.0f} pp/{ns_w}d"
        f"{lag_sec}.{broad} "
        f"Cross-reference with relative-strength for the watchlist view."
    )


# ----- CLI -----

def parse_windows(raw: str) -> list[int]:
    out = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            v = int(chunk)
        except ValueError as exc:
            raise SystemExit(f"--windows entry not an integer: {chunk!r}") from exc
        if v <= 0:
            raise SystemExit(f"--windows entry must be > 0: {chunk!r}")
        out.append(v)
    if not out:
        raise SystemExit("--windows requires at least one value")
    return sorted(set(out))


def resolve_sector(ticker: str, override: str | None) -> str:
    """Map ticker to its sector ETF, honoring an explicit override.

    Raises SystemExit with a clear message when the ticker is unknown and
    no override was given, so any ticker still works via --sector.
    """
    if override:
        return override.strip().upper()
    sector = TICKER_TO_SECTOR.get(ticker)
    if sector is None:
        raise SystemExit(
            f"ERROR: {ticker} is not in the built-in ticker->sector map. "
            f"Pass its SPDR sector ETF explicitly, e.g. "
            f"--sector XLK (Technology), XLF (Financials), XLE (Energy), "
            f"XLV (Health Care), XLI (Industrials), XLY (Consumer "
            f"Discretionary), XLP (Consumer Staples), XLU (Utilities), "
            f"XLB (Materials), XLRE (Real Estate), or XLC (Communication "
            f"Services)."
        )
    return sector


def build_payload(args: argparse.Namespace) -> dict:
    global _SLEEP_BETWEEN
    if getattr(args, "sleep", None) and args.sleep > 0:
        _SLEEP_BETWEEN = args.sleep

    ticker = args.ticker.strip().upper()
    if not ticker:
        raise SystemExit("ERROR: --ticker cannot be empty")
    benchmark = args.benchmark.strip().upper()
    sector = resolve_sector(ticker, args.sector)
    sector_label = SECTOR_LABELS.get(sector, "user-supplied sector")

    windows = parse_windows(args.windows)
    max_window = max(windows)
    calendar_days = int(max_window * 1.6) + 14

    to_pull = list(dict.fromkeys([ticker, sector, benchmark]))
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

    name_rows = rows.get(ticker, [])
    sector_rows = rows.get(sector, [])
    bench_rows = rows.get(benchmark, [])

    # Per-window relative strengths, all in basis points.
    name_vs_sector: dict[int, float | None] = {}
    sector_vs_bench: dict[int, float | None] = {}
    name_vs_bench: dict[int, float | None] = {}
    name_ret: dict[int, float | None] = {}
    sector_ret: dict[int, float | None] = {}
    bench_ret: dict[int, float | None] = {}
    n_obs: dict[int, int] = {}

    for w in windows:
        nr, n = window_return(name_rows, w)
        sr, _ = window_return(sector_rows, w)
        br, _ = window_return(bench_rows, w)
        name_ret[w] = nr
        sector_ret[w] = sr
        bench_ret[w] = br
        n_obs[w] = n
        name_vs_sector[w] = (nr - sr) * 10_000.0 if (nr is not None and sr is not None) else None
        sector_vs_bench[w] = (sr - br) * 10_000.0 if (sr is not None and br is not None) else None
        name_vs_bench[w] = (nr - br) * 10_000.0 if (nr is not None and br is not None) else None

    ns_vals = [v for v in name_vs_sector.values() if v is not None]
    sb_vals = [v for v in sector_vs_bench.values() if v is not None]
    nb_vals = [v for v in name_vs_bench.values() if v is not None]

    divergence_score_bps = _mean(ns_vals)
    divergence_composite_bps = _mean([abs(v) for v in ns_vals]) if ns_vals else None
    sector_vs_bench_avg_bps = _mean(sb_vals)
    name_vs_bench_avg_bps = _mean(nb_vals)

    classification = classify(divergence_score_bps, sector_vs_bench_avg_bps)

    def _round_map(m: dict[int, float | None], suffix: str, nd: int = 1) -> dict:
        return {f"{w}{suffix}": (round(m[w], nd) if m[w] is not None else None)
                for w in windows}

    tier_caveats: list[str] = []
    if _RATE_LIMITED:
        tier_caveats.insert(0,
            f"RATE LIMIT: {len(_RATE_LIMITED)} series "
            f"({', '.join(sorted(_RATE_LIMITED))}) returned no data because the "
            f"API rate limit was hit, not because history is missing. Any RS "
            f"leg using them is UNKNOWN, not zero. Rerun with --sleep 13 or "
            f"upgrade to Stocks Starter.")

    missing_windows = [w for w in windows if name_vs_sector[w] is None]
    if missing_windows:
        tier_caveats.append(
            f"Insufficient history for windows {missing_windows}; the "
            f"name-vs-sector leg is null there and excluded from the "
            f"divergence score.")
    if args.sector:
        tier_caveats.append(
            f"Sector {sector} supplied via --sector override, not the "
            f"built-in map. Confirm it is the right sector proxy for {ticker}.")
    tier_caveats.append(
        "RS is past relative return, not predictive. Sector ETF is a "
        "cap-weighted proxy for the peer group, not a custom peer basket; a "
        "name can look like it is diverging from its sector when it is really "
        "diverging from the ETF's largest holdings.")

    payload = {
        "skill": "single-name-vs-sector",
        "as_of": TODAY.isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "sector": sector,
        "sector_label": sector_label,
        "benchmark": benchmark,
        "windows_days": windows,
        "name_return_by_window": _round_map(name_ret, "d_pct", 4),
        "sector_return_by_window": _round_map(sector_ret, "d_pct", 4),
        "benchmark_return_by_window": _round_map(bench_ret, "d_pct", 4),
        "name_vs_sector_bps": _round_map(name_vs_sector, "d_bps"),
        "sector_vs_benchmark_bps": _round_map(sector_vs_bench, "d_bps"),
        "name_vs_benchmark_bps": _round_map(name_vs_bench, "d_bps"),
        "n_obs_per_window": {f"{w}d": int(n_obs[w]) for w in windows},
        "trend_name_vs_sector": trend_label(name_vs_sector),
        "trend_sector_vs_benchmark": trend_label(sector_vs_bench),
        "trend_name_vs_benchmark": trend_label(name_vs_bench),
        "divergence": {
            "score_bps": round(divergence_score_bps, 1) if divergence_score_bps is not None else None,
            "composite_bps": round(divergence_composite_bps, 1) if divergence_composite_bps is not None else None,
            "sector_vs_benchmark_avg_bps": round(sector_vs_bench_avg_bps, 1) if sector_vs_bench_avg_bps is not None else None,
            "name_vs_benchmark_avg_bps": round(name_vs_bench_avg_bps, 1) if name_vs_bench_avg_bps is not None else None,
        },
        "classification": classification,
        "take": "",
        "tier_caveats": tier_caveats,
        "sources": sources,
    }
    payload["take"] = compose_take(payload, name_vs_sector, sector_vs_bench)
    return payload


# ----- Renderer -----

def render(payload: dict) -> str:
    wins = payload["windows_days"]
    ticker = payload["ticker"]
    sector = payload["sector"]
    benchmark = payload["benchmark"]

    def cells(prefix: str, m: dict) -> str:
        return "  ".join(
            (f"{m[f'{w}{prefix}']:+.0f}" if m.get(f"{w}{prefix}") is not None else "n/a").rjust(9)
            for w in wins
        )

    win_cols = "  ".join(f"{w}d".rjust(9) for w in wins)
    lines: list[str] = []
    lines.append(f"{ticker} vs {sector} ({payload['sector_label']}) "
                 f"vs {benchmark} ({payload['as_of']})")
    lines.append("")
    lines.append(f"{'Relative strength (bps)':<26}{win_cols}   {'Trend':<15}")
    lines.append("-" * (26 + len(win_cols) + 18))
    lines.append(f"{ticker + ' vs ' + sector:<26}"
                 f"{cells('d_bps', payload['name_vs_sector_bps'])}   "
                 f"{payload['trend_name_vs_sector']:<15}")
    lines.append(f"{sector + ' vs ' + benchmark:<26}"
                 f"{cells('d_bps', payload['sector_vs_benchmark_bps'])}   "
                 f"{payload['trend_sector_vs_benchmark']:<15}")
    lines.append(f"{ticker + ' vs ' + benchmark:<26}"
                 f"{cells('d_bps', payload['name_vs_benchmark_bps'])}   "
                 f"{payload['trend_name_vs_benchmark']:<15}")

    d = payload["divergence"]
    lines.append("")
    lines.append("Divergence:")
    score = d["score_bps"]
    comp = d["composite_bps"]
    lines.append(f"  Score (name vs {sector}, avg across windows): "
                 f"{('%+.0f bps' % score) if score is not None else 'n/a'}")
    lines.append(f"  Composite (avg |name vs {sector}|): "
                 f"{('%.0f bps' % comp) if comp is not None else 'n/a'}")
    sb = d["sector_vs_benchmark_avg_bps"]
    lines.append(f"  Sector vs {benchmark} (avg): "
                 f"{('%+.0f bps' % sb) if sb is not None else 'n/a'}")
    lines.append(f"  Classification: {payload['classification']}")

    lines.append("")
    lines.append("Take: " + payload["take"])

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"  - {c}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="single-name-vs-sector reference")
    ap.add_argument("--ticker", required=True,
                    help="The single name to measure against its sector.")
    ap.add_argument("--benchmark", default="SPY",
                    help="Benchmark for the sector-vs-market leg. Default SPY.")
    ap.add_argument("--windows", default="5,20,60,120",
                    help="Comma-separated lookback windows in trading days.")
    ap.add_argument("--sector", default=None,
                    help="SPDR sector ETF override (e.g. XLK). Required when "
                         "the ticker is not in the built-in map.")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between aggregate calls. Free Basic caps at "
                         "5 calls/min; this pulls 3 series so it is rarely "
                         "needed, but use --sleep 13 when batching names.")
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
