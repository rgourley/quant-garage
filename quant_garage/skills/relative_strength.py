"""
relative-strength as an importable library function.

Ranks a watchlist by relative strength (RS) in bps vs a benchmark across
multiple lookback windows, produces a composite percentile, and labels
each ticker's RS trend (improving, deteriorating, stable_leader,
stable_laggard, mixed).

    from quant_garage.skills.relative_strength import run, render
    payload = run(["NVDA","AMD","AVGO"], benchmark="SPY", windows=[5,20,60,120])
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Iterable

from .. import MassiveClient, FetchError, RateLimited, today, utcnow_iso, percentile_rank


SECTOR_ETFS = (
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY",
    "XLP", "XLU", "XLB", "XLRE", "XLC",
)

# Module-level cache — same-process runs reuse aggs (relative-strength +
# stock-one-pager both hit SPY, benchmark reuse across watchlist runs, etc.)
_AGGS_CACHE: dict[str, list[dict]] = {}

# Tickers that came back empty because the tier throttled us, not because
# history is missing. Tracked per-process so the caller can surface a loud
# caveat instead of rendering a normal-looking table full of n/a.
_RATE_LIMITED: set[str] = set()

# One 429 cooldown: Free Basic caps at 5 calls/min (12s spacing); 13s adds
# a 1s safety margin.
_RATE_LIMIT_COOLDOWN_SECONDS = 13


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


# ----- HTTP -----

def _fetch_daily_aggs(
    client: MassiveClient, ticker: str, calendar_days: int, sources: _Sources,
) -> list[dict]:
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]
    end = today()
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true"
    )
    try:
        doc, _ = client.get(path)
    except RateLimited:
        # Client already retried with backoff and still got 429. One tier
        # cooldown, one retry. If still failing, flag the ticker so the
        # caller can surface a loud caveat rather than silently returning
        # an empty series that renders as "no data".
        print(
            f"  WARN: rate limited on {ticker}; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            doc, _ = client.get(path)
        except FetchError:
            _RATE_LIMITED.add(ticker)
            _AGGS_CACHE[ticker] = []
            return []
    except FetchError:
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
    sources.record(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        utcnow_iso(),
        f"daily aggs for {ticker}",
    )
    return rows


# ----- Returns / labeling -----

def _window_return(rows: list[dict], window_days: int) -> tuple[float | None, int]:
    if len(rows) < window_days + 1:
        return (None, len(rows))
    last = rows[-1]["close"]
    prior = rows[-(window_days + 1)]["close"]
    if prior <= 0 or last <= 0:
        return (None, len(rows))
    return ((last / prior) - 1.0, window_days)


def _trend_label(rs_by_window: dict[int, float | None]) -> str:
    sorted_keys = sorted(rs_by_window.keys())
    vals = [rs_by_window[k] for k in sorted_keys]
    if any(v is None for v in vals) or len(vals) < 2:
        return "mixed"
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


# ----- Public API -----

def run(
    watchlist: Iterable[str] | str,
    benchmark: str = "SPY",
    windows: Iterable[int] | None = None,
    include_sectors: bool = False,
    client: MassiveClient | None = None,
) -> dict:
    """Rank `watchlist` by RS vs `benchmark` across `windows` trading-day lookbacks.

    Args:
        watchlist: comma-separated string or iterable of tickers.
        benchmark: benchmark ticker (default 'SPY').
        windows: iterable of trading-day windows (default [5, 20, 60, 120]).
        include_sectors: also rank the 11 SPDR sector ETFs alongside.
        client: reuse an existing MassiveClient.
    """
    if isinstance(watchlist, str):
        tickers = [t.strip().upper() for t in watchlist.split(",") if t.strip()]
    else:
        tickers = [t.strip().upper() for t in watchlist if t and t.strip()]
    if not tickers:
        raise ValueError("watchlist must contain at least one ticker")
    windows = sorted(set(int(w) for w in (windows or (5, 20, 60, 120))))
    if not windows or min(windows) <= 0:
        raise ValueError("windows must be positive integers")
    benchmark = benchmark.strip().upper()
    if not benchmark:
        raise ValueError("benchmark cannot be empty")

    client = client or MassiveClient()
    sources = _Sources()
    max_window = max(windows)
    calendar_days_to_pull = int(max_window * 1.6) + 14

    to_pull = list(dict.fromkeys(tickers + [benchmark]))
    ranked_universe = list(tickers)
    if include_sectors:
        for sym in SECTOR_ETFS:
            if sym not in to_pull:
                to_pull.append(sym)
            if sym not in ranked_universe:
                ranked_universe.append(sym)

    all_rows: dict[str, list[dict]] = {}
    for t in to_pull:
        all_rows[t] = _fetch_daily_aggs(client, t, calendar_days_to_pull, sources)
        time.sleep(0.02)  # gentle throttle for larger watchlists

    tier_caveats: list[str] = []
    rate_limited_in_pull = [t for t in to_pull if t in _RATE_LIMITED]
    if rate_limited_in_pull:
        names = ", ".join(rate_limited_in_pull)
        tier_caveats.append(
            f"RATE LIMIT: {len(rate_limited_in_pull)} of {len(to_pull)} tickers "
            f"({names}) returned no data because the API rate limit was hit, "
            f"not because history is missing. Their RS shows as n/a but is "
            f"UNKNOWN, not zero. Free Basic tier caps at 5 calls/min: rerun "
            f"with a longer per-call delay, upgrade to Stocks Starter, or "
            f"run in smaller batches."
        )
    bench_rows = all_rows.get(benchmark) or []
    bench_returns: dict[int, float | None] = {}
    bench_n: dict[int, int] = {}
    for w in windows:
        ret, n = _window_return(bench_rows, w)
        bench_returns[w] = ret
        bench_n[w] = n
    missing_bench_windows = [w for w in windows if bench_returns[w] is None]
    if missing_bench_windows:
        tier_caveats.append(
            f"Benchmark {benchmark} missing history for windows "
            f"{missing_bench_windows}; RS in those windows reported as null."
        )

    rs_by_window_across_universe: dict[int, list[float]] = {w: [] for w in windows}
    per_ticker_rs: dict[str, dict[int, float | None]] = {}
    per_ticker_ret: dict[str, dict[int, float | None]] = {}
    per_ticker_n: dict[str, dict[int, int]] = {}

    for t in ranked_universe:
        rows = all_rows.get(t) or []
        rs_w: dict[int, float | None] = {}
        ret_w: dict[int, float | None] = {}
        n_w: dict[int, int] = {}
        for w in windows:
            ret, n = _window_return(rows, w)
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

    results: list[dict] = []
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
        label = _trend_label(rs_w)
        rs_keys = {f"{w}d_bps": (int(round(rs_w[w])) if rs_w[w] is not None else None) for w in windows}
        ret_keys = {f"{w}d_pct": (round(ret_w[w], 4) if ret_w[w] is not None else None) for w in windows}
        nobs_keys = {f"{w}d": int(n_w[w]) for w in windows}
        results.append({
            "ticker": t,
            "rs_by_window": rs_keys,
            "return_by_window": ret_keys,
            "composite_rs_percentile": composite,
            "trend_label": label,
            "n_obs_per_window": nobs_keys,
        })

    def _sort_key(r: dict) -> float:
        c = r.get("composite_rs_percentile")
        return c if c is not None else -1.0

    sorted_results = sorted(results, key=_sort_key, reverse=True)
    ranked_tickers = [r["ticker"] for r in sorted_results if r.get("composite_rs_percentile") is not None]
    leaders_top_3 = ranked_tickers[:3]
    laggards_bottom_3 = list(reversed(ranked_tickers[-3:])) if len(ranked_tickers) >= 1 else []

    tier_caveats.append(
        "RS is past-return relative to benchmark; not predictive on its own. Pair with regime read."
    )
    if include_sectors:
        tier_caveats.append(
            f"Sector ETFs ranked alongside watchlist ({len(SECTOR_ETFS)} SPDR sector funds); leadership context, not stock picks."
        )
    for r in results:
        longest = max(windows)
        n_longest = r["n_obs_per_window"].get(f"{longest}d", 0)
        if n_longest < longest:
            tier_caveats.append(
                f"{r['ticker']}: only {n_longest} bars for {longest}d window; RS over that window reported as null."
            )

    return {
        "skill": "relative-strength",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "watchlist": tickers,
        "benchmark": benchmark,
        "windows_days": windows,
        "include_sectors": bool(include_sectors),
        "n_tickers": len(ranked_universe),
        "results": sorted_results,
        "ranking": {"leaders_top_3": leaders_top_3, "laggards_bottom_3": laggards_bottom_3},
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _fmt_bps(x: float | None) -> str:
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 else ""
    return f"{sign}{int(round(x))}bp"


def render(payload: dict) -> str:
    lines: list[str] = []
    bench = payload["benchmark"]
    wins = payload["windows_days"]
    n = payload["n_tickers"]
    win_str = "/".join(str(w) for w in wins)

    lines.append(f"Relative Strength vs {bench} — {payload['as_of']}")
    extra = " (incl. SPDR sectors)" if payload["include_sectors"] else ""
    lines.append(f"Watchlist: {n} tickers{extra} · Windows: {win_str} days")
    lines.append("")

    ticker_w, rs_w, trend_w, comp_w = 8, 10, 18, 10
    headers = [f"{'Ticker':<{ticker_w}}"]
    for w in wins:
        headers.append(f"{w}d RS".rjust(rs_w))
    headers.append(f"{'Trend':<{trend_w}}")
    headers.append(f"{'Comp %ile':>{comp_w}}")
    lines.append(" ".join(headers))
    total_width = ticker_w + (rs_w + 1) * len(wins) + 1 + trend_w + 1 + comp_w
    lines.append("-" * total_width)

    for r in payload["results"]:
        row = [f"{r['ticker']:<{ticker_w}}"]
        for w in wins:
            row.append(_fmt_bps(r["rs_by_window"].get(f"{w}d_bps")).rjust(rs_w))
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

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines)
