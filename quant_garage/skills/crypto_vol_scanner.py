"""
crypto-vol-scanner as an importable library function.

Scans a crypto universe over the last N hours for realized-vol spikes,
volume anomalies, cross-exchange basis divergence, and tail moves.

    from quant_garage.skills.crypto_vol_scanner import run, render
    payload = run(universe="BTC,ETH,SOL", hours=24, top_n=10)
"""
from __future__ import annotations

import json
import sys
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .. import (
    MassiveClient,
    FetchError,
    utcnow_iso,
    percentile_rank,
    format_rank_label,
)


# MATIC -> POL (Polygon chain rebrand).
TICKER_REWRITES = {"MATIC": "POL"}
DEFAULT_UNIVERSE = "BTC,ETH,SOL,XRP,ADA,DOGE,AVAX,LINK,DOT,MATIC"

# Thresholds (kept as constants; mirrored in scan_params)
VOL_PERCENTILE_THRESHOLD = 0.90
VOLUME_ANOMALY_THRESHOLD = 2.0
MOVE_ZSCORE_THRESHOLD = 2.0
BASIS_BPS_THRESHOLD = 20.0
QUIET_VOL_PCT = 0.25
QUIET_VOLUME_RATIO = 0.7

# Realized-vol window: fixed N completed hourly bars ending at the last fully
# closed hour. Excludes the current partial hour to avoid run-time drift.
# Need at least MIN_BARS_24H complete bars before emitting a realized-vol stat;
# anything less is flagged with reason=insufficient_bars rather than published.
RV_WINDOW_BARS = 24
MIN_BARS_24H = 20

EXCHANGE_NAMES = {1: "Coinbase", 2: "Bitfinex", 6: "Bitstamp", 10: "Binance", 23: "Kraken"}


client = MassiveClient()


# -------- Math helpers --------

def stdev(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / n)


def percentile(value, distribution):
    """Fraction of distribution strictly less than value, in [0, 1]."""
    if not distribution:
        return 0.5
    below = sum(1 for x in distribution if x < value)
    return below / len(distribution)


def humanize_usd(v):
    if v is None:
        return "n/a"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def ordinal(n):
    """1 -> 1st, 2 -> 2nd, 3 -> 3rd, 22 -> 22nd, 11 -> 11th."""
    n = int(n)
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def humanize_price(p):
    if p is None:
        return "n/a"
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:.2f}"
    return f"${p:.4f}"


# -------- Step 1: bulk snapshot --------

def fetch_bulk_snapshot(tickers):
    qs = ",".join(tickers)
    body, fetched_at = client.get(
        "/v2/snapshot/locale/global/markets/crypto/tickers",
        {"tickers": qs},
    )
    by_ticker = {}
    for t in body.get("tickers") or []:
        by_ticker[t.get("ticker")] = t
    return by_ticker, fetched_at


# -------- Step 2: daily aggs (TTM) for return distribution + volume baseline --------

def fetch_daily_aggs(ticker, days_back=365):
    frm = (NOW_UTC - timedelta(days=days_back)).strftime("%Y-%m-%d")
    to = NOW_UTC.strftime("%Y-%m-%d")
    try:
        body, fetched_at = client.get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/{frm}/{to}",
            {"adjusted": "true", "sort": "asc", "limit": 5000},
        )
    except FetchError as e:
        print(f"  warn: daily aggs failed for {ticker}: {e}", file=sys.stderr)
        return [], None
    return body.get("results") or [], fetched_at


# -------- Step 3: hourly aggs for realized vol --------

def fetch_hourly_aggs(ticker, days_back=32):
    frm = (NOW_UTC - timedelta(days=days_back)).strftime("%Y-%m-%d")
    to = NOW_UTC.strftime("%Y-%m-%d")
    try:
        body, fetched_at = client.get(
            f"/v2/aggs/ticker/{ticker}/range/1/hour/{frm}/{to}",
            {"adjusted": "true", "sort": "asc", "limit": 50000},
        )
    except FetchError as e:
        print(f"  warn: hourly aggs failed for {ticker}: {e}", file=sys.stderr)
        return [], None
    return body.get("results") or [], fetched_at


# -------- Step 4: per-exchange trades --------

def fetch_recent_trades(ticker, limit=200):
    try:
        body, fetched_at = client.get(
            f"/v3/trades/{ticker}",
            {"limit": limit, "order": "desc"},
        )
    except FetchError as e:
        print(f"  warn: trades fetch failed for {ticker}: {e}", file=sys.stderr)
        return [], None
    return body.get("results") or [], fetched_at


def per_exchange_basis(trades):
    """Return (basis_bps, exchanges_compared, high_ex_name, low_ex_name)."""
    if not trades:
        return None, [], None, None
    by_ex = defaultdict(list)
    for t in trades:
        x = t.get("exchange")
        p = t.get("price")
        if x is None or p is None:
            continue
        by_ex[x].append((t.get("participant_timestamp") or 0, p))
    if len(by_ex) < 2:
        # Only one exchange in the most-recent window; basis not measurable.
        details = []
        for x, ticks in by_ex.items():
            ticks.sort(reverse=True)  # most recent first
            details.append({
                "exchange_id": x,
                "exchange_name": EXCHANGE_NAMES.get(x, f"X{x}"),
                "last_price": ticks[0][1],
                "trade_count": len(ticks),
            })
        return None, details, None, None

    last_prices = {}
    for x, ticks in by_ex.items():
        ticks.sort(reverse=True)
        last_prices[x] = (ticks[0][1], len(ticks))

    sorted_by_price = sorted(last_prices.items(), key=lambda kv: kv[1][0])
    low_x, (low_p, _) = sorted_by_price[0]
    high_x, (high_p, _) = sorted_by_price[-1]
    mid = (low_p + high_p) / 2
    basis_bps = ((high_p - low_p) / mid) * 10000 if mid > 0 else None

    exchanges_compared = [
        {
            "exchange_id": x,
            "exchange_name": EXCHANGE_NAMES.get(x, f"X{x}"),
            "last_price": price,
            "trade_count": count,
        }
        for x, (price, count) in sorted(last_prices.items())
    ]
    return basis_bps, exchanges_compared, EXCHANGE_NAMES.get(high_x, f"X{high_x}"), EXCHANGE_NAMES.get(low_x, f"X{low_x}")


# -------- Realized vol --------

def realized_vol_from_closes(closes, periods_per_year, min_bars=3):
    """Annualized realized vol from a list of close prices.

    Requires at least `min_bars` close observations (which yields min_bars-1
    log returns). Returns None if undersized or if no valid log returns
    can be computed.
    """
    if len(closes) < min_bars:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if not rets:
        return None
    sigma = stdev(rets)
    return sigma * math.sqrt(periods_per_year) * 100


def completed_hourly_closes(hourly_aggs, now_utc):
    """Closes from hourly bars whose hour has fully elapsed.

    Massive emits the in-progress hour as a partial bar; including it makes
    realized vol drift with run time. We filter to bars whose start
    timestamp (`t`, ms epoch) is strictly before the current hour bucket.
    Bars without a usable timestamp are dropped to stay conservative.
    """
    current_hour_start_ms = int(
        now_utc.replace(minute=0, second=0, microsecond=0).timestamp() * 1000
    )
    closes = []
    for b in hourly_aggs:
        t = b.get("t")
        c = b.get("c")
        if t is None or c is None:
            continue
        if t >= current_hour_start_ms:
            continue
        closes.append(c)
    return closes


def rolling_realized_vol_distribution(closes, window=RV_WINDOW_BARS):
    """Compute rolling annualized realized vol over `window`-bar windows."""
    if len(closes) < window + 1:
        return []
    vols = []
    for end in range(window, len(closes)):
        window_closes = closes[end - window : end + 1]
        v = realized_vol_from_closes(window_closes, 24 * 365)
        if v is not None:
            vols.append(v)
    return vols


# -------- Public API --------

def run(
    universe: Iterable[str] | str = DEFAULT_UNIVERSE,
    hours: int = 24,
    top_n: int = 15,
    client_: MassiveClient | None = None,
) -> dict:
    """Scan a crypto universe for vol spikes, volume anomalies, basis divergence, tail moves.

    Args:
        universe: comma-separated base currencies (MATIC auto-substitutes to POL).
        hours: lookback window.
        top_n: max events to emit.
    """
    global client, NOW_UTC
    client = client_ or MassiveClient()
    NOW_UTC = datetime.now(timezone.utc)

    if isinstance(universe, str):
        raw_universe = [t.strip().upper() for t in universe.split(",") if t.strip()]
    else:
        raw_universe = [t.strip().upper() for t in universe if t and t.strip()]
    if not raw_universe:
        raise ValueError("universe must contain at least one currency")
    UNIVERSE = [TICKER_REWRITES.get(t, t) for t in raw_universe]
    TICKERS_RESOLVED = [f"X:{base}USD" for base in UNIVERSE]
    WINDOW_HOURS = hours
    TOP_N = top_n

    print(f"Scanning {len(TICKERS_RESOLVED)} crypto tickers...", file=sys.stderr)

    # 1. Bulk snapshot
    print("  fetching bulk snapshot", file=sys.stderr)
    snapshots, snapshot_fetched_at = fetch_bulk_snapshot(TICKERS_RESOLVED)
    print(f"  got {len(snapshots)} snapshots", file=sys.stderr)

    # Per-ticker compute
    events = []
    skipped = []

    # Track per-endpoint last fetched_at for the sources block. Per-call provenance
    # (M8) is the right pattern; we record the latest landing of each endpoint
    # class across the loop.
    last_daily_fetched_at = None
    last_hourly_fetched_at = None
    last_trades_fetched_at = None

    for base, ticker in zip(UNIVERSE, TICKERS_RESOLVED):
        print(f"  processing {ticker}", file=sys.stderr)
        snap = snapshots.get(ticker)
        if not snap:
            skipped.append({"ticker": ticker, "reason": "no snapshot"})
            continue

        last_trade = snap.get("lastTrade") or {}
        day = snap.get("day") or {}
        prev_day = snap.get("prevDay") or {}
        minute = snap.get("min") or {}

        # Spot via fallback chain. Crypto snapshot uses the same shape as stocks,
        # so the canonical fallback (lastTrade.p → min.c → day.c → prevDay.c) holds.
        # We inline it here rather than calling resolve_price() because resolve_price
        # expects the v2/snapshot/locale/.../tickers/{T} response shape (with a
        # top-level `ticker` key); the bulk crypto endpoint returns the ticker
        # blocks directly under `tickers`.
        spot = None
        spot_source = None
        for field, src in [
            ("p", "lastTrade.p"),
        ]:
            v = last_trade.get(field)
            if v:
                spot, spot_source = v, src
                break
        if spot is None:
            for blk, src in [(minute, "min.c"), (day, "day.c"), (prev_day, "prevDay.c")]:
                v = blk.get("c")
                if v:
                    spot, spot_source = v, src
                    break

        if spot is None or not prev_day.get("c"):
            skipped.append({"ticker": ticker, "reason": "missing spot or prevDay close"})
            continue

        # 24h move (vs prevDay.c)
        prev_close = prev_day.get("c")
        move_24h_pct = (spot / prev_close) - 1
        log_ret_24h = math.log(spot / prev_close) if prev_close > 0 else 0

        # 2. Daily aggs for return distribution + volume baseline
        daily, daily_fetched_at = fetch_daily_aggs(ticker, days_back=60)
        if daily_fetched_at:
            last_daily_fetched_at = daily_fetched_at
        daily_closes = [b.get("c") for b in daily if b.get("c")]
        daily_log_rets = []
        for i in range(1, len(daily_closes)):
            if daily_closes[i - 1] > 0:
                daily_log_rets.append(math.log(daily_closes[i] / daily_closes[i - 1]))
        # Take the last 30 daily returns for the σ
        sigma_30d = stdev(daily_log_rets[-30:]) if len(daily_log_rets) >= 5 else 0
        move_zscore = (log_ret_24h / sigma_30d) if sigma_30d > 0 else 0

        # USD volume baseline: last 30 trading days, daily.v * daily.vw
        daily_volumes_usd = []
        for b in daily[-30:]:
            v = b.get("v")
            vw = b.get("vw") or b.get("c")
            if v and vw:
                daily_volumes_usd.append(v * vw)
        volume_30d_avg = sum(daily_volumes_usd) / len(daily_volumes_usd) if daily_volumes_usd else None

        # Current 24h USD volume from prevDay
        prev_v = prev_day.get("v")
        prev_vw = prev_day.get("vw") or prev_close
        volume_24h_usd = (prev_v * prev_vw) if (prev_v and prev_vw) else None
        volume_vs_avg_ratio = (volume_24h_usd / volume_30d_avg) if (volume_24h_usd and volume_30d_avg) else 1.0

        # 3. Hourly aggs for realized vol.
        # Fixed 24-bar window over the most recent COMPLETED hourly bars only.
        # Excluding the in-progress bar keeps the metric run-time-invariant.
        # If fewer than MIN_BARS_24H bars are available (low-liquidity coin or
        # endpoint hiccup), we emit None + reason=insufficient_bars rather than
        # publish a garbage stat over 3-5 bars.
        hourly, hourly_fetched_at = fetch_hourly_aggs(ticker, days_back=32)
        if hourly_fetched_at:
            last_hourly_fetched_at = hourly_fetched_at
        hourly_closes_completed = completed_hourly_closes(hourly, NOW_UTC)
        rv_window_closes = hourly_closes_completed[-(RV_WINDOW_BARS + 1):]
        # n_bars = number of completed close observations feeding the calc
        # (returns = n_bars - 1). Capped at RV_WINDOW_BARS + 1 = 25.
        rv_n_bars = len(rv_window_closes)
        if rv_n_bars >= MIN_BARS_24H:
            rv_24h = realized_vol_from_closes(rv_window_closes, 24 * 365, min_bars=MIN_BARS_24H)
            rv_reason = None if rv_24h is not None else "insufficient_bars"
        else:
            rv_24h = None
            rv_reason = "insufficient_bars"
        rv_distribution_30d = rolling_realized_vol_distribution(hourly_closes_completed, window=RV_WINDOW_BARS)
        vol_percentile = percentile(rv_24h, rv_distribution_30d) if (rv_24h is not None and rv_distribution_30d) else 0.5
        rv_avg_30d = sum(rv_distribution_30d) / len(rv_distribution_30d) if rv_distribution_30d else None
        vol_vs_avg_ratio = (rv_24h / rv_avg_30d) if (rv_24h and rv_avg_30d) else 1.0

        # 4. Per-exchange trades
        trades, trades_fetched_at = fetch_recent_trades(ticker, limit=200)
        if trades_fetched_at:
            last_trades_fetched_at = trades_fetched_at
        basis_bps, exchanges_compared, high_ex, low_ex = per_exchange_basis(trades)

        # Signals fired
        signals_fired = []
        if vol_percentile >= VOL_PERCENTILE_THRESHOLD:
            signals_fired.append("vol_spike")
        if volume_vs_avg_ratio >= VOLUME_ANOMALY_THRESHOLD:
            signals_fired.append("volume_anomaly")
        if abs(move_zscore) >= MOVE_ZSCORE_THRESHOLD:
            signals_fired.append("tail_move")
        if basis_bps and basis_bps >= BASIS_BPS_THRESHOLD:
            signals_fired.append("cross_exchange")
        if vol_percentile <= QUIET_VOL_PCT and volume_vs_avg_ratio <= QUIET_VOLUME_RATIO:
            signals_fired.append("quiet")

        # Dominant signal type
        if not signals_fired:
            signal_type = "quiet" if vol_percentile < 0.5 else "tail_move"
        elif len(signals_fired) == 1:
            signal_type = signals_fired[0]
        elif "quiet" in signals_fired and len(signals_fired) == 1:
            signal_type = "quiet"
        elif len([s for s in signals_fired if s != "quiet"]) >= 2:
            signal_type = "combined"
        else:
            # Priority order
            for s in ["vol_spike", "volume_anomaly", "tail_move", "cross_exchange"]:
                if s in signals_fired:
                    signal_type = s
                    break
            else:
                signal_type = signals_fired[0]

        # Composite score (max-of-normalized)
        vol_score = max(0.0, (vol_percentile - 0.5) * 2) if vol_percentile is not None else 0
        volume_score = max(0.0, math.log(max(volume_vs_avg_ratio, 1.0)) / math.log(5)) if volume_vs_avg_ratio else 0
        move_score = min(1.0, abs(move_zscore) / 4.0)
        basis_score = min(1.0, basis_bps / 50.0) if basis_bps else 0.0
        composite_score = max(vol_score, volume_score, move_score, basis_score)

        # Context line (one-line trader read)
        context_line = None
        if signal_type == "combined" and "vol_spike" in signals_fired and "volume_anomaly" in signals_fired:
            context_line = f"vol AND volume both elevated; directional flow"
        elif signal_type == "vol_spike" and rv_avg_30d:
            context_line = f"30d realized {rv_avg_30d:.0f}%, current ~{vol_vs_avg_ratio:.1f}x baseline"
        elif signal_type == "volume_anomaly":
            context_line = f"{volume_vs_avg_ratio:.1f}x 30d avg volume; flow regime change"
        elif signal_type == "cross_exchange" and high_ex and low_ex:
            context_line = f"{high_ex} bid heavier than {low_ex}; persistent venue divergence"
        elif signal_type == "tail_move":
            context_line = f"{abs(move_zscore):.1f}σ move; check for catalyst"
        elif signal_type == "quiet" and "quiet" in signals_fired:
            context_line = "unusually quiet; calm-before-storm watch"
        elif signal_type == "quiet":
            # Below-median but didn't meet strict quiet rule; honest about it
            context_line = f"sub-median activity; {ordinal(round(vol_percentile*100))}-%ile RV, {volume_vs_avg_ratio:.1f}x vol"

        events.append({
            "ticker": ticker,
            "base_currency": base,
            "quote_currency": "USD",
            "signal_type": signal_type,
            "signals_fired": signals_fired,
            "spot": spot,
            "spot_source": spot_source,
            "move_24h_pct": round(move_24h_pct, 6),
            "move_zscore": round(move_zscore, 3),
            "realized_vol_24h_pct": round(rv_24h, 2) if rv_24h is not None else None,
            "realized_vol_n_bars": rv_n_bars,
            "realized_vol_reason": rv_reason,
            "vol_percentile_30d": round(vol_percentile, 3),
            "vol_vs_avg_ratio": round(vol_vs_avg_ratio, 3) if vol_vs_avg_ratio else None,
            "volume_24h_usd": round(volume_24h_usd, 2) if volume_24h_usd else None,
            "volume_vs_avg_ratio": round(volume_vs_avg_ratio, 3) if volume_vs_avg_ratio else None,
            "basis_bps": round(basis_bps, 2) if basis_bps is not None else None,
            "exchanges_compared": exchanges_compared,
            "basis_high_exchange": high_ex,
            "basis_low_exchange": low_ex,
            "context_line": context_line,
            "composite_score": round(composite_score, 4),
        })

    # M9: build a run-wide composite-score distribution from EVERY event
    # (the full universe scan), so each top event can be ranked against the
    # whole run. Captured before the TOP_N truncation.
    score_distribution = [
        e["composite_score"] for e in events if e.get("composite_score") is not None
    ]
    score_universe_n = len(score_distribution)

    # Sort by composite_score descending
    events.sort(key=lambda e: e["composite_score"], reverse=True)
    top_events = events[:TOP_N]

    # Attach percentile_rank + rank_label per surfaced event
    for _e in top_events:
        pr = percentile_rank(_e["composite_score"], score_distribution)
        _e["percentile_rank"] = pr
        _e["rank_label"] = format_rank_label(pr)
        if pr is None:
            _e["rank_reason"] = "insufficient_universe"
        _e["score_universe_n"] = score_universe_n

    # Summary
    signal_counts = defaultdict(int)
    for e in top_events:
        signal_counts[e["signal_type"]] += 1
    rvs = [e["realized_vol_24h_pct"] for e in events if e["realized_vol_24h_pct"] is not None]
    median_rv = sorted(rvs)[len(rvs) // 2] if rvs else None
    # Count across the FULL universe, not just top_events — a coin that failed the
    # min-sample guard usually has a low composite score and won't surface in the
    # top list, but the operator still needs to know how many were dropped.
    insufficient_bars_count = sum(
        1 for e in events if e.get("realized_vol_reason") == "insufficient_bars"
    )

    # Take: 1-2 sentence crypto desk read
    strong_signals = [e for e in top_events if e["composite_score"] >= 0.4]
    if strong_signals:
        top1 = strong_signals[0]
        sign_text = "+" if top1["move_24h_pct"] >= 0 else ""
        take_parts = [
            f"{top1['base_currency']} leads with {top1['signal_type'].replace('_', ' ')}: "
            f"{sign_text}{top1['move_24h_pct']*100:.1f}% on {ordinal(round(top1['vol_percentile_30d']*100))}-%ile realized vol"
        ]
        if len(strong_signals) > 1:
            take_parts.append(
                f"{len(strong_signals) - 1} other {'name shows' if len(strong_signals) == 2 else 'names show'} above-threshold anomalies"
            )
        if median_rv is not None:
            regime = "elevated" if median_rv >= 60 else ("normal" if median_rv >= 40 else "quiet")
            take_parts.append(f"universe median RV {median_rv:.0f}% ({regime} regime)")
        take = ". ".join(take_parts) + "."
    elif median_rv is not None:
        take = (
            f"Quiet across the universe: no threshold breaches. "
            f"Median RV {median_rv:.0f}%; watch for vol expansion."
        )
    else:
        take = "Universe quiet; no events crossed threshold."

    # Tier
    tier = "A"  # All paid keys return real-time crypto + tick trades
    tier_caveats = []

    payload = {
        "tier": tier,
        "tier_caveats": tier_caveats,
        "mode": "stream",
        "run_at": utcnow_iso(),
        "scan_params": {
            "universe": raw_universe,
            "tickers_resolved": TICKERS_RESOLVED,
            "window_hours": WINDOW_HOURS,
            "top_n": TOP_N,
            "vol_spike_percentile_threshold": VOL_PERCENTILE_THRESHOLD,
            "volume_anomaly_threshold": VOLUME_ANOMALY_THRESHOLD,
            "move_zscore_threshold": MOVE_ZSCORE_THRESHOLD,
            "basis_bps_threshold": BASIS_BPS_THRESHOLD,
            "rv_window_bars": RV_WINDOW_BARS,
            "rv_min_bars": MIN_BARS_24H,
        },
        "events": top_events,
        "summary": {
            "count": len(top_events),
            "by_signal": dict(signal_counts),
            "median_realized_vol_pct": round(median_rv, 2) if median_rv is not None else None,
            "insufficient_bars_count": insufficient_bars_count,
        },
        "take": take,
        "skipped_tickers": skipped,
        "sources": [
            {
                "endpoint": "https://api.polygon.io/v2/snapshot/locale/global/markets/crypto/tickers",
                "fetched_at": snapshot_fetched_at,
                "context": "Bulk snapshot for the resolved universe",
            },
            {
                "endpoint": "https://api.polygon.io/v2/aggs/ticker/{X:BASEUSD}/range/1/day/{from}/{to}",
                "fetched_at": last_daily_fetched_at,
                "context": "Daily aggregates per ticker for return distribution and volume baseline",
            },
            {
                "endpoint": "https://api.polygon.io/v2/aggs/ticker/{X:BASEUSD}/range/1/hour/{from}/{to}",
                "fetched_at": last_hourly_fetched_at,
                "context": "Hourly aggregates per ticker for current 24h realized vol and trailing 30d distribution",
            },
            {
                "endpoint": "https://api.polygon.io/v3/trades/{X:BASEUSD}?limit=200&order=desc",
                "fetched_at": last_trades_fetched_at,
                "context": "Recent tick-level trades per ticker for cross-exchange basis",
            },
        ],
    }
    return payload


# -------- Render --------

def render_block(e):
    base = e["base_currency"]
    quote = e["quote_currency"]
    pair = f"{base}-{quote}"
    sig = e["signal_type"]
    tag_map = {
        "vol_spike": "VOL SPIKE",
        "volume_anomaly": "VOLUME ANOMALY",
        "cross_exchange": "CROSS-EXCHANGE",
        "tail_move": "TAIL MOVE",
        "quiet": "QUIET",
        "combined": "COMBINED",
    }
    tag = tag_map.get(sig, sig.upper())

    # Signal-specific summary on line 1
    rv = e["realized_vol_24h_pct"]
    vol_pct = e["vol_percentile_30d"]
    vol_ratio = e["vol_vs_avg_ratio"]
    vol_24h_usd = e["volume_24h_usd"]
    vol_ratio_v = e["volume_vs_avg_ratio"]
    basis = e["basis_bps"]

    if sig == "vol_spike":
        summary = f"realized 24h: {rv:.0f}% ({ordinal(round(vol_pct*100))} %ile TTM, {vol_ratio:.1f}x avg)"
    elif sig == "volume_anomaly":
        summary = f"24h {humanize_usd(vol_24h_usd)} ({vol_ratio_v:.1f}x trailing 30d avg)"
    elif sig == "cross_exchange":
        high_ex = e["basis_high_exchange"]
        low_ex = e["basis_low_exchange"]
        ex_lookup = {ex["exchange_name"]: ex["last_price"] for ex in (e["exchanges_compared"] or [])}
        high_p = ex_lookup.get(high_ex, 0)
        low_p = ex_lookup.get(low_ex, 0)
        summary = f"{high_ex} {humanize_price(high_p)} · {low_ex} {humanize_price(low_p)} ({basis:.0f}bps)"
    elif sig == "tail_move":
        pct_str = f"{e['move_24h_pct']*100:+.1f}%"
        summary = f"{pct_str} 24h ({abs(e['move_zscore']):.1f}σ vs 30d)"
    elif sig == "quiet":
        if rv is not None:
            summary = f"realized 24h: {rv:.0f}% ({ordinal(round(vol_pct*100))} %ile TTM)"
        else:
            summary = "realized 24h: n/a"
    elif sig == "combined":
        # Pick dominant non-quiet signal
        primary = None
        for s in ["vol_spike", "volume_anomaly", "tail_move", "cross_exchange"]:
            if s in (e["signals_fired"] or []):
                primary = s
                break
        if primary == "vol_spike":
            primary_text = f"realized 24h: {rv:.0f}% ({ordinal(round(vol_pct*100))} %ile TTM, {vol_ratio:.1f}x avg)"
        elif primary == "volume_anomaly":
            primary_text = f"24h {humanize_usd(vol_24h_usd)} ({vol_ratio_v:.1f}x trailing 30d avg)"
        elif primary == "tail_move":
            primary_text = f"{e['move_24h_pct']*100:+.1f}% 24h ({abs(e['move_zscore']):.1f}σ)"
        elif primary == "cross_exchange":
            primary_text = f"basis {basis:.0f}bps {e['basis_high_exchange']}/{e['basis_low_exchange']}"
        else:
            primary_text = "multiple signals"
        secondaries = [s for s in (e["signals_fired"] or []) if s != primary and s != "quiet"]
        sec_texts = []
        for s in secondaries:
            if s == "volume_anomaly":
                sec_texts.append(f"volume {vol_ratio_v:.1f}x avg")
            elif s == "vol_spike":
                sec_texts.append(f"RV {ordinal(round(vol_pct*100))} %ile")
            elif s == "tail_move":
                sec_texts.append(f"{abs(e['move_zscore']):.1f}σ move")
            elif s == "cross_exchange":
                sec_texts.append(f"basis {basis:.0f}bps")
        summary = f"{primary_text}" + (f" · also {' + '.join(sec_texts)}" if sec_texts else "")
    else:
        summary = ""

    line1 = f"{pair}  {tag}  {summary}"

    # Line 2: spot · 24h move · 24h vol · realized vol
    parts = [humanize_price(e["spot"])]
    parts.append(f"24h move {e['move_24h_pct']*100:+.1f}% ({abs(e['move_zscore']):.1f}σ)")
    if vol_24h_usd is not None:
        parts.append(f"24h vol {humanize_usd(vol_24h_usd)} ({vol_ratio_v:.1f}x avg)")
    if rv is not None:
        parts.append(f"realized vol {rv:.0f}%")
    # M9: rank suffix anchors the composite score against the run-wide universe.
    pr = e.get("percentile_rank")
    universe_n = e.get("score_universe_n") or 0
    if pr is not None:
        parts.append(f"{e['rank_label']} ({pr:.0f}th %ile, n={universe_n})")
    line2 = " · ".join(parts)

    block = [line1, line2]
    if e["context_line"]:
        block.append(f"↳ {e['context_line']}")
    return "\n".join(block)


def render(payload: dict) -> str:
    events = payload["events"]
    universe = payload["scan_params"]["universe"]
    hours = payload["scan_params"]["window_hours"]
    tier = payload["tier"]
    summary = payload.get("summary", {})
    median_rv = summary.get("median_realized_vol_pct")
    skipped = payload.get("skipped_tickers", [])
    run_at = payload.get("run_at", "")

    lines = []
    header = (
        f"{len(events)} events surfaced from {len(universe)} names · "
        f"window: last {hours}h · "
        f"run {run_at[:16].replace('T', ' ')} UTC"
    )
    lines.append(header)
    if tier == "B":
        lines.append("Note: cross-exchange basis on Crypto Starter may reflect 15-min-delayed prints.")
    lines.append("")

    for e in events:
        lines.append(render_block(e))
        lines.append("")

    footer = f"End of stream. {len(events)} events across {len(universe)} names."
    if median_rv is not None:
        footer += f" Universe median RV: {median_rv:.0f}%."
    lines.append(footer)
    if skipped:
        skip_str = ", ".join(s["ticker"] for s in skipped)
        lines.append(f"Skipped: {skip_str}.")

    return "\n".join(lines)
