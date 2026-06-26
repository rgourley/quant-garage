#!/usr/bin/env python3
"""
Reference implementation of the options-flow skill.

Scans a watchlist for unusual options activity, classifies each notable
print as a sweep or block, infers direction from the NBBO context, and
emits two output layers from one analysis:

  Layer 1: canonical JSON matching skills/options-flow/output-schema.json
  Layer 2: Cheddar Flow / FlowAlgo-style stream rendered to
           examples/options-flow-output.md

Usage:
    python3 examples/run-options-flow.py             # default watchlist
    python3 examples/run-options-flow.py SPY TSLA    # custom watchlist

Reads MASSIVE_API_KEY from env, never from a file.
Writes output to examples/options-flow-output.md (gitignored).

Tier B run on Options Developer + Stocks Starter: 15-min delayed tape.
The methodology is identical to Tier A; only the timestamp recency
differs.

Audit cleanup (2026-06-26):
- H1, H2, L3, D3, D4, D5, M8: all routed through lib.quant_garage.
- C8: closed. Each contributing trade is classified against the NBBO at
  that trade's sip_timestamp (fetch_nbbo_at), not against a day-VWAP vs
  a single most-recent quote. Spike-window trades are pulled by
  timestamp.gte/lte so sweeps outside the most-recent 200 prints are no
  longer silently downgraded.
"""
import os
import sys
import json
import urllib.parse
from datetime import datetime, timedelta, timezone

# Make `lib.quant_garage` importable when running this script from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    resolve_price,
)


DEFAULT_WATCHLIST = ["AAPL", "NVDA", "TSLA", "AMD", "SPY"]
TICKERS = [t.upper().strip() for t in (sys.argv[1:] or DEFAULT_WATCHLIST)]

client = MassiveClient()

TODAY = today()
NOW_UTC = datetime.now(timezone.utc)

# Scan params (see references/unusual-activity-detection.md)
MAX_PRINTS = 20
MIN_VOL_AVG_RATIO = 3.0
MIN_PREMIUM_USD = 100_000
EXPIRY_WINDOW_DAYS = 60
STRIKE_BAND_PCT = 0.10

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

    Lifted from examples/run-best-ex-check.py's fetch_nbbo_at. Same
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


def classify_trades_against_nbbo(occ_ticker, contract_type, contrib):
    """For each contributing trade, fetch NBBO at trade time and tag it.

    Returns a dict:
      {
        "nbbo_tag": dominant_tag,          # volume-weighted majority
        "direction": dominant_direction,
        "nbbo_bid": representative_bid,    # from the largest trade
        "nbbo_ask": representative_ask,
        "per_trade": [{ ... }, ...],       # one entry per contrib trade
      }

    Replaces the old day-VWAP vs single most-recent quote comparison.
    Each trade is judged against the inside quote that was active at
    that trade's sip_timestamp.
    """
    if not contrib:
        return {
            "nbbo_tag": "unknown",
            "direction": "unknown",
            "nbbo_bid": None,
            "nbbo_ask": None,
            "per_trade": [],
        }

    per_trade = []
    # Volume-weighted tag tallies and a parallel direction tally
    tag_weight = {}
    dir_weight = {}
    for t in contrib:
        ts_ns = t.get("sip_timestamp") or t.get("participant_timestamp")
        bid, ask, _qts = fetch_nbbo_at(occ_ticker, ts_ns)
        price = t.get("price")
        size = t.get("size") or 0
        if bid is None or ask is None or price is None:
            tag = "unknown"
        else:
            tag = price_vs_nbbo(price, bid, ask)
        direction = inferred_direction(contract_type, tag)
        per_trade.append({
            "sip_timestamp": ts_ns,
            "price": price,
            "size": size,
            "bid": bid,
            "ask": ask,
            "tag": tag,
            "direction": direction,
        })
        tag_weight[tag] = tag_weight.get(tag, 0) + size
        dir_weight[direction] = dir_weight.get(direction, 0) + size

    # Dominant by volume, ignoring "unknown" if any concrete tag won size
    def dominant(weights):
        concrete = {k: v for k, v in weights.items() if k != "unknown" and v > 0}
        if concrete:
            return max(concrete.items(), key=lambda kv: kv[1])[0]
        return "unknown"

    nbbo_tag = dominant(tag_weight)
    direction = dominant(dir_weight)

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
        "nbbo_bid": rep_bid,
        "nbbo_ask": rep_ask,
        "per_trade": per_trade,
    }


def compute_score(vol_avg_ratio, vol_oi_ratio, premium_usd, chain_share):
    """See references/unusual-activity-detection.md."""
    return (
        (vol_avg_ratio / 3.0) * 0.40
        + (min(vol_oi_ratio or 0, 50) / 5.0) * 0.30
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

        vol_oi_ratio = (vol / oi) if oi and oi > 0 else (vol / 1.0)
        chain_share = (vol / chain_total_vol) if chain_total_vol > 0 else 0

        candidates.append({
            "occ": occ,
            "spot": (o.get("underlying_asset") or {}).get("price") or spot,
            "vol": vol,
            "oi": oi or 0,
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
        # day-VWAP vs single most-recent quote comparison.
        nbbo = classify_trades_against_nbbo(c["occ"], c["type"], contrib)
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
            "avg_volume_30d": avg_30d,
            "avg_volume_source": avg_source,
            "price_vs_nbbo": nbbo_tag,
            "nbbo_bid": bid,
            "nbbo_ask": ask,
            "inferred_direction": direction,
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


# ----- Run the scan -----

all_prints = []
skipped = []
for tk in TICKERS:
    try:
        prints, skip = scan_ticker(tk)
        all_prints.extend(prints)
        if skip:
            skipped.append(skip)
    except Exception as e:
        skipped.append({"ticker": tk, "reason": f"error: {e}"})

# Sort by score descending and cap at MAX_PRINTS
all_prints.sort(key=lambda p: p["score"], reverse=True)
all_prints = all_prints[:MAX_PRINTS]

# Build payload
payload = {
    "tier": "B",
    "tier_caveats": [
        "15-min delayed tape (Options Developer plan).",
        "NBBO is the inside quote at each contributing trade's sip_timestamp, fetched via /v3/quotes?timestamp.lte={ns} (30s lookback window). Tag and direction are volume-weighted across contributing trades.",
        "Volume/avg ratio computed against the 30-day daily aggregate; new weeklies use chain-median fallback.",
    ],
    "mode": "stream",
    "run_at": NOW_UTC.isoformat(),
    "scan_params": {
        "watchlist": TICKERS,
        "max_prints": MAX_PRINTS,
        "min_volume_to_avg_ratio": MIN_VOL_AVG_RATIO,
        "min_premium_usd": MIN_PREMIUM_USD,
        "expiry_window_days": EXPIRY_WINDOW_DAYS,
        "strike_band_pct": STRIKE_BAND_PCT,
    },
    "prints": all_prints,
    "skipped_tickers": skipped,
    "sources": [
        {"endpoint": "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}", "fetched_at": utcnow_iso(), "context": "spot price per ticker"},
        {"endpoint": "https://api.polygon.io/v3/snapshot/options/{ticker}", "fetched_at": utcnow_iso(), "context": "chain snapshot for volume/OI/IV/greeks"},
        {"endpoint": "https://api.polygon.io/v2/aggs/ticker/{occ_ticker}/range/1/day/{from}/{to}", "fetched_at": utcnow_iso(), "context": "30-day daily volume baseline per contract"},
        {"endpoint": "https://api.polygon.io/v3/trades/{occ_ticker}?timestamp.gte={start_ns}&timestamp.lte={end_ns}", "fetched_at": utcnow_iso(), "context": "session-window trades for sweep/block classification"},
        {"endpoint": "https://api.polygon.io/v3/quotes/{occ_ticker}?timestamp.lte={trade_ns}", "fetched_at": utcnow_iso(), "context": "NBBO at each contributing trade's sip_timestamp for direction inference"},
    ],
}


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

    line2_parts = [f"{vol_str} vol", f"{prem_str} prem", ratio_str]
    if nbbo_str:
        line2_parts.append(nbbo_str)
    if direction != "unknown":
        line2_parts.append(direction.upper())
    line2 = " · ".join(line2_parts)

    spot_str = f"spot ${p['spot_at_print']:.2f}" if p["spot_at_print"] else "spot n/a"
    oi_str = f"OI {p['oi_pre_trade']:,} ({p['oi_position_signal']})"
    iv_str = f"IV {round((p['iv_at_print'] or 0) * 100)}%" if p["iv_at_print"] else "IV n/a"
    line3 = f"{spot_str} · {oi_str} · {iv_str}"

    return "\n".join([line1, line2, line3])


lines = []
tier = payload["tier"]
header = (
    f"{len(TICKERS)} tickers scanned · {len(all_prints)} prints surfaced · "
    f"run {NOW_UTC.strftime('%Y-%m-%d %H:%M')} UTC · Tier {tier}"
)
lines.append(header)
if tier != "A":
    lines.append("Note: 15-min delayed tape (Options Developer). Latest prints from end-of-tape.")
lines.append("")

for p in all_prints:
    lines.append(render_block(p))
    lines.append("")

skipped_names = [s["ticker"] for s in skipped]
footer = f"End of stream. {len(all_prints)} prints across {len({p['ticker'] for p in all_prints})} tickers."
if skipped_names:
    footer += f" {len(skipped_names)} tickers skipped: {', '.join(skipped_names)}."
lines.append(footer)

rendered = "\n".join(lines)


# ----- Write output -----

out_name = "options-flow-output.md"
out_path = os.path.join(os.path.dirname(__file__), out_name)
with open(out_path, "w") as f:
    f.write("# options-flow run\n\n")
    f.write(f"Generated: {NOW_UTC.isoformat()}\n")
    f.write(f"Watchlist: {', '.join(TICKERS)}\n")
    f.write(f"Tier: {tier} (Options Developer + Stocks Starter, 15-min delayed)\n\n")
    f.write("## Layer 1: canonical JSON (live data)\n\n")
    f.write("```json\n")
    f.write(json.dumps(payload, indent=2, default=str))
    f.write("\n```\n\n")
    f.write("## Layer 2: rendered stream (live data)\n\n")
    f.write("```\n")
    f.write(rendered)
    f.write("\n```\n")

print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
print(rendered)
