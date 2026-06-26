#!/usr/bin/env python3
"""
Reference implementation of the best-ex-check skill.

Takes a CSV of executed fills and runs TCA (transaction cost analysis)
against the NBBO at the fill timestamp. Flags fills that crossed the
spread, traded off-NBBO, paid through a wide spread, slipped versus
session VWAP, or experienced adverse selection in the 30 seconds after
the fill.

Two tiers:
  A: /v3/quotes/{ticker} returns 200 -> use microsecond NBBO ticks
  B: /v3/quotes/{ticker} returns 403 -> fall back to 1-second
                                        aggregate bars as NBBO proxy

Usage:
    python3 examples/run-best-ex-check.py examples/sample-fills.csv

Reads MASSIVE_API_KEY from env, never from a file.
Writes JSON and rendered exception report to examples/best-ex-check-output.md
"""
import csv
import json
import os
import sys
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# Make `lib.quant_garage` importable when running this script from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import MassiveClient, FetchError, ET, utc_to_et, utcnow_iso

# ----- Config -----

# Flag thresholds (see references/flag-categories.md)
WIDE_SPREAD_BPS = 50.0
HIGH_VWAP_SLIPPAGE_BPS = 25.0
ADVERSE_SELECTION_BPS = 5.0

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "best-ex-check-output.md")

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
    Tier A: pull NBBO ticks straddling ts_dt. Return the most recent
    quote at or before ts_dt as (bid, ask, quote_ts_iso, endpoint).
    """
    ts_iso = ts_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    # Window: 10s before, 2s after. Thin names may not quote every second;
    # 10s lookback finds the most recent inside quote that was active at fill time.
    ts_minus = (ts_dt - timedelta(seconds=10)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    ts_plus = (ts_dt + timedelta(seconds=2)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    path = (
        f"/v3/quotes/{ticker}"
        f"?timestamp.gte={urllib.parse.quote(ts_minus)}"
        f"&timestamp.lte={urllib.parse.quote(ts_plus)}"
        f"&order=desc&sort=timestamp&limit=50"
    )
    doc, status = fetch(path)
    if status != 200:
        return None, None, None, path
    fill_ns = int(ts_dt.timestamp() * 1_000_000_000)
    chosen = None
    for q in doc.get("results", []) or []:
        sip_ts = q.get("sip_timestamp") or q.get("participant_timestamp")
        if sip_ts is None:
            continue
        if sip_ts <= fill_ns:
            chosen = q
            break
    if chosen is None:
        # No quote at or before fill time; take the closest one
        results = doc.get("results", []) or []
        if not results:
            return None, None, None, path
        chosen = results[-1]
    bid = chosen.get("bid_price")
    ask = chosen.get("ask_price")
    sip_ns = chosen.get("sip_timestamp") or chosen.get("participant_timestamp")
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

    # Apply flag categories
    reasons = []
    if slip is not None and slip > 0:
        reasons.append("crossed_spread")
    if spread is not None and spread > WIDE_SPREAD_BPS:
        reasons.append("wide_spread_at_fill")
    # Off-NBBO: fill outside the inside by a meaningful amount.
    # Crossed-spread already captures "paid through the spread";
    # off_nbbo is the stronger statement that the print landed
    # outside the NBBO band by enough that it's an investigation,
    # not just a routing miss. Threshold: 20bps beyond inside.
    if side == "BUY" and ask is not None and fill_price > ask:
        if slip is not None and slip > 20:
            reasons.append("off_nbbo_buy")
    if side == "SELL" and bid is not None and fill_price < bid:
        if slip is not None and slip > 20:
            reasons.append("off_nbbo_sell")
    if vslip is not None and vslip > HIGH_VWAP_SLIPPAGE_BPS:
        reasons.append("high_vwap_slippage")
    if adverse is not None and adverse > ADVERSE_SELECTION_BPS:
        reasons.append("adverse_selection")

    if not reasons:
        return None

    impl_shortfall = 0.0
    if slip is not None:
        impl_shortfall = abs(slip) / 10000.0 * fill_price * qty

    suggest = suggest_action(reasons, slip, vslip, adverse, spread)

    return {
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
        return "No clear best-ex violation; trader took available liquidity in thin tape"
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
            "aggregate band as NBBO proxy. Off-NBBO calls are a lower bound on this tier."
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

    if payload["tier"] == "B":
        out.append("")
        out.append(
            "Methodology note: Tier B uses 1-second aggregate bars as the NBBO proxy."
        )
        out.append(
            "Off-NBBO counts are a lower bound. For a Reg NMS compliance review,"
        )
        out.append(
            "upgrade to Stocks Developer (entitles /v3/quotes) or use flat-files"
        )
        out.append("quotes_v1 for the day.")

    return "\n".join(out) + "\n"


# ----- Main -----

def main():
    if len(sys.argv) < 2:
        print("Usage: run-best-ex-check.py <fills.csv>", file=sys.stderr)
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
    for i, fill in enumerate(fills, 1):
        print(f"  [{i}/{len(fills)}] {fill['ticker']} {fill['side']} {fill['qty']}@{fill['price']}", file=sys.stderr)
        try:
            result = process_fill(fill, tier, sources)
        except Exception as e:
            print(f"    ERROR: {e}", file=sys.stderr)
            continue
        if result:
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
        },
        "quote_source": quote_source,
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
    print(rendered)


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
