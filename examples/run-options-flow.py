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
"""
import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict


DEFAULT_WATCHLIST = ["AAPL", "NVDA", "TSLA", "AMD", "SPY"]
TICKERS = [t.upper().strip() for t in (sys.argv[1:] or DEFAULT_WATCHLIST)]

KEY = os.environ.get("MASSIVE_API_KEY")
if not KEY:
    print("ERROR: MASSIVE_API_KEY not set", file=sys.stderr)
    sys.exit(1)

BASE = "https://api.polygon.io"
HEADERS = {"Authorization": f"Bearer {KEY}"}
TODAY = date(2026, 6, 23)
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


def fetch(path):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode("utf-8", errors="replace")
        raise RuntimeError(f"{e.code} on {path}: {body}")


def fetch_all(path, hard_cap=2000):
    """Follow next_url; cap results so a heavy chain doesn't run forever."""
    out = []
    url = f"{BASE}{path}"
    while url and len(out) < hard_cap:
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


def get_spot(ticker):
    """Walk best-price fallback chain per massive-api-patterns."""
    snap = fetch(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
    t = snap.get("ticker") or {}
    last = t.get("lastTrade") or {}
    if last.get("p"):
        return last["p"]
    day = t.get("day") or {}
    if day.get("c"):
        return day["c"]
    prev = t.get("prevDay") or {}
    if prev.get("c"):
        return prev["c"]
    return None


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
    return fetch_all(path, hard_cap=500)


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
        doc = fetch(path)
    except RuntimeError:
        return None
    results = doc.get("results") or []
    vols = [a.get("v") for a in results if a.get("v") is not None]
    if not vols:
        return None
    # Use up to last 30 sessions
    return sum(vols[:30]) / min(len(vols), 30)


def get_trades(occ_ticker, limit=200):
    """Pull recent trades for sweep/block classification."""
    path = f"/v3/trades/{occ_ticker}?limit={limit}&order=desc"
    try:
        doc = fetch(path)
    except RuntimeError:
        return []
    return doc.get("results") or []


def get_last_quote(occ_ticker):
    """Pull the most recent quote (NBBO) for the contract."""
    path = f"/v3/quotes/{occ_ticker}?limit=1&order=desc"
    try:
        doc = fetch(path)
    except RuntimeError:
        return None
    results = doc.get("results") or []
    return results[0] if results else None


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
    """Return (kind, contributing_trades, vwap)."""
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

    # Otherwise: 'other' (qualifying volume but no sweep/block)
    if clean:
        total_size = sum(t.get("size", 0) for t in clean)
        if total_size > 0:
            vwap = sum(t.get("size", 0) * t.get("price", 0) for t in clean) / total_size
            return "other", clean[:10], vwap
    return "other", [], None


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
            exp_d = date.fromisoformat(exp_str)
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

        # Pull trades and last quote for sweep/block + NBBO classification
        trades = get_trades(c["occ"], limit=200)
        kind, contrib, vwap_classified = classify_kind(trades, ticker, c["vol"])
        quote = get_last_quote(c["occ"])

        # Use the classified print's price if available, else day vwap
        print_price = vwap_classified if vwap_classified else c["avg_price"]

        # Premium is the full day's premium on the contract: that's the
        # actionable dollar figure regardless of which print kind classified
        # the activity. The contributing trades are only the sample we used
        # to determine kind (sweep / block / other).
        premium_render = c["premium_usd"]

        bid = quote.get("bid_price") if quote else None
        ask = quote.get("ask_price") if quote else None
        # Only classify NBBO when the quote and the day's VWAP are in the
        # same order of magnitude. A $0.40 print vs a stale $30 quote
        # would produce garbage direction tags.
        if bid and ask and print_price and bid > 0:
            mid = (bid + ask) / 2
            if mid > 0 and 0.2 <= (print_price / mid) <= 5.0:
                nbbo_tag = price_vs_nbbo(print_price, bid, ask)
            else:
                nbbo_tag = "unknown"
        else:
            nbbo_tag = "unknown"
        direction = inferred_direction(c["type"], nbbo_tag)

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
        "NBBO from last quote on the contract, not the exact print millisecond.",
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
        {"endpoint": "/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}", "fetched_at": NOW_UTC.isoformat(), "context": "spot price per ticker"},
        {"endpoint": "/v3/snapshot/options/{ticker}", "fetched_at": NOW_UTC.isoformat(), "context": "chain snapshot for volume/OI/IV/greeks"},
        {"endpoint": "/v2/aggs/ticker/{occ_ticker}/range/1/day/{from}/{to}", "fetched_at": NOW_UTC.isoformat(), "context": "30-day daily volume baseline per contract"},
        {"endpoint": "/v3/trades/{occ_ticker}", "fetched_at": NOW_UTC.isoformat(), "context": "tick trades for sweep/block classification"},
        {"endpoint": "/v3/quotes/{occ_ticker}", "fetched_at": NOW_UTC.isoformat(), "context": "last NBBO for direction inference"},
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
