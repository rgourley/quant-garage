#!/usr/bin/env python3
"""
Reference implementation of the slippage-cost skill.

Takes a CSV of executed fills and runs fill-vs-NBBO slippage analysis
against the NBBO at the fill timestamp. Flags fills that crossed the
spread, traded off-NBBO, paid through a wide spread, slipped versus
session VWAP, or experienced adverse selection in the 30 seconds after
the fill.

Not true Implementation Shortfall: IS compares against the arrival /
decision-time benchmark; this compares against NBBO at fill time. The
input CSV doesn't carry an arrival timestamp.

Two tiers:
  A: /v3/quotes/{ticker} returns 200 -> use microsecond NBBO ticks
  B: /v3/quotes/{ticker} returns 403 -> fall back to 1-second
                                        aggregate bars as NBBO proxy

Usage:
    python3 examples/run-slippage-cost.py examples/sample-fills.csv

Reads MASSIVE_API_KEY from env, never from a file.
Writes JSON and rendered exception report to examples/slippage-cost-output.md
"""
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# Make `lib.quant_garage` importable when running this script from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    FetchError,
    ET,
    utc_to_et,
    utcnow_iso,
    resolve_output_format,
    emit_to_stdout,
)

# ----- Config -----

# Flag thresholds (see references/flag-categories.md)
WIDE_SPREAD_BPS = 50.0
HIGH_VWAP_SLIPPAGE_BPS = 25.0
ADVERSE_SELECTION_BPS = 5.0

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "slippage-cost-output.md")

client = MassiveClient()


# ----- HTTP helpers -----

def fetch(path, params=None):
    """Wrap client.get to preserve the old (doc, status) shape callers expect.
    On HTTP errors returns ({}, status_code) so the tier probe can detect 403.
    """
    try:
        doc, _ = client.get(path, params=params)
        return doc, 200
    except FetchError as e:
        return {"_error": str(e)}, (e.status_code or 0)


def now_iso():
    return utcnow_iso()


def abs_url(path):
    """Promote a /v3/... path to the canonical polygon.io URL for citations."""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"https://api.polygon.io{path}"


# ----- CSV loading -----

def load_fills(path):
    fills = []
    with open(path) as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp"].strip())
            if ts.tzinfo is None:
                # Naive timestamps are assumed ET. zoneinfo handles DST so
                # winter-half-of-year fills no longer mis-bucket by an hour.
                ts = ts.replace(tzinfo=ET)
            fills.append({
                "ticker": row["ticker"].strip().upper(),
                "side": row["side"].strip().upper(),
                "qty": float(row["qty"]),
                "price": float(row["price"]),
                "timestamp": ts,
            })
    return fills


# ----- Tier probe -----

def probe_quote_data(ticker):
    """
    Try /v3/quotes/{ticker} with a tiny limit. Returns "A" if it
    returns NBBO data, "B" otherwise.
    """
    path = f"/v3/quotes/{ticker}?limit=1"
    doc, status = fetch(path)
    if status == 200 and isinstance(doc.get("results"), list):
        return "A"
    return "B"


# ----- Reference quote pulls -----

def fetch_nbbo_at(ticker, ts_dt):
    """
    Tier A: pull the real NBBO at the trade timestamp from /v3/quotes
    using `timestamp.lte={fill_ns}&order=desc&limit=1` (M2). Return
    (bid, ask, quote_ts_iso, endpoint).

    Primary query is the literal audit spec: most recent quote at or
    before the fill timestamp. If the single-row response is empty
    (thin-quote names where the latest quote is older than the
    default API window), retry with a 60-second backstop window so
    the comparison still has a reference quote.
    """
    fill_ns = int(ts_dt.timestamp() * 1_000_000_000)
    path = (
        f"/v3/quotes/{ticker}"
        f"?timestamp.lte={fill_ns}"
        f"&order=desc&sort=timestamp&limit=1"
    )
    doc, status = fetch(path)
    chosen = None
    if status == 200:
        results = doc.get("results", []) or []
        if results:
            chosen = results[0]

    if chosen is None:
        # Backstop: widen the lookback to 60 seconds. Some thin names
        # don't quote every second; without this, fetch_nbbo_at can
        # return None for fills that did have a stale-but-real NBBO.
        ts_minus_ns = fill_ns - 60 * 1_000_000_000
        path = (
            f"/v3/quotes/{ticker}"
            f"?timestamp.gte={ts_minus_ns}"
            f"&timestamp.lte={fill_ns}"
            f"&order=desc&sort=timestamp&limit=1"
        )
        doc, status = fetch(path)
        if status != 200:
            return None, None, None, path
        results = doc.get("results", []) or []
        if not results:
            return None, None, None, path
        chosen = results[0]

    bid = chosen.get("bid_price")
    ask = chosen.get("ask_price")
    sip_ns = chosen.get("sip_timestamp") or chosen.get("participant_timestamp")
    qts = None
    if sip_ns:
        qts = datetime.fromtimestamp(sip_ns / 1_000_000_000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return bid, ask, qts, path


def fetch_nbbo_proxy_at(ticker, ts_dt):
    """
    Tier B: pull 1-second aggregates straddling ts_dt. Return
    (proxy_bid, proxy_ask, bar_ts_iso, endpoint).
    """
    start = (ts_dt - timedelta(seconds=1)).astimezone(timezone.utc)
    end = (ts_dt + timedelta(seconds=1)).astimezone(timezone.utc)
    from_str = start.strftime("%Y-%m-%d")
    to_str = end.strftime("%Y-%m-%d")
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/second/{from_str}/{to_str}"
        f"?adjusted=true&sort=asc&limit=50000"
    )
    # Narrow request with timestamp filter
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    path_narrow = (
        f"/v2/aggs/ticker/{ticker}/range/1/second/{start_ms}/{end_ms}"
        f"?adjusted=true&sort=asc&limit=10"
    )
    doc, status = fetch(path_narrow)
    if status != 200 or not doc.get("results"):
        return None, None, None, path_narrow
    fill_ms = int(ts_dt.timestamp() * 1000)
    # Find the bar covering fill_ms (bar.t <= fill_ms < bar.t + 1000)
    chosen = None
    for bar in doc["results"]:
        if bar["t"] <= fill_ms < bar["t"] + 1000:
            chosen = bar
            break
    if chosen is None:
        # Use the closest bar
        chosen = min(doc["results"], key=lambda b: abs(b["t"] - fill_ms))
    bar_ts_iso = datetime.fromtimestamp(chosen["t"] / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return chosen["l"], chosen["h"], bar_ts_iso, path_narrow


# ----- Session VWAP -----

_vwap_cache = {}


def fetch_session_vwap(ticker, ts_dt):
    """
    Pull minute aggregates for the session and compute cumulative VWAP
    up to ts_dt. Cached per (ticker, date).
    """
    date_str = utc_to_et(ts_dt).strftime("%Y-%m-%d")
    key = (ticker, date_str)
    if key not in _vwap_cache:
        path = (
            f"/v2/aggs/ticker/{ticker}/range/1/minute/{date_str}/{date_str}"
            f"?adjusted=true&sort=asc&limit=50000"
        )
        doc, status = fetch(path)
        if status != 200:
            _vwap_cache[key] = ([], path)
        else:
            _vwap_cache[key] = (doc.get("results", []) or [], path)
    bars, path = _vwap_cache[key]
    if not bars:
        return None, path
    fill_ms = int(ts_dt.timestamp() * 1000)
    vol_x_vw = 0.0
    vol = 0.0
    for bar in bars:
        if bar["t"] > fill_ms:
            break
        v = bar.get("v") or 0.0
        vw = bar.get("vw") or bar.get("c") or 0.0
        vol_x_vw += v * vw
        vol += v
    if vol == 0:
        return None, path
    return vol_x_vw / vol, path


# ----- Adverse selection -----

def fetch_adverse_drift(ticker, ts_dt):
    """
    Pull 1-second aggregates for [ts_dt, ts_dt + 30s] and return the
    last bar's close as final_price.
    """
    start = ts_dt.astimezone(timezone.utc)
    end = ts_dt + timedelta(seconds=30)
    end_utc = end.astimezone(timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/second/{start_ms}/{end_ms}"
        f"?adjusted=true&sort=asc&limit=100"
    )
    doc, status = fetch(path)
    if status != 200 or not doc.get("results"):
        return None, path
    last = doc["results"][-1]
    return last.get("c"), path


# ----- Slippage math -----

def bps_diff(a, b):
    if b == 0 or b is None:
        return None
    return (a - b) / b * 10000.0


def signed_slippage(side, fill_price, reference_price):
    raw = bps_diff(fill_price, reference_price)
    if raw is None:
        return None
    return raw if side == "BUY" else -raw


def signed_vwap_slip(side, fill_price, vwap):
    raw = bps_diff(fill_price, vwap)
    if raw is None:
        return None
    return raw if side == "BUY" else -raw


def signed_adverse(side, fill_price, final_price):
    if final_price is None:
        return None
    # BUY: adverse if price falls -> positive when fill > final
    # SELL: adverse if price rises -> positive when final > fill
    if side == "BUY":
        return (fill_price - final_price) / fill_price * 10000.0
    return (final_price - fill_price) / fill_price * 10000.0


def spread_bps(bid, ask):
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return (ask - bid) / mid * 10000.0


# ----- Per-fill processing -----

def process_fill(fill, tier, sources):
    ticker = fill["ticker"]
    side = fill["side"]
    fill_price = fill["price"]
    qty = fill["qty"]
    ts_dt = fill["timestamp"]

    if tier == "A":
        bid, ask, qts, qpath = fetch_nbbo_at(ticker, ts_dt)
    else:
        bid, ask, qts, qpath = fetch_nbbo_proxy_at(ticker, ts_dt)

    if bid is not None and ask is not None:
        sources.append({"endpoint": abs_url(qpath), "fetched_at": now_iso(), "ticker": ticker})

    reference_price = ask if side == "BUY" else bid

    slip = signed_slippage(side, fill_price, reference_price) if reference_price else None
    spread = spread_bps(bid, ask)

    vwap, vpath = fetch_session_vwap(ticker, ts_dt)
    if vwap is not None:
        sources.append({"endpoint": abs_url(vpath), "fetched_at": now_iso(), "ticker": ticker})
    vslip = signed_vwap_slip(side, fill_price, vwap) if vwap else None

    final_price, apath = fetch_adverse_drift(ticker, ts_dt)
    if final_price is not None:
        sources.append({"endpoint": abs_url(apath), "fetched_at": now_iso(), "ticker": ticker})
    adverse = signed_adverse(side, fill_price, final_price)

    # Apply flag categories.
    #
    # NBBO bucket assignment is mutually exclusive (M1). Each fill lands in
    # exactly ONE of:
    #   1. crossed_spread  -- worst case: BUY > ask + 20bps, SELL < bid - 20bps
    #                         (paid through the inside by a material amount)
    #   2. off_nbbo        -- outside NBBO but not crossed by enough to be
    #                         a "paid through" violation: 0 < slip <= 20bps
    #                         and printed outside the inside
    #   3. on_nbbo         -- printed at or inside the NBBO band; no violation
    #
    # Priority order: crossed_spread > off_nbbo > on_nbbo. Bucket
    # percentages sum to 100% of fills that had a usable reference quote.
    # The other reasons (wide_spread_at_fill, high_vwap_slippage,
    # adverse_selection) are independent context flags and may co-occur.
    reasons = []
    nbbo_bucket = None
    outside_nbbo = (
        (side == "BUY" and ask is not None and fill_price > ask)
        or (side == "SELL" and bid is not None and fill_price < bid)
    )
    if slip is not None and outside_nbbo and slip > 20:
        nbbo_bucket = "crossed_spread"
        reasons.append("crossed_spread")
    elif slip is not None and outside_nbbo and slip > 0:
        nbbo_bucket = "off_nbbo_buy" if side == "BUY" else "off_nbbo_sell"
        reasons.append(nbbo_bucket)
    elif bid is not None and ask is not None:
        nbbo_bucket = "on_nbbo"
        # Not appended to `reasons` -- on_nbbo is the clean bucket and
        # does not by itself flag a fill. Tracked separately for the
        # 100%-sum invariant in the summary.

    if spread is not None and spread > WIDE_SPREAD_BPS:
        reasons.append("wide_spread_at_fill")
    if vslip is not None and vslip > HIGH_VWAP_SLIPPAGE_BPS:
        reasons.append("high_vwap_slippage")
    if adverse is not None and adverse > ADVERSE_SELECTION_BPS:
        reasons.append("adverse_selection")

    if not reasons:
        # Return the NBBO bucket so main() can tally the 100%-sum
        # crossed_spread / off_nbbo / on_nbbo distribution across all fills,
        # not just flagged ones. Caller checks the `flagged` flag.
        return {"flagged": False, "nbbo_bucket": nbbo_bucket}

    impl_shortfall = 0.0
    if slip is not None:
        impl_shortfall = abs(slip) / 10000.0 * fill_price * qty

    suggest = suggest_action(reasons, slip, vslip, adverse, spread)

    return {
        "flagged": True,
        "nbbo_bucket": nbbo_bucket,
        "ticker": ticker,
        "side": side,
        "qty": qty,
        "price": fill_price,
        "timestamp": ts_dt.isoformat(),
        "reference_price": reference_price,
        "reference_bid": bid,
        "reference_ask": ask,
        "slippage_bps": round(slip, 2) if slip is not None else None,
        "spread_bps_at_fill": round(spread, 2) if spread is not None else None,
        "session_vwap": round(vwap, 4) if vwap is not None else None,
        "vwap_slippage_bps": round(vslip, 2) if vslip is not None else None,
        "adverse_selection_bps": round(adverse, 2) if adverse is not None else None,
        "reasons": reasons,
        "implementation_shortfall_usd": round(impl_shortfall, 2),
        "suggested_next_action": suggest,
        "source": {
            "endpoint": abs_url(qpath),
            "fetched_at": qts or now_iso(),
        },
    }


def suggest_action(reasons, slip, vslip, adverse, spread):
    rs = set(reasons)
    if rs == {"wide_spread_at_fill"}:
        return "No clear fill-vs-NBBO violation; trader took available liquidity in thin tape"
    if "off_nbbo_buy" in rs or "off_nbbo_sell" in rs:
        return "Trade printed outside NBBO proxy; verify timestamp accuracy and check for block/dark print carveout"
    if "adverse_selection" in rs and "crossed_spread" in rs:
        return "Paid up into adverse flow; classic toxic-fill pattern; track venue and counterparty"
    if "crossed_spread" in rs and "high_vwap_slippage" in rs:
        return "Investigate execution timing and venue choice; cost was material to portfolio"
    if "crossed_spread" in rs:
        return "Investigate venue routing; price improvement opportunity missed"
    if "adverse_selection" in rs:
        return "Counterparty likely had information; track venue and counterparty for pattern"
    if "high_vwap_slippage" in rs:
        return "Fill timing diverged from VWAP; review parent-order strategy"
    return "Review fill context"


# ----- Rendering -----

def fmt_qty(x):
    return f"{int(x):,}" if x == int(x) else f"{x:,.2f}"


def spread_label(bps):
    if bps is None:
        return "n/a"
    if bps < 10:
        return "normal"
    if bps <= 50:
        return "medium"
    return "wide"


def adverse_label(bps):
    if bps is None:
        return "no post-fill prints"
    if bps < ADVERSE_SELECTION_BPS:
        return "no clear adverse selection"
    if bps < 15:
        return "mild adverse"
    if bps < 50:
        return "meaningful adverse"
    return "severe adverse"


def render(payload):
    out = []
    if payload.get("take"):
        out.append(payload["take"])
        out.append("")

    fills_checked = payload["fills_checked"]
    flagged_count = payload["flagged_count"]
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    if flagged_count == 0:
        out.append(f"TCA: {fills_checked} fills checked · No breaks flagged · run {as_of} UTC")
    else:
        out.append(f"TCA: {fills_checked} fills checked · {flagged_count} BREAKs flagged · run {as_of} UTC")
    out.append("")

    sp = payload["scan_params"]
    out.append(
        f"Universe: {fills_checked} fills from {sp['file_in']} "
        f"{sp['date_range']['from']} to {sp['date_range']['to']}"
    )
    qsrc = payload["quote_source"]
    qlabel = "v3 NBBO ticks (microsecond precision)" if qsrc == "v3_quotes" else "1-second aggs (NBBO proxy)"
    out.append(f"Reference quote source: {qlabel}")

    if payload["tier"] == "B":
        out.append("")
        out.append(
            "Note: /v3/quotes returned 403 on this key; falling back to 1-second"
        )
        out.append(
            "aggregate band as NBBO proxy. Bias: under-counts off-NBBO and"
        )
        out.append(
            "crossed_spread, over-counts on-NBBO (off-NBBO is a lower bound)."
        )

    if flagged_count == 0:
        return "\n".join(out) + "\n"

    out.append("")
    out.append(f"FLAGGED FILLS ({flagged_count})")
    out.append("")

    for i, f in enumerate(payload["flagged"], 1):
        ts_et = utc_to_et(datetime.fromisoformat(f["timestamp"])).strftime("%Y-%m-%d %H:%M:%S")
        header = (
            f"BREAK {i}: {f['ticker']} {f['side']} {fmt_qty(f['qty'])} "
            f"@ ${f['price']:.2f} · {ts_et} ET"
        )
        if f["reasons"] == ["wide_spread_at_fill"] and f["spread_bps_at_fill"] is not None:
            header += f" (in {f['spread_bps_at_fill']:.0f}bps spread window)"
        out.append(header)

        # Slippage line
        if f["slippage_bps"] is None:
            out.append("  Slippage:    not computable (no reference quote)")
        else:
            ref_label = "ask" if f["side"] == "BUY" else "bid"
            sign = "+" if f["slippage_bps"] >= 0 else "-"
            ref_ts = utc_to_et(datetime.fromisoformat(f["source"]["fetched_at"].replace("Z", "+00:00"))).strftime("%H:%M:%S")
            out.append(
                f"  Slippage:    {sign}{abs(f['slippage_bps']):.1f} bps vs reference "
                f"{ref_label} ${f['reference_price']:.2f} at {ref_ts}"
            )

        # Spread line
        if f["reference_bid"] is not None and f["reference_ask"] is not None and f["spread_bps_at_fill"] is not None:
            out.append(
                f"  Spread:      ${f['reference_bid']:.2f} × ${f['reference_ask']:.2f} "
                f"({f['spread_bps_at_fill']:.0f} bps inside, {spread_label(f['spread_bps_at_fill'])})"
            )

        # VWAP line
        if f["vwap_slippage_bps"] is not None and f["session_vwap"] is not None:
            sign = "+" if f["vwap_slippage_bps"] >= 0 else "-"
            out.append(
                f"  VWAP slip:   {sign}{abs(f['vwap_slippage_bps']):.1f} bps vs session VWAP "
                f"${f['session_vwap']:.2f}"
            )

        # Reasons
        out.append(f"  Reasons:     {', '.join(f['reasons'])}")

        # Adverse
        if f["adverse_selection_bps"] is not None:
            sign = "+" if f["adverse_selection_bps"] >= 0 else "-"
            out.append(
                f"  Adverse:     {sign}{abs(f['adverse_selection_bps']):.1f} bps within 30s of fill "
                f"({adverse_label(f['adverse_selection_bps'])})"
            )

        # Suggest
        out.append(f"  Suggest:     {f['suggested_next_action']}")
        out.append("")

    # Summary
    s = payload["summary"]
    out.append("Summary")
    out.append(f"- {flagged_count} flagged of {fills_checked} ({s['break_rate_pct']:.0f}% break rate)")
    by = s["by_reason"]
    if by.get("crossed_spread"):
        avg = s.get("avg_crossed_spread_bps")
        if avg is not None:
            out.append(f"- Crossed-spread: {by['crossed_spread']} fills · avg cost +{avg:.0f}bps")
        else:
            out.append(f"- Crossed-spread: {by['crossed_spread']} fills")
    if by.get("high_vwap_slippage"):
        out.append(f"- VWAP slippage: {by['high_vwap_slippage']} fills")
    off_total = by.get("off_nbbo_buy", 0) + by.get("off_nbbo_sell", 0)
    if off_total:
        out.append(f"- Off-NBBO: {off_total} fills")
    if by.get("adverse_selection"):
        out.append(f"- Adverse selection: {by['adverse_selection']} fills")
    if by.get("wide_spread_at_fill"):
        out.append(
            f"- Wide-spread context: {by['wide_spread_at_fill']} fills "
            f"(genuinely thin moments, not violations)"
        )
    out.append(
        f"- Estimated implementation shortfall: "
        f"${s['total_implementation_shortfall_usd']:,.0f} across all flagged fills"
    )

    # NBBO bucket distribution (M1): mutually exclusive, sums to 100%.
    pct = s.get("nbbo_bucket_pct") or {}
    if pct:
        out.append("")
        out.append(
            f"NBBO bucket distribution ({s['nbbo_bucket_total']} fills with reference quote)"
        )
        out.append(f"- crossed_spread: {pct.get('crossed_spread', 0.0):.1f}%")
        out.append(f"- off_nbbo:       {pct.get('off_nbbo', 0.0):.1f}%")
        out.append(f"- on_nbbo:        {pct.get('on_nbbo', 0.0):.1f}%")

    if payload["tier"] == "B":
        out.append("")
        out.append(
            "Methodology note: Tier B uses 1-second aggregate bars (bar.l / bar.h)"
        )
        out.append(
            "as the NBBO band proxy. Bias direction: the 1s band is wider than"
        )
        out.append(
            "the instantaneous NBBO, so off-NBBO and crossed_spread counts are"
        )
        out.append(
            "UNDER-counted (lower bound) and on_nbbo is OVER-counted. For Reg NMS"
        )
        out.append(
            "review, upgrade to Stocks Developer (entitles /v3/quotes) or use the"
        )
        out.append("flat-files quotes_v1 export for the day.")

    return "\n".join(out) + "\n"


# ----- Main -----

def main():
    if len(sys.argv) < 2:
        print("Usage: run-slippage-cost.py <fills.csv>", file=sys.stderr)
        sys.exit(1)
    csv_path = sys.argv[1]
    fills = load_fills(csv_path)
    if not fills:
        print("ERROR: no fills in input", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(fills)} fills from {csv_path}", file=sys.stderr)

    # Probe tier
    probe_ticker = fills[0]["ticker"]
    tier = probe_quote_data(probe_ticker)
    quote_source = "v3_quotes" if tier == "A" else "1s_aggs_proxy"
    print(f"Tier probe: {tier} ({quote_source})", file=sys.stderr)

    sources = []
    flagged = []
    # nbbo_buckets tallies every fill (flagged or not) that had a usable
    # reference quote into exactly one of crossed_spread / off_nbbo / on_nbbo.
    # Used for the 100%-sum invariant in the rendered summary (M1).
    nbbo_buckets = defaultdict(int)
    for i, fill in enumerate(fills, 1):
        print(f"  [{i}/{len(fills)}] {fill['ticker']} {fill['side']} {fill['qty']}@{fill['price']}", file=sys.stderr)
        try:
            result = process_fill(fill, tier, sources)
        except Exception as e:
            print(f"    ERROR: {e}", file=sys.stderr)
            continue
        if result is None:
            continue
        bucket = result.get("nbbo_bucket")
        if bucket == "off_nbbo_buy" or bucket == "off_nbbo_sell":
            nbbo_buckets["off_nbbo"] += 1
        elif bucket:
            nbbo_buckets[bucket] += 1
        if result.get("flagged"):
            flagged.append(result)
            print(f"    FLAG: {', '.join(result['reasons'])}", file=sys.stderr)

    # Build summary
    by_reason = defaultdict(int)
    crossed_bps = []
    for f in flagged:
        for r in f["reasons"]:
            by_reason[r] += 1
        if "crossed_spread" in f["reasons"] and f["slippage_bps"] is not None:
            crossed_bps.append(f["slippage_bps"])
    avg_crossed = round(sum(crossed_bps) / len(crossed_bps), 2) if crossed_bps else None
    total_shortfall = round(sum(f["implementation_shortfall_usd"] for f in flagged), 2)

    # NBBO bucket distribution as percentages of fills with a reference quote.
    # M1 invariant: these must sum to 100%.
    nbbo_total = sum(nbbo_buckets.values())
    nbbo_pct = {}
    if nbbo_total > 0:
        nbbo_pct = {
            "crossed_spread": round(nbbo_buckets.get("crossed_spread", 0) / nbbo_total * 100, 1),
            "off_nbbo": round(nbbo_buckets.get("off_nbbo", 0) / nbbo_total * 100, 1),
            "on_nbbo": round(nbbo_buckets.get("on_nbbo", 0) / nbbo_total * 100, 1),
        }
        # Snap rounding drift onto on_nbbo so the three values sum to 100.0.
        drift = round(100.0 - sum(nbbo_pct.values()), 1)
        if drift:
            nbbo_pct["on_nbbo"] = round(nbbo_pct["on_nbbo"] + drift, 1)

    # Take line
    if not flagged:
        take = f"No breaks across {len(fills)} fills · clean session"
    else:
        dominant = max(by_reason.items(), key=lambda kv: kv[1])[0]
        take = (
            f"{len(flagged)} of {len(fills)} fills flagged · "
            f"${total_shortfall:,.0f} implementation shortfall · "
            f"{dominant.replace('_', '-')} is the dominant cost driver"
        )

    # Tier caveats: per-tier NBBO source and bias direction (M2).
    # Tier A pulls real NBBO ticks per trade timestamp from /v3/quotes
    # (timestamp.lte={ns}&order=desc&limit=1 semantics, via a small
    # straddle window to handle thin-quote names). No NBBO proxy bias.
    # Tier B uses 1-second aggregate bars (bar.l / bar.h) as the NBBO
    # band proxy. The band is wider than the instantaneous NBBO because
    # it covers a whole second of quote churn, so trades that printed
    # outside the instantaneous NBBO can fall inside the bar's [low, high]
    # range. Net effect: Tier B UNDER-COUNTS off-NBBO and over-counts
    # on-NBBO.
    if tier == "A":
        tier_caveats = (
            "NBBO sourced from /v3/quotes per trade timestamp "
            "(timestamp.lte={trade_ts_ns}&order=desc); no NBBO proxy bias."
        )
    else:
        tier_caveats = (
            "NBBO inferred from 1-second aggregate bars (bar.l / bar.h) "
            "because /v3/quotes returned 403 on this key. The 1s band is "
            "wider than the instantaneous NBBO, so trades that printed "
            "outside the instantaneous NBBO can fall inside the bar range. "
            "Bias direction: under-counts off-NBBO and crossed_spread, "
            "over-counts on-NBBO (off-NBBO percentage is a lower bound)."
        )

    timestamps = [f["timestamp"] for f in fills]
    payload = {
        "tier": tier,
        "mode": "exception-report",
        "scan_params": {
            "file_in": csv_path,
            "date_range": {
                "from": min(timestamps).isoformat(),
                "to": max(timestamps).isoformat(),
            },
        },
        "fills_checked": len(fills),
        "flagged_count": len(flagged),
        "flagged": flagged,
        "summary": {
            "by_reason": dict(by_reason),
            "avg_crossed_spread_bps": avg_crossed,
            "total_implementation_shortfall_usd": total_shortfall,
            "break_rate_pct": round(len(flagged) / len(fills) * 100, 1),
            "nbbo_bucket_counts": dict(nbbo_buckets),
            "nbbo_bucket_pct": nbbo_pct,
            "nbbo_bucket_total": nbbo_total,
        },
        "quote_source": quote_source,
        "tier_caveats": tier_caveats,
        "take": take,
        "sources": dedupe_sources(sources),
    }

    rendered = render(payload)

    with open(OUTPUT_PATH, "w") as f:
        f.write(rendered)
        f.write("\n---\n\n")
        f.write("## Layer 1: canonical JSON\n\n")
        f.write("```json\n")
        f.write(json.dumps(payload, indent=2, default=str))
        f.write("\n```\n")

    print(f"\nOutput written to {OUTPUT_PATH}", file=sys.stderr)
    emit_to_stdout(rendered, payload, resolve_output_format())


def dedupe_sources(sources):
    seen = set()
    out = []
    for s in sources:
        key = (s["endpoint"].split("&apiKey=")[0], s.get("ticker"))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


if __name__ == "__main__":
    main()
