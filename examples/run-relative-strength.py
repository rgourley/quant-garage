#!/usr/bin/env python3
"""
Reference implementation of the relative-strength skill.

Takes a watchlist of tickers and a benchmark (default SPY), pulls daily
aggregates for each, and computes relative strength (RS) in basis points
over multiple lookback windows (default 5/20/60/120 trading days).

For each ticker × window:
    RS_bps = (ticker_return - benchmark_return) * 10_000

Per ticker:
    - composite_rs_percentile: average of within-watchlist percentile
      ranks across the windows (rewards consistency)
    - trend_label: improving / deteriorating / stable_leader /
      stable_laggard / mixed

Two output layers:
  Layer 1: canonical JSON matching skills/relative-strength/output-schema.json
  Layer 2: rendered table sorted by composite RS percentile desc

Usage:
    python3 examples/run-relative-strength.py \\
      --watchlist NVDA,AMD,MU,INTC,QCOM,AVGO,TXN,KLAC,AMAT,LRCX \\
      --benchmark SPY \\
      --windows 5,20,60,120

Reads MASSIVE_API_KEY from env.
"""
from __future__ import annotations

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone, timedelta

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
NOW_UTC = datetime.now(timezone.utc)
TODAY = today()

# Per-run cache: one daily-aggs pull per ticker.
_AGGS_CACHE: dict[str, list[dict]] = {}

# Tickers whose pull hit a rate limit even after the client's own retries
# plus one 13s cooldown. These come back empty NOT because there's no data
# but because the tier throttled us. Tracked so the output can say so loudly
# instead of rendering a normal-looking table full of n/a.
_RATE_LIMITED: set[str] = set()

# Seconds to sleep between aggregate calls. Free Basic caps at 5 calls/min,
# so --sleep 13 keeps a batch under the ceiling. Default spacing is tiny;
# set explicitly on a rate-limited tier. Populated from --sleep after parse.
_SLEEP_BETWEEN: float = 0.05

# One 429 cooldown: 5/min ceiling is a 12s spacing; 13s adds a 1s margin.
_RATE_LIMIT_COOLDOWN_SECONDS = 13

# The 11 SPDR sector ETFs. Optional ranking context when --include-sectors.
SECTOR_ETFS = (
    "XLK",   # Technology
    "XLF",   # Financials
    "XLE",   # Energy
    "XLV",   # Health Care
    "XLI",   # Industrials
    "XLY",   # Consumer Discretionary
    "XLP",   # Consumer Staples
    "XLU",   # Utilities
    "XLB",   # Materials
    "XLRE",  # Real Estate
    "XLC",   # Communication Services
)


# ----- HTTP -----

def fetch_daily_aggs(ticker: str, calendar_days: int) -> list[dict]:
    """Pull daily aggregates for `ticker` covering at least `calendar_days`.

    Returns a list of {date, close} records sorted ascending by date.
    Cached per-process for the run.
    """
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]

    end = TODAY
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true"
    )
    try:
        doc, _ = client.get(path)
    except RateLimited:
        # The client already retried with backoff and still got 429. Give the
        # tier one cooldown, then try once more. If it fails again, flag the
        # ticker as rate-limited so the caller surfaces a loud caveat rather
        # than silently returning an empty series that looks like "no data".
        print(
            f"  WARN: rate limited on {ticker}; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            doc, _ = client.get(path)
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

    results = doc.get("results") or []
    rows: list[dict] = []
    for r in results:
        ts_ms = r.get("t")
        close = r.get("c")
        if ts_ms is None or close is None:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    _AGGS_CACHE[ticker] = rows
    return rows


# ----- Returns -----

def window_return(rows: list[dict], window_days: int) -> tuple[float | None, int]:
    """Compute total return over the last `window_days` trading bars.

    Uses the last close and the close `window_days` bars back. Returns
    (return_decimal, n_obs_available). If we don't have window_days+1
    bars, returns (None, n_bars_available).
    """
    if len(rows) < window_days + 1:
        return (None, len(rows))
    last = rows[-1]["close"]
    prior = rows[-(window_days + 1)]["close"]
    if prior <= 0 or last <= 0:
        return (None, len(rows))
    return ((last / prior) - 1.0, window_days)


# ----- Trend labeling -----

def trend_label(rs_by_window: dict[int, float | None]) -> str:
    """Classify the trend across windows from shortest to longest.

    Sorted by window length ascending: e.g. [5, 20, 60, 120].
    Buckets:
      improving        : RS strictly increases from longest to shortest
                         (5d > 20d > 60d), i.e. recent windows stronger
      deteriorating    : RS strictly decreases from longest to shortest
                         (5d < 20d < 60d)
      stable_leader    : every window RS > 0, no clear acceleration
      stable_laggard   : every window RS < 0
      mixed            : anything else
    """
    sorted_keys = sorted(rs_by_window.keys())
    vals = [rs_by_window[k] for k in sorted_keys]
    if any(v is None for v in vals) or len(vals) < 2:
        return "mixed"
    # Treat as a series ordered shortest -> longest window
    # We want to detect short-window stronger than long-window: improving
    # "Recent stronger" = shorter-window RS > longer-window RS strictly
    # Take at most the first three windows for the trend test
    head = vals[: min(3, len(vals))]
    strictly_improving = all(head[i] > head[i + 1] for i in range(len(head) - 1))
    strictly_deteriorating = all(head[i] < head[i + 1] for i in range(len(head) - 1))
    all_positive = all(v > 0 for v in vals)
    all_negative = all(v < 0 for v in vals)

    if strictly_improving and not all_negative:
        return "improving"
    if strictly_deteriorating and not all_positive:
        return "deteriorating"
    if all_positive:
        return "stable_leader"
    if all_negative:
        return "stable_laggard"
    return "mixed"


# ----- Formatting -----

def fmt_bps(x: float | None) -> str:
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 else ""
    return f"{sign}{int(round(x))}bp"


# ----- CLI -----

def parse_int_list(raw: str, name: str) -> list[int]:
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            v = int(chunk)
        except ValueError as exc:
            raise SystemExit(f"--{name} entry not an integer: {chunk!r}") from exc
        if v <= 0:
            raise SystemExit(f"--{name} entry must be > 0: {chunk!r}")
        out.append(v)
    if not out:
        raise SystemExit(f"--{name} requires at least one value")
    return sorted(set(out))


ap = argparse.ArgumentParser(description="relative-strength reference")
ap.add_argument("--watchlist", type=str, required=True,
                help="Comma-separated tickers to rank")
ap.add_argument("--benchmark", type=str, default="SPY",
                help="Benchmark ticker for relative comparison. Default SPY.")
ap.add_argument("--windows", type=str, default="5,20,60,120",
                help="Comma-separated lookback windows in trading days. "
                     "Default 5,20,60,120.")
ap.add_argument("--include-sectors", action="store_true",
                help="Also rank the 11 SPDR sector ETFs alongside the watchlist.")
ap.add_argument("--sleep", type=float, default=None,
                help="Seconds to sleep between aggregate calls. Free Basic "
                     "tier caps at 5 calls/min; use --sleep 13 to stay under "
                     "it. Default: minimal spacing (assumes an unmetered tier).")
ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT. "
                     "Default: render.")
args = ap.parse_args()
fmt = resolve_output_format(args.format)

if args.sleep is not None:
    if args.sleep < 0:
        print("ERROR: --sleep cannot be negative", file=sys.stderr)
        sys.exit(1)
    _SLEEP_BETWEEN = args.sleep

watchlist_req = [t.strip().upper() for t in args.watchlist.split(",") if t.strip()]
if len(watchlist_req) < 1:
    print("ERROR: --watchlist needs at least 1 ticker", file=sys.stderr)
    sys.exit(1)

benchmark = args.benchmark.strip().upper()
if not benchmark:
    print("ERROR: --benchmark cannot be empty", file=sys.stderr)
    sys.exit(1)

windows = parse_int_list(args.windows, "windows")
max_window = max(windows)
# Overshoot by 1.6x in calendar days + buffer for weekends/holidays.
calendar_days_to_pull = int(max_window * 1.6) + 14


# ----- Data pull -----

print(
    f"Ranking {len(watchlist_req)} tickers vs {benchmark} across windows "
    f"{windows}",
    file=sys.stderr,
)

# Universe to pull: watchlist + benchmark + (sectors if requested)
to_pull: list[str] = list(dict.fromkeys(watchlist_req + [benchmark]))
ranked_universe: list[str] = list(watchlist_req)
if args.include_sectors:
    for sym in SECTOR_ETFS:
        if sym not in to_pull:
            to_pull.append(sym)
        if sym not in ranked_universe:
            ranked_universe.append(sym)

sources: list[dict] = []
all_rows: dict[str, list[dict]] = {}
tier_caveats: list[str] = []

for t in to_pull:
    print(f"  Pulling daily aggs for {t}...", file=sys.stderr)
    rows = fetch_daily_aggs(t, calendar_days_to_pull)
    sources.append({
        "endpoint": f"/v2/aggs/ticker/{t}/range/1/day/{{from}}/{{to}}",
        "fetched_at": utcnow_iso(),
        "context": f"daily aggs for {t}",
    })
    all_rows[t] = rows
    time.sleep(_SLEEP_BETWEEN)

# Benchmark must have enough history
bench_rows = all_rows.get(benchmark) or []
bench_returns: dict[int, float | None] = {}
bench_n: dict[int, int] = {}
for w in windows:
    ret, n = window_return(bench_rows, w)
    bench_returns[w] = ret
    bench_n[w] = n

missing_bench_windows = [w for w in windows if bench_returns[w] is None]
if missing_bench_windows:
    tier_caveats.append(
        f"Benchmark {benchmark} missing history for windows "
        f"{missing_bench_windows}; RS in those windows reported as null."
    )


# ----- Compute per-ticker RS + returns -----

results: list[dict] = []
# We need per-window RS lists across the ranked universe for percentile rank.
rs_by_window_across_universe: dict[int, list[float]] = {w: [] for w in windows}
# Hold per-ticker per-window RS temporarily so we can percentile-rank
per_ticker_rs: dict[str, dict[int, float | None]] = {}
per_ticker_ret: dict[str, dict[int, float | None]] = {}
per_ticker_n: dict[str, dict[int, int]] = {}

for t in ranked_universe:
    rows = all_rows.get(t) or []
    rs_w: dict[int, float | None] = {}
    ret_w: dict[int, float | None] = {}
    n_w: dict[int, int] = {}
    for w in windows:
        ret, n = window_return(rows, w)
        bench_ret = bench_returns[w]
        rs = None
        if ret is not None and bench_ret is not None:
            rs = (ret - bench_ret) * 10_000.0
        rs_w[w] = rs
        ret_w[w] = ret
        n_w[w] = n
        if rs is not None:
            rs_by_window_across_universe[w].append(rs)
    per_ticker_rs[t] = rs_w
    per_ticker_ret[t] = ret_w
    per_ticker_n[t] = n_w

# Now compute composite percentile per ticker across windows.
for t in ranked_universe:
    rs_w = per_ticker_rs[t]
    ret_w = per_ticker_ret[t]
    n_w = per_ticker_n[t]

    window_ranks: list[float] = []
    for w in windows:
        rs = rs_w[w]
        if rs is None:
            continue
        dist = rs_by_window_across_universe[w]
        rank = percentile_rank(rs, dist)
        if rank is not None:
            window_ranks.append(rank)
    composite = (
        round(sum(window_ranks) / len(window_ranks), 1)
        if window_ranks
        else None
    )
    label = trend_label(rs_w)

    rs_keys = {f"{w}d_bps": (int(round(rs_w[w])) if rs_w[w] is not None else None)
               for w in windows}
    ret_keys = {f"{w}d_pct": (round(ret_w[w], 4) if ret_w[w] is not None else None)
                for w in windows}
    nobs_keys = {f"{w}d": int(n_w[w]) for w in windows}

    results.append({
        "ticker": t,
        "rs_by_window": rs_keys,
        "return_by_window": ret_keys,
        "composite_rs_percentile": composite,
        "trend_label": label,
        "n_obs_per_window": nobs_keys,
    })


# ----- Ranking -----

def _sort_key(r: dict) -> float:
    c = r.get("composite_rs_percentile")
    # Push None to the bottom
    return c if c is not None else -1.0

sorted_results = sorted(results, key=_sort_key, reverse=True)
ranked_tickers = [r["ticker"] for r in sorted_results
                  if r.get("composite_rs_percentile") is not None]
leaders_top_3 = ranked_tickers[:3]
laggards_bottom_3 = list(reversed(ranked_tickers[-3:])) if len(ranked_tickers) >= 1 else []


# ----- Rate-limit caveat (loud, goes first) -----

# If any ticker came back empty because the tier throttled us (not because
# the data is missing), say so at the top. Otherwise the table renders a
# normal-looking wall of n/a that reads as a real "no data" answer.
if _RATE_LIMITED:
    n_rl = len(_RATE_LIMITED)
    tier_caveats.insert(
        0,
        f"RATE LIMIT: {n_rl} of {len(to_pull)} tickers "
        f"({', '.join(sorted(_RATE_LIMITED))}) returned no data because the "
        f"API rate limit was hit, not because history is missing. Their RS "
        f"shows as n/a but is UNKNOWN, not zero. Free Basic tier caps at 5 "
        f"calls/min: rerun with --sleep 13, upgrade to Stocks Starter, or run "
        f"in smaller batches.",
    )


# ----- Always-on caveats -----

tier_caveats.append(
    "RS is past-return relative to benchmark; not predictive on its own. "
    "Pair with regime read."
)
if args.include_sectors:
    tier_caveats.append(
        f"Sector ETFs ranked alongside watchlist ({len(SECTOR_ETFS)} SPDR "
        f"sector funds); leadership context, not stock picks."
    )

# Surface any ticker with insufficient history for the longest window
for r in results:
    longest = max(windows)
    n_longest = r["n_obs_per_window"].get(f"{longest}d", 0)
    if n_longest < longest:
        tier_caveats.append(
            f"{r['ticker']}: only {n_longest} bars for {longest}d window; "
            f"RS over that window reported as null."
        )


# ----- Payload -----

payload = {
    "skill": "relative-strength",
    "as_of": TODAY.isoformat(),
    "fetched_at": NOW_UTC.isoformat(),
    "watchlist": watchlist_req,
    "benchmark": benchmark,
    "windows_days": windows,
    "include_sectors": bool(args.include_sectors),
    "n_tickers": len(ranked_universe),
    "results": sorted_results,
    "ranking": {
        "leaders_top_3": leaders_top_3,
        "laggards_bottom_3": laggards_bottom_3,
    },
    "tier_caveats": tier_caveats,
    "sources": sources,
}


# ----- Renderer -----

def render(payload: dict) -> str:
    lines: list[str] = []
    bench = payload["benchmark"]
    wins = payload["windows_days"]
    n = payload["n_tickers"]
    win_str = "/".join(str(w) for w in wins)

    # Header
    lines.append(f"Relative Strength vs {bench} — {payload['as_of']}")
    extra = " (incl. SPDR sectors)" if payload["include_sectors"] else ""
    lines.append(f"Watchlist: {n} tickers{extra} · Windows: {win_str} days")
    lines.append("")

    # Table header
    ticker_w = 8
    rs_w = 10
    trend_w = 18
    comp_w = 10
    headers = [f"{'Ticker':<{ticker_w}}"]
    for w in wins:
        headers.append(f"{w}d RS".rjust(rs_w))
    headers.append(f"{'Trend':<{trend_w}}")
    headers.append(f"{'Comp %ile':>{comp_w}}")
    lines.append(" ".join(headers))
    total_width = ticker_w + (rs_w + 1) * len(wins) + 1 + trend_w + 1 + comp_w
    lines.append("-" * total_width)

    # Body rows in sorted order
    for r in payload["results"]:
        row = [f"{r['ticker']:<{ticker_w}}"]
        for w in wins:
            rs_val = r["rs_by_window"].get(f"{w}d_bps")
            row.append(fmt_bps(rs_val).rjust(rs_w))
        row.append(f"{r['trend_label']:<{trend_w}}")
        c = r.get("composite_rs_percentile")
        c_str = "n/a" if c is None else f"{c:.0f}"
        row.append(c_str.rjust(comp_w))
        lines.append(" ".join(row))

    lines.append("")
    if payload["ranking"]["leaders_top_3"]:
        lines.append("Leaders:  " + ", ".join(payload["ranking"]["leaders_top_3"]))
    if payload["ranking"]["laggards_bottom_3"]:
        lines.append("Laggards: " + ", ".join(payload["ranking"]["laggards_bottom_3"]))

    # Caveats footer
    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines)


rendered = render(payload)


# ----- Write output -----

out_name = "relative-strength-output.md"
out_path = os.path.join(os.path.dirname(__file__), out_name)
with open(out_path, "w") as fout:
    fout.write("# relative-strength run\n\n")
    fout.write(f"Generated: {NOW_UTC.isoformat()}\n")
    fout.write(f"Watchlist: {','.join(payload['watchlist'])}\n")
    fout.write(f"Benchmark: {payload['benchmark']}  ")
    fout.write(f"Windows: {payload['windows_days']}  ")
    fout.write(f"Include sectors: {payload['include_sectors']}\n\n")
    fout.write("## Layer 1: canonical JSON (live data)\n\n")
    fout.write("```json\n")
    fout.write(json.dumps(payload, indent=2, default=str))
    fout.write("\n```\n\n")
    fout.write("## Layer 2: rendered table (live data)\n\n")
    fout.write("```\n")
    fout.write(rendered)
    fout.write("\n```\n")

print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
emit_to_stdout(rendered, payload, fmt)
