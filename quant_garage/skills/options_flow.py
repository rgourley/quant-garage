"""
options-flow as an importable library function.

Watchlist scanner for unusual options activity. Classifies each notable
print as sweep or block, infers direction from NBBO context.

    from quant_garage.skills.options_flow import run, render
    payload = run(["SPY","TSLA","NVDA"])
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .. import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    resolve_price,
    percentile_rank,
    format_rank_label,
)


DEFAULT_WATCHLIST = ["AAPL", "NVDA", "TSLA", "AMD", "SPY"]

client = MassiveClient()

TODAY = today()
NOW_UTC = datetime.now(timezone.utc)

# Scan params (see references/unusual-activity-detection.md)
MAX_PRINTS = 20
MIN_VOL_AVG_RATIO = 3.0
MIN_PREMIUM_USD = 100_000
EXPIRY_WINDOW_DAYS = 60
STRIKE_BAND_PCT = 0.10

# When NBBO comes back empty (entitlement gap, sparse-quote contract, OCC
# format mismatch), fall back to a trade-price percentile heuristic against
# the contract's own day distribution. Less precise than NBBO but a real
# signal. Set False to revert to silent "unknown" tagging.
NBBO_FALLBACK_ENABLED = True

# Per-ticker block thresholds (see references/sweep-vs-block.md)
BLOCK_THRESHOLDS = {
    "SPY": 500, "QQQ": 500,
    "AAPL": 200, "TSLA": 200, "NVDA": 200, "META": 200,
    "AMZN": 150, "GOOGL": 150, "MSFT": 150,
    "AMD": 100,
}
DEFAULT_BLOCK_THRESHOLD = 100

# Conditions of interest (see references/sweep-vs-block.md)
SWEEP_CONDITIONS = {219, 228, 230}
EXCLUDE_CONDITIONS = {201, 202, 203, 204, 205, 206, 207}  # canceled/late
MULTI_LEG_CONDITIONS = set(range(232, 246))  # 232-245 multi-leg


def fetch_all(path, hard_cap=2000):
    """Follow next_url; cap results so a heavy chain doesn't run forever."""
    out = []
    fetched_at = None
    for results, ts in client.paginate(path):
        out.extend(results)
        fetched_at = ts
        if len(out) >= hard_cap:
            out = out[:hard_cap]
            break
    return out, fetched_at


def get_spot(ticker):
    """Walk best-price fallback chain via lib.resolve_price (D4/D5)."""
    try:
        doc, _ = client.get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
    except FetchError:
        return None
    return resolve_price(doc).price


def get_chain(ticker, spot):
    """Pull options chain limited to ±strike_band and expiry_window."""
    if not spot:
        return []
    strike_lo = int(spot * (1 - STRIKE_BAND_PCT))
    strike_hi = int(spot * (1 + STRIKE_BAND_PCT) + 1)
    exp_from = TODAY.isoformat()
    exp_to = (TODAY + timedelta(days=EXPIRY_WINDOW_DAYS)).isoformat()
    path = (
        f"/v3/snapshot/options/{ticker}"
        f"?expiration_date.gte={exp_from}&expiration_date.lte={exp_to}"
        f"&strike_price.gte={strike_lo}&strike_price.lte={strike_hi}"
        f"&limit=250"
    )
    rows, _ = fetch_all(path, hard_cap=500)
    return rows


def get_30d_avg_volume(occ_ticker):
    """Average daily volume over the last 30 trading days for a contract.

    Falls back to None when the contract has insufficient history (newly
    listed weekly), which the caller handles by substituting a chain-level
    benchmark.
    """
    end = (TODAY - timedelta(days=1)).isoformat()
    start = (TODAY - timedelta(days=45)).isoformat()
    path = (
        f"/v2/aggs/ticker/{occ_ticker}/range/1/day/{start}/{end}"
        f"?adjusted=true&sort=desc&limit=50"
    )
    try:
        doc, _ = client.get(path)
    except FetchError:
        return None
    results = doc.get("results") or []
    vols = [a.get("v") for a in results if a.get("v") is not None]
    if not vols:
        return None
    # Use up to last 30 sessions
    return sum(vols[:30]) / min(len(vols), 30)


# Session-window pull cap. Liquid weeklies on big names can print
# thousands of trades a session; 5000 keeps the fan-out bounded while
# still catching sweeps that fall outside the most-recent 200 prints
# (the bug C8 closed).
TRADES_WINDOW_HARD_CAP = 5000


def _session_window_ns():
    """Return (start_ns, end_ns) covering today's session in UTC.

    Uses a wide window (00:00 UTC -> now) rather than ET market hours so
    extended-hours options prints aren't dropped. Bounded by
    TRADES_WINDOW_HARD_CAP downstream so this isn't a runaway pull.
    """
    start_dt = datetime(TODAY.year, TODAY.month, TODAY.day, tzinfo=timezone.utc)
    start_ns = int(start_dt.timestamp() * 1_000_000_000)
    end_ns = int(NOW_UTC.timestamp() * 1_000_000_000)
    return start_ns, end_ns


def get_trades_window(occ_ticker, start_ns, end_ns, hard_cap=TRADES_WINDOW_HARD_CAP):
    """Pull trades inside [start_ns, end_ns] for one OCC ticker.

    Replaces the old `limit=200&order=desc` pull. Using timestamp.gte/lte
    means sweeps that landed earlier in the session are still considered
    rather than being silently truncated when the contract printed >200
    trades after the spike.
    """
    path = (
        f"/v3/trades/{occ_ticker}"
        f"?timestamp.gte={start_ns}&timestamp.lte={end_ns}"
        f"&order=asc&sort=timestamp&limit=1000"
    )
    out = []
    try:
        for results, _ in client.paginate(path):
            out.extend(results)
            if len(out) >= hard_cap:
                out = out[:hard_cap]
                break
    except FetchError:
        return out
    return out


def fetch_nbbo_at(occ_ticker, ts_ns):
    """Return the most-recent quote at or before ts_ns as (bid, ask, qts_iso).

    Lifted from examples/run-slippage-cost.py's fetch_nbbo_at. Same
    pattern: pull a small window of quotes ending at ts_ns, take the
    last one whose sip_timestamp <= ts_ns. The window approach (vs a
    single limit=1&order=desc with timestamp.lte) protects against
    sparse-quote contracts where the most-recent inside-the-window quote
    may sit a few hundred ms before the trade.
    """
    if ts_ns is None or ts_ns <= 0:
        return None, None, None
    # 30-second lookback. Options NBBOs tick every quote update; thin
    # weeklies might go a few seconds without a print, so 30s is enough
    # to find the active inside quote without burning pages.
    window_ns = 30 * 1_000_000_000
    start_ns = ts_ns - window_ns
    ts_minus_iso = (
        datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    ts_at_iso = (
        datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    path = (
        f"/v3/quotes/{occ_ticker}"
        f"?timestamp.gte={urllib.parse.quote(ts_minus_iso)}"
        f"&timestamp.lte={urllib.parse.quote(ts_at_iso)}"
        f"&order=desc&sort=timestamp&limit=50"
    )
    try:
        doc, _ = client.get(path)
    except FetchError:
        return None, None, None
    chosen = None
    for q in (doc.get("results") or []):
        sip_ts = q.get("sip_timestamp") or q.get("participant_timestamp")
        if sip_ts is None:
            continue
        if sip_ts <= ts_ns:
            chosen = q
            break
    if chosen is None:
        results = doc.get("results") or []
        if not results:
            return None, None, None
        chosen = results[-1]
    bid = chosen.get("bid_price")
    ask = chosen.get("ask_price")
    sip_ns = chosen.get("sip_timestamp") or chosen.get("participant_timestamp")
    qts = (
        datetime.fromtimestamp(sip_ns / 1_000_000_000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
        if sip_ns
        else None
    )
    return bid, ask, qts


def price_vs_nbbo(price, bid, ask):
    """See references/directional-inference.md for full logic."""
    if bid is None or ask is None or bid <= 0 or ask <= bid:
        return "unknown"
    mid = (bid + ask) / 2
    spread = ask - bid
    mid_band = max(0.01, spread * 0.10)
    if price > ask + 0.001:
        return "above_ask"
    if price >= ask - 0.01:
        return "at_ask"
    if abs(price - mid) <= mid_band:
        return "at_mid"
    if price <= bid + 0.01:
        return "at_bid"
    if price < bid:
        return "below_bid"
    return "at_ask" if (price - mid) > 0 else "at_bid"


def inferred_direction(contract_type, nbbo_tag):
    bullish = {("call", "above_ask"), ("call", "at_ask"),
               ("put", "at_bid"), ("put", "below_bid")}
    bearish = {("call", "at_bid"), ("call", "below_bid"),
               ("put", "above_ask"), ("put", "at_ask")}
    if (contract_type, nbbo_tag) in bullish:
        return "bullish"
    if (contract_type, nbbo_tag) in bearish:
        return "bearish"
    if nbbo_tag == "at_mid":
        return "neutral"
    return "unknown"


def position_signal(vol_oi_ratio, oi_pre):
    if vol_oi_ratio is None:
        return "unknown"
    # OI of 0 = strike just listed or never traded; all volume opens fresh
    if oi_pre is not None and oi_pre == 0:
        return "opening"
    if vol_oi_ratio > 1.0:
        return "opening"
    if vol_oi_ratio < 0.5:
        return "closing"
    return "mixed"


def classify_kind(trades, ticker, contract_volume):
    """Return (kind, contributing_trades, vwap).

    `trades` is the windowed session pull from get_trades_window. It
    can be unordered relative to sip_timestamp depending on the API
    page order, so each detection path sorts what it needs.
    """
    if not trades:
        # No trade history available; classify as 'other' with chain VWAP
        return "other", [], None

    # Filter excluded conditions
    clean = []
    for t in trades:
        conds = set(t.get("conditions") or [])
        if conds & EXCLUDE_CONDITIONS:
            continue
        if conds & MULTI_LEG_CONDITIONS:
            continue
        clean.append(t)

    # Sweep detection: 2+ ISO prints within 500ms on different exchanges
    iso = [t for t in clean if (set(t.get("conditions") or []) & SWEEP_CONDITIONS)]
    if len(iso) >= 2:
        # Group by 500ms windows
        iso_sorted = sorted(iso, key=lambda x: x.get("sip_timestamp", 0))
        for i in range(len(iso_sorted) - 1):
            ts0 = iso_sorted[i].get("sip_timestamp", 0)
            window = [t for t in iso_sorted if 0 <= t.get("sip_timestamp", 0) - ts0 <= 500_000_000]
            if len(window) >= 2:
                exchanges = {t.get("exchange") for t in window}
                if len(exchanges) >= 2:
                    total_size = sum(t.get("size", 0) for t in window)
                    if total_size > 0:
                        vwap = sum(t.get("size", 0) * t.get("price", 0) for t in window) / total_size
                        return "sweep", window, vwap
    elif iso:
        # Single ISO print; tag as sweep with single_leg flag
        total_size = sum(t.get("size", 0) for t in iso)
        if total_size > 0:
            vwap = sum(t.get("size", 0) * t.get("price", 0) for t in iso) / total_size
            return "sweep", iso, vwap

    # Block detection: single print at or above the per-ticker threshold
    block_thresh = BLOCK_THRESHOLDS.get(ticker, DEFAULT_BLOCK_THRESHOLD)
    blocks = [t for t in clean if t.get("size", 0) >= block_thresh]
    if blocks:
        biggest = max(blocks, key=lambda t: t.get("size", 0))
        return "block", [biggest], biggest.get("price")

    # Otherwise: 'other' (qualifying volume but no sweep/block).
    # Cap the contributing-trade sample at the 10 largest by size to
    # bound the per-print NBBO-fetch fan-out (each contrib trade costs
    # one /v3/quotes call below).
    if clean:
        total_size = sum(t.get("size", 0) for t in clean)
        if total_size > 0:
            vwap = sum(t.get("size", 0) * t.get("price", 0) for t in clean) / total_size
            biggest = sorted(clean, key=lambda t: t.get("size", 0), reverse=True)[:10]
            return "other", biggest, vwap
    return "other", [], None


def _percentile(sorted_values, p):
    """Linear-interpolation percentile on a pre-sorted list. p in [0, 1]."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = p * (len(sorted_values) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _heuristic_tag(price, p25, p50, p75):
    """Trade-price percentile against the contract's own day distribution.

    Above p75: trader paid up vs day's range → above_ask analog.
    Below p25: trader hit near bottom → below_bid analog.
    Otherwise: at_mid.
    """
    if price is None or p25 is None or p75 is None:
        return "unknown"
    if price >= p75:
        return "above_ask"
    if price <= p25:
        return "below_bid"
    return "at_mid"


def classify_trades_against_nbbo(occ_ticker, contract_type, contrib, all_contract_trades=None):
    """For each contributing trade, fetch NBBO at trade time and tag it.

    `all_contract_trades` is the full session-window pull for this contract,
    used to compute the trade-price percentile distribution for the
    fallback heuristic when NBBO is unavailable. Pass None to disable the
    fallback for this call (e.g. tests).

    Returns a dict:
      {
        "nbbo_tag": dominant_tag,          # volume-weighted majority
        "direction": dominant_direction,
        "direction_confidence": "high"|"medium"|"low"|"unknown",
        "direction_method_mix": {"nbbo_inside": N, "trade_price_heuristic": N, "unknown": N},
        "nbbo_bid": representative_bid,    # from the largest trade
        "nbbo_ask": representative_ask,
        "n_total_trades": int,
        "n_with_nbbo": int,
        "n_missing_nbbo": int,
        "per_trade": [{ ..., "direction_method": ... }, ...],
      }

    Replaces the old day-VWAP vs single most-recent quote comparison.
    Each trade is judged against the inside quote that was active at
    that trade's sip_timestamp; when no inside quote is available, falls
    back to a trade-price-percentile heuristic against the contract's
    own day's trade-price distribution.
    """
    empty = {
        "nbbo_tag": "unknown",
        "direction": "unknown",
        "direction_confidence": "unknown",
        "direction_method_mix": {"nbbo_inside": 0, "trade_price_heuristic": 0, "unknown": 0},
        "nbbo_bid": None,
        "nbbo_ask": None,
        "n_total_trades": 0,
        "n_with_nbbo": 0,
        "n_missing_nbbo": 0,
        "per_trade": [],
    }
    if not contrib:
        return empty

    # Per-contract day's trade-price distribution for the heuristic
    # fallback. Computed against ALL trades on this contract today (the
    # session window pull), not run-wide and not just the contributing
    # sample, so the percentile bands reflect this contract's own range.
    price_p25 = price_p50 = price_p75 = None
    if NBBO_FALLBACK_ENABLED and all_contract_trades:
        day_prices = sorted(
            t.get("price") for t in all_contract_trades
            if t.get("price") is not None and t.get("price") > 0
        )
        if day_prices:
            price_p25 = _percentile(day_prices, 0.25)
            price_p50 = _percentile(day_prices, 0.50)
            price_p75 = _percentile(day_prices, 0.75)

    per_trade = []
    n_total = 0
    n_with_nbbo = 0
    # Volume-weighted tag tallies and a parallel direction tally. Method
    # is tracked alongside so dominant() can half-weight heuristic calls.
    tag_weight = {}
    dir_weight_by_method = {"nbbo_inside": {}, "trade_price_heuristic": {}, "unknown": {}}
    method_counts = {"nbbo_inside": 0, "trade_price_heuristic": 0, "unknown": 0}

    for t in contrib:
        n_total += 1
        ts_ns = t.get("sip_timestamp") or t.get("participant_timestamp")
        bid, ask, _qts = fetch_nbbo_at(occ_ticker, ts_ns)
        price = t.get("price")
        size = t.get("size") or 0

        if bid is not None and ask is not None and price is not None:
            n_with_nbbo += 1
            tag = price_vs_nbbo(price, bid, ask)
            method = "nbbo_inside"
        elif NBBO_FALLBACK_ENABLED and price is not None and price_p25 is not None:
            tag = _heuristic_tag(price, price_p25, price_p50, price_p75)
            method = "trade_price_heuristic" if tag != "unknown" else "unknown"
        else:
            tag = "unknown"
            method = "unknown"

        direction = inferred_direction(contract_type, tag)
        per_trade.append({
            "sip_timestamp": ts_ns,
            "price": price,
            "size": size,
            "bid": bid,
            "ask": ask,
            "tag": tag,
            "direction": direction,
            "direction_method": method,
        })
        tag_weight[tag] = tag_weight.get(tag, 0) + size
        dir_weight_by_method[method][direction] = (
            dir_weight_by_method[method].get(direction, 0) + size
        )
        method_counts[method] += 1

    n_missing_nbbo = n_total - n_with_nbbo

    # Dominant by volume, ignoring "unknown" if any concrete tag won size.
    # For direction, NBBO-method gets full weight; heuristic-method gets
    # half weight so NBBO calls dominate when both are present.
    def dominant_tag(weights):
        concrete = {k: v for k, v in weights.items() if k != "unknown" and v > 0}
        if concrete:
            return max(concrete.items(), key=lambda kv: kv[1])[0]
        return "unknown"

    combined_dir = {}
    for d, w in dir_weight_by_method["nbbo_inside"].items():
        combined_dir[d] = combined_dir.get(d, 0) + w
    for d, w in dir_weight_by_method["trade_price_heuristic"].items():
        combined_dir[d] = combined_dir.get(d, 0) + w * 0.5

    nbbo_tag = dominant_tag(tag_weight)
    direction = dominant_tag(combined_dir)

    # Confidence reflects what method drove the dominant call.
    if n_total == 0:
        confidence = "unknown"
    else:
        nbbo_ratio = n_with_nbbo / n_total
        if nbbo_ratio >= 0.8:
            confidence = "high"
        elif nbbo_ratio >= 0.5:
            confidence = "medium"
        elif nbbo_ratio > 0:
            confidence = "low"
        else:
            # Pure heuristic
            confidence = "low" if method_counts["trade_price_heuristic"] > 0 else "unknown"

    # Representative bid/ask from the largest contributing trade with a
    # real quote, so the rendered output still shows a single NBBO band.
    rep = sorted(
        [p for p in per_trade if p["bid"] is not None and p["ask"] is not None],
        key=lambda p: p["size"],
        reverse=True,
    )
    rep_bid = rep[0]["bid"] if rep else None
    rep_ask = rep[0]["ask"] if rep else None

    return {
        "nbbo_tag": nbbo_tag,
        "direction": direction,
        "direction_confidence": confidence,
        "direction_method_mix": method_counts,
        "nbbo_bid": rep_bid,
        "nbbo_ask": rep_ask,
        "n_total_trades": n_total,
        "n_with_nbbo": n_with_nbbo,
        "n_missing_nbbo": n_missing_nbbo,
        "per_trade": per_trade,
    }


def compute_score(vol_avg_ratio, vol_oi_ratio, premium_usd, chain_share):
    """See references/unusual-activity-detection.md.

    When vol_oi_ratio is None (zero-OI / freshly-listed contract) the OI
    component is dropped and the remaining weights are renormalized so
    the score stays comparable to scored-with-OI candidates.
    """
    if vol_oi_ratio is None:
        # Drop the OI term and renormalize the other three weights
        # (0.40 + 0.20 + 0.10 = 0.70) up to 1.0.
        return (
            (vol_avg_ratio / 3.0) * (0.40 / 0.70)
            + (premium_usd / 1_000_000) * (0.20 / 0.70)
            + (chain_share / 0.05) * (0.10 / 0.70)
        )
    return (
        (vol_avg_ratio / 3.0) * 0.40
        + (min(vol_oi_ratio, 50) / 5.0) * 0.30
        + (premium_usd / 1_000_000) * 0.20
        + (chain_share / 0.05) * 0.10
    )


def parse_occ(occ_ticker):
    """Parse e.g. O:TSLA260718C00310000 → expiry, strike, type."""
    # Format: O:{TICKER}{YYMMDD}{C|P}{STRIKE*1000:08d}
    body = occ_ticker.split(":", 1)[1]
    # Find the date+type+strike (always 15 chars at the end: YYMMDD + C/P + 8 digits)
    tail = body[-15:]
    ticker = body[:-15]
    yy, mm, dd = tail[:2], tail[2:4], tail[4:6]
    expiry = f"20{yy}-{mm}-{dd}"
    cp = tail[6]
    strike_int = int(tail[7:])
    strike = strike_int / 1000.0
    return ticker, expiry, strike, "call" if cp == "C" else "put"


def scan_ticker(ticker):
    """Run the full scan for one ticker. Returns list of qualifying prints."""
    print(f"Scanning {ticker}...", file=sys.stderr)
    spot = get_spot(ticker)
    if not spot:
        return [], {"ticker": ticker, "reason": "no spot price"}
    chain = get_chain(ticker, spot)
    if not chain:
        return [], {"ticker": ticker, "reason": "no chain in strike/expiry band"}

    # Compute chain total volume for chain_share
    chain_total_vol = sum((o.get("day") or {}).get("volume") or 0 for o in chain)

    # Per-contract initial scoring using only snapshot data
    candidates = []
    for o in chain:
        day = o.get("day") or {}
        vol = day.get("volume") or 0
        oi = o.get("open_interest")
        det = o.get("details") or {}
        occ = det.get("ticker")
        if not occ or vol <= 0:
            continue

        # Filter expired/expiring contracts
        exp_str = det.get("expiration_date")
        if exp_str:
            from datetime import date as _date
            exp_d = _date.fromisoformat(exp_str)
            if exp_d < TODAY:
                continue

        # Approximate avg_trade_price for premium estimate
        avg_price = day.get("vwap") or day.get("close") or 0
        if avg_price <= 0:
            continue

        premium_usd = vol * avg_price * 100
        if premium_usd < MIN_PREMIUM_USD:
            continue

        # Zero-OI / freshly-listed contracts have no real open-interest
        # signal. Carry None through so compute_score and position_signal
        # drop the OI term explicitly rather than treating it as OI=1.
        if oi is None or oi <= 0:
            vol_oi_ratio = None
            zero_oi = True
        else:
            vol_oi_ratio = vol / oi
            zero_oi = False
        chain_share = (vol / chain_total_vol) if chain_total_vol > 0 else 0

        candidates.append({
            "occ": occ,
            "spot": (o.get("underlying_asset") or {}).get("price") or spot,
            "vol": vol,
            "oi": oi or 0,
            "zero_oi": zero_oi,
            "vol_oi_ratio": vol_oi_ratio,
            "premium_usd": premium_usd,
            "chain_share": chain_share,
            "iv": o.get("implied_volatility"),
            "avg_price": avg_price,
            "type": det.get("contract_type"),
            "expiry": exp_str,
            "strike": det.get("strike_price"),
        })

    # Take top 8 by raw premium for the deep dive (avg_volume requires
    # an extra call per contract; keep the fan-out bounded)
    candidates.sort(key=lambda c: c["premium_usd"], reverse=True)
    top_candidates = candidates[:8]

    prints = []
    for c in top_candidates:
        avg_30d = get_30d_avg_volume(c["occ"])
        if avg_30d is None or avg_30d <= 0:
            # Fall back to chain median for newly-listed contracts
            chain_vols = sorted(
                [(o.get("day") or {}).get("volume") or 0 for o in chain],
                reverse=True,
            )
            non_zero = [v for v in chain_vols if v > 0]
            avg_30d = (sum(non_zero[:20]) / max(1, len(non_zero[:20]))) if non_zero else c["vol"]
            avg_source = "chain_median_fallback"
        else:
            avg_source = "contract_aggs_30d"

        vol_avg_ratio = c["vol"] / avg_30d if avg_30d > 0 else 0
        if vol_avg_ratio < MIN_VOL_AVG_RATIO:
            continue

        # Pull all trades inside today's session window and classify the
        # qualifying subset (sweep / block / other). Using timestamp.gte/lte
        # means sweeps outside the most-recent 200 prints are no longer
        # silently downgraded (audit item C8).
        start_ns, end_ns = _session_window_ns()
        trades = get_trades_window(c["occ"], start_ns, end_ns)
        kind, contrib, vwap_classified = classify_kind(trades, ticker, c["vol"])

        # Use the classified print's price if available, else day vwap
        print_price = vwap_classified if vwap_classified else c["avg_price"]

        # Premium is the full day's premium on the contract: that's the
        # actionable dollar figure regardless of which print kind classified
        # the activity. The contributing trades are only the sample we used
        # to determine kind (sweep / block / other).
        premium_render = c["premium_usd"]

        # NBBO at trade time, per contributing trade. Replaces the old
        # day-VWAP vs single most-recent quote comparison. The full
        # session-window trade list is passed so the heuristic fallback
        # can compute the contract's own day-price percentiles.
        nbbo = classify_trades_against_nbbo(
            c["occ"], c["type"], contrib, all_contract_trades=trades,
        )
        nbbo_tag = nbbo["nbbo_tag"]
        direction = nbbo["direction"]
        bid = nbbo["nbbo_bid"]
        ask = nbbo["nbbo_ask"]

        score = compute_score(
            vol_avg_ratio=vol_avg_ratio,
            vol_oi_ratio=c["vol_oi_ratio"],
            premium_usd=premium_render,
            chain_share=c["chain_share"],
        )

        # Last trade timestamp from contrib if available
        if contrib:
            latest_ns = max((t.get("sip_timestamp") or 0) for t in contrib)
            ts_iso = (
                datetime.utcfromtimestamp(latest_ns / 1e9)
                .replace(tzinfo=timezone.utc)
                .isoformat()
            ) if latest_ns else NOW_UTC.isoformat()
            exchange = contrib[0].get("exchange") if len(contrib) == 1 else None
        else:
            ts_iso = NOW_UTC.isoformat()
            exchange = None

        prints.append({
            "ticker": ticker,
            "contract": {
                "expiry": c["expiry"],
                "strike": c["strike"],
                "type": c["type"],
                "occ_ticker": c["occ"],
            },
            "kind": kind,
            "premium_total_usd": premium_render,
            "print_price": print_price,
            "volume": c["vol"],
            "volume_to_avg_ratio": vol_avg_ratio,
            "volume_to_oi_ratio": c["vol_oi_ratio"],
            "zero_oi": c["zero_oi"],
            "avg_volume_30d": avg_30d,
            "avg_volume_source": avg_source,
            "price_vs_nbbo": nbbo_tag,
            "nbbo_bid": bid,
            "nbbo_ask": ask,
            "inferred_direction": direction,
            "direction_confidence": nbbo["direction_confidence"],
            "direction_method_mix": nbbo["direction_method_mix"],
            "nbbo_availability": {
                "n_total_trades": nbbo["n_total_trades"],
                "n_with_nbbo": nbbo["n_with_nbbo"],
                "n_missing_nbbo": nbbo["n_missing_nbbo"],
            },
            "spot_at_print": c["spot"],
            "iv_at_print": c["iv"],
            "oi_pre_trade": c["oi"],
            "oi_position_signal": position_signal(c["vol_oi_ratio"], c["oi"]),
            "timestamp": ts_iso,
            "exchange": exchange,
            "score": score,
            "related_prints": [],
            "contributing_trade_count": len(contrib),
        })

    return prints, None


# ----- Public API -----

def run(
    watchlist: Iterable[str] | str | None = None,
    client_: MassiveClient | None = None,
) -> dict:
    """Scan a watchlist for unusual options prints.

    Args:
        watchlist: comma-separated string or iterable of tickers.
                   Defaults to ["AAPL","NVDA","TSLA","AMD","SPY"].
    """
    global client, NOW_UTC, TODAY
    client = client_ or MassiveClient()
    NOW_UTC = datetime.now(timezone.utc)
    TODAY = today()

    if watchlist is None:
        tickers = list(DEFAULT_WATCHLIST)
    elif isinstance(watchlist, str):
        tickers = [t.strip().upper() for t in watchlist.split(",") if t.strip()]
    else:
        tickers = [t.strip().upper() for t in watchlist if t and t.strip()]
    if not tickers:
        raise ValueError("watchlist must contain at least one ticker")

    all_prints = []
    skipped = []
    for tk in tickers:
        try:
            prints, skip = scan_ticker(tk)
            all_prints.extend(prints)
            if skip:
                skipped.append(skip)
        except Exception as e:
            skipped.append({"ticker": tk, "reason": f"error: {e}"})

    # M9: build a run-wide score distribution (every qualifying print across
    # every ticker scanned) so each surfaced print can be ranked against the
    # whole run, not just the top-N. The distribution is captured BEFORE the
    # MAX_PRINTS truncation; otherwise the top score always ranks at 100.
    score_distribution = [p["score"] for p in all_prints if p.get("score") is not None]
    score_universe_n = len(score_distribution)

    # Sort by score descending and cap at MAX_PRINTS
    all_prints.sort(key=lambda p: p["score"], reverse=True)
    all_prints = all_prints[:MAX_PRINTS]

    # Attach percentile_rank + rank_label per surfaced print
    for _p in all_prints:
        pr = percentile_rank(_p["score"], score_distribution)
        _p["percentile_rank"] = pr
        _p["rank_label"] = format_rank_label(pr)
        if pr is None:
            _p["rank_reason"] = "insufficient_universe"
        _p["score_universe_n"] = score_universe_n

    zero_oi_count = sum(1 for p in all_prints if p.get("zero_oi"))

    # NBBO availability across all surfaced prints. Drives tier_caveats so a
    # run on an under-entitled key (or a sparse-quote watchlist) makes the
    # fallback explicit instead of silently rendering direction = OTHER.
    nbbo_total = sum((p.get("nbbo_availability") or {}).get("n_total_trades", 0) for p in all_prints)
    nbbo_have = sum((p.get("nbbo_availability") or {}).get("n_with_nbbo", 0) for p in all_prints)
    nbbo_missing = nbbo_total - nbbo_have

    tier_caveats = [
        "15-min delayed tape (Options Developer plan).",
        "NBBO is the inside quote at each contributing trade's sip_timestamp, fetched via /v3/quotes?timestamp.lte={ns} (30s lookback window). Tag and direction are volume-weighted across contributing trades.",
        "Volume/avg ratio computed against the 30-day daily aggregate; new weeklies use chain-median fallback.",
    ]
    if nbbo_total > 0:
        miss_ratio = nbbo_missing / nbbo_total
        if miss_ratio > 0.5:
            tier_caveats.append(
                f"NBBO unavailable on {nbbo_missing} of {nbbo_total} contributing trades "
                f"(likely entitlement: options quotes need Options Developer ($79/m) or higher). "
                f"Direction tagged via trade-price heuristic for those trades; "
                f"see per_trade.direction_method for which path was used."
            )
        elif miss_ratio > 0:
            tier_caveats.append(
                f"NBBO unavailable on {nbbo_missing} of {nbbo_total} contributing trades; "
                f"remaining sparse-quote trades tagged via trade-price heuristic."
            )

    # Build payload
    payload = {
        "tier": "B",
        "tier_caveats": tier_caveats,
        "mode": "stream",
        "run_at": NOW_UTC.isoformat(),
        "scan_params": {
            "watchlist": tickers,
            "max_prints": MAX_PRINTS,
            "min_volume_to_avg_ratio": MIN_VOL_AVG_RATIO,
            "min_premium_usd": MIN_PREMIUM_USD,
            "expiry_window_days": EXPIRY_WINDOW_DAYS,
            "strike_band_pct": STRIKE_BAND_PCT,
        },
        "prints": all_prints,
        "zero_oi_count": zero_oi_count,
        "skipped_tickers": skipped,
        "sources": [
            {"endpoint": "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}", "fetched_at": utcnow_iso(), "context": "spot price per ticker"},
            {"endpoint": "https://api.polygon.io/v3/snapshot/options/{ticker}", "fetched_at": utcnow_iso(), "context": "chain snapshot for volume/OI/IV/greeks"},
            {"endpoint": "https://api.polygon.io/v2/aggs/ticker/{occ_ticker}/range/1/day/{from}/{to}", "fetched_at": utcnow_iso(), "context": "30-day daily volume baseline per contract"},
            {"endpoint": "https://api.polygon.io/v3/trades/{occ_ticker}?timestamp.gte={start_ns}&timestamp.lte={end_ns}", "fetched_at": utcnow_iso(), "context": "session-window trades for sweep/block classification"},
            {"endpoint": "https://api.polygon.io/v3/quotes/{occ_ticker}?timestamp.lte={trade_ns}", "fetched_at": utcnow_iso(), "context": "NBBO at each contributing trade's sip_timestamp for direction inference"},
        ],
    }
    return payload


# ----- Render the stream -----

def fmt_premium(usd):
    if usd >= 1_000_000_000:
        return f"${usd / 1_000_000_000:.1f}B"
    if usd >= 1_000_000:
        return f"${usd / 1_000_000:.1f}M"
    if usd >= 1_000:
        return f"${usd / 1_000:.0f}K"
    return f"${usd:.0f}"


def fmt_strike(s):
    if s == int(s):
        return f"${int(s)}"
    return f"${s:.1f}"


def fmt_nbbo(tag):
    return {
        "above_ask": "ABOVE ASK",
        "at_ask": "ASK side",
        "at_mid": "MID",
        "at_bid": "BID side",
        "below_bid": "BELOW BID",
    }.get(tag)


def render_block(p):
    ticker = p["ticker"].ljust(4)
    contract = p["contract"]
    type_letter = "C" if contract["type"] == "call" else "P"
    strike_str = fmt_strike(contract["strike"])
    kind = p["kind"].upper()
    price = p["print_price"]
    price_str = f"${price:.2f}" if price else "n/a"

    line1 = f"{ticker}  {contract['expiry']}  {strike_str}{type_letter}  {kind}  @ {price_str}"

    vol_str = f"{p['volume']:,}"
    prem_str = fmt_premium(p["premium_total_usd"])
    ratio = p["volume_to_avg_ratio"]
    # Cap render at 100x; above that the denominator is unreliable
    # (newly-listed weekly, chain-median fallback). Source field in JSON
    # carries the actual value.
    if ratio >= 100:
        ratio_str = ">100x avg"
    elif ratio >= 10:
        ratio_str = f"{ratio:.0f}x avg"
    else:
        ratio_str = f"{ratio:.1f}x avg"
    nbbo_str = fmt_nbbo(p["price_vs_nbbo"])
    direction = p["inferred_direction"]
    confidence = p.get("direction_confidence") or "unknown"

    line2_parts = [f"{vol_str} vol", f"{prem_str} prem", ratio_str]
    if nbbo_str:
        line2_parts.append(nbbo_str)
    if direction != "unknown":
        dir_label = direction.upper()
        # Suffix when the dominant direction came from heuristic only.
        if confidence == "low":
            dir_label = f"{dir_label} (heuristic)"
        line2_parts.append(dir_label)
    # M9: rank suffix anchors the score against the run-wide distribution.
    pr = p.get("percentile_rank")
    universe_n = p.get("score_universe_n") or 0
    if pr is not None:
        line2_parts.append(f"{p['rank_label']} ({pr:.0f}th %ile, n={universe_n})")
    line2 = " · ".join(line2_parts)

    spot_str = f"spot ${p['spot_at_print']:.2f}" if p["spot_at_print"] else "spot n/a"
    if p.get("zero_oi"):
        oi_str = "OI: 0 (new)"
    else:
        oi_str = f"OI {p['oi_pre_trade']:,} ({p['oi_position_signal']})"
    iv_str = f"IV {round((p['iv_at_print'] or 0) * 100)}%" if p["iv_at_print"] else "IV n/a"
    line3 = f"{spot_str} · {oi_str} · {iv_str}"

    out_lines = [line1, line2, line3]

    # Surface method mix + confidence only when NBBO wasn't the clean
    # happy path (confidence != "high"). Quiet on entitled runs.
    if confidence != "high" and confidence != "unknown":
        mix = p.get("direction_method_mix") or {}
        avail = p.get("nbbo_availability") or {}
        total = avail.get("n_total_trades") or 0
        n_nbbo = mix.get("nbbo_inside") or 0
        n_heur = mix.get("trade_price_heuristic") or 0
        method_line = (
            f"  NBBO method: {n_nbbo}/{total} trades · "
            f"Heuristic method: {n_heur}/{total} trades · "
            f"Confidence: {confidence}"
        )
        out_lines.append(method_line)

    return "\n".join(out_lines)


def render(payload: dict) -> str:
    lines = []
    tier = payload["tier"]
    watchlist = payload["scan_params"]["watchlist"]
    prints = payload["prints"]
    skipped = payload.get("skipped_tickers", [])
    run_at = payload.get("run_at", "")

    header = (
        f"{len(watchlist)} tickers scanned · {len(prints)} prints surfaced · "
        f"run {run_at[:16].replace('T', ' ')} UTC · Tier {tier}"
    )
    lines.append(header)
    if tier != "A":
        lines.append("Note: 15-min delayed tape (Options Developer). Latest prints from end-of-tape.")
    lines.append("")

    for p in prints:
        lines.append(render_block(p))
        lines.append("")

    skipped_names = [s["ticker"] for s in skipped]
    footer = f"End of stream. {len(prints)} prints across {len({p['ticker'] for p in prints})} tickers."
    if skipped_names:
        footer += f" {len(skipped_names)} tickers skipped: {', '.join(skipped_names)}."
    lines.append(footer)

    return "\n".join(lines)
