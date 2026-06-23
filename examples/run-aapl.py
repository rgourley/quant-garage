#!/usr/bin/env python3
"""
Run the earnings-drilldown skill against AAPL using a live Massive key.
Reads MASSIVE_API_KEY from env, never from a file.
Writes output to examples/aapl-real-output.md (gitignored).
"""
import os
import sys
import json
import math
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict

KEY = os.environ.get("MASSIVE_API_KEY")
if not KEY:
    print("ERROR: MASSIVE_API_KEY not set", file=sys.stderr)
    sys.exit(1)

BASE = "https://api.polygon.io"
HEADERS = {"Authorization": f"Bearer {KEY}"}
TICKER = "AAPL"
TODAY = date(2026, 6, 23)


def fetch(path):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode("utf-8", errors="replace")
        raise RuntimeError(f"{e.code} on {path}: {body}")


def fetch_all(path):
    """Follow next_url to collect paginated results."""
    out = []
    url = f"{BASE}{path}"
    while url:
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


def median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def t_stat(xs):
    n = len(xs)
    if n < 2:
        return None
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return None
    se = math.sqrt(var / n)
    return mean / se if se > 0 else None


# 1. Ticker metadata
print("Fetching ticker metadata...", file=sys.stderr)
ticker_meta = fetch(f"/v3/reference/tickers/{TICKER}")["results"]
sic_code = ticker_meta.get("sic_code")
sic_desc = ticker_meta.get("sic_description")

# 2. Benzinga earnings (primary source for print dates + consensus + actuals)
print("Fetching Benzinga earnings...", file=sys.stderr)
bz = fetch(f"/benzinga/v1/earnings?ticker={TICKER}&limit=20&order=desc&sort=date")["results"]

# Split into upcoming (no actual) and historical (has actual)
upcoming = [r for r in bz if r.get("actual_eps") is None]
historical = [r for r in bz if r.get("actual_eps") is not None]

# Take the most recent 8 historical prints
historical.sort(key=lambda r: r["date"])
historical = historical[-8:]

prints = []
for r in historical:
    # Build ISO timestamp from date + time + ET assumption
    # Benzinga time is typically America/New_York. Treat as ET → UTC.
    time_str = r.get("time") or "16:30:00"
    dt_et = datetime.fromisoformat(f"{r['date']}T{time_str}")
    # Crude ET → UTC: EDT in summer (Mar-Nov) is +4, EST otherwise +5.
    # AAPL prints in Jan/Feb (EST), Apr/May (EDT), Jul/Aug (EDT), Oct/Nov (EDT/EST).
    # The 1-hr error around DST shifts the BMO/AMC bucket cleanly either way.
    month = dt_et.month
    is_dst = 3 <= month <= 10 or (month == 11 and dt_et.day < 6) or (month == 3 and dt_et.day >= 13)
    utc_offset_hours = 4 if is_dst else 5
    filing_dt = dt_et + timedelta(hours=utc_offset_hours)
    prints.append({
        "fiscal_period": f"{r['fiscal_period']} {r['fiscal_year']}",
        "filing_dt": filing_dt.isoformat() + "Z",
        "filing_date": r["date"],
        "eps_actual": r.get("actual_eps"),
        "eps_estimate": r.get("estimated_eps"),
        "eps_surprise_pct": r.get("eps_surprise_percent"),
        "revenue_actual": r.get("actual_revenue"),
        "revenue_estimate": r.get("estimated_revenue"),
        "revenue_surprise_pct": r.get("revenue_surprise_percent"),
    })

# Next print (the upcoming one)
upcoming.sort(key=lambda r: r["date"])
next_print = upcoming[0] if upcoming else None

# 3. Daily aggregates (3 years to cover 8 prints + buffer)
end_date = TODAY.isoformat()
start_date = (TODAY - timedelta(days=365 * 3)).isoformat()
print(f"Fetching daily aggregates {start_date} to {end_date}...", file=sys.stderr)
aggs = fetch_all(
    f"/v2/aggs/ticker/{TICKER}/range/1/day/{start_date}/{end_date}?adjusted=true&sort=asc&limit=50000"
)
agg_by_date = {}
for a in aggs:
    d = datetime.fromtimestamp(a["t"] / 1000, tz=timezone.utc).date()
    agg_by_date[d.isoformat()] = a

trading_dates = sorted(agg_by_date.keys())


def next_trading_day(d_str, offset=1):
    """Return the date string for the trading day `offset` days after `d_str`."""
    try:
        idx = trading_dates.index(d_str)
    except ValueError:
        idx = next((i for i, td in enumerate(trading_dates) if td > d_str), None)
        if idx is None:
            return None
        idx -= 1
    target_idx = idx + offset
    if 0 <= target_idx < len(trading_dates):
        return trading_dates[target_idx]
    return None


# 4. SPY aggregates for PEAD beta-adjustment
print("Fetching SPY aggregates for PEAD adjustment...", file=sys.stderr)
spy_aggs = fetch_all(
    f"/v2/aggs/ticker/SPY/range/1/day/{start_date}/{end_date}?adjusted=true&sort=asc&limit=50000"
)
spy_by_date = {datetime.fromtimestamp(a["t"] / 1000, tz=timezone.utc).date().isoformat(): a for a in spy_aggs}


# 5. Print history with reactions
def date_after_filing(filing_dt_iso):
    """
    Determine the reaction window for an earnings print.
    AAPL reports AMC (~16:30 ET press release).
      - AMC (hour_et >= 16): ref_date = print date, reaction_date = next trading day
      - BMO (hour_et < 9):   ref_date = prior trading day, reaction_date = print date
      - Intraday:            same-day reaction window
    """
    dt = datetime.fromisoformat(filing_dt_iso.replace("Z", "+00:00"))
    filing_d_str = dt.date().isoformat()
    hour_et = (dt.hour - 4) % 24  # crude; we built it from ET above

    if hour_et < 9:
        reaction_d = filing_d_str
        ref_d = next_trading_day(reaction_d, -1)
        session = "BMO"
    elif hour_et >= 16:
        ref_d = filing_d_str
        reaction_d = next_trading_day(ref_d, 1)
        session = "AMC"
    else:
        reaction_d = filing_d_str
        ref_d = next_trading_day(reaction_d, -1)
        session = "Intraday"

    if ref_d and ref_d not in agg_by_date:
        ref_d = next((td for td in reversed(trading_dates) if td < ref_d), None)

    return ref_d, reaction_d, session


prints_with_reaction = []
for p in prints:
    ref_d, next_d, session = date_after_filing(p["filing_dt"])
    if not (ref_d and next_d):
        continue
    if ref_d not in agg_by_date or next_d not in agg_by_date:
        continue
    ref_close = agg_by_date[ref_d]["c"]
    next_close = agg_by_date[next_d]["c"]
    reaction = (next_close - ref_close) / ref_close
    t5 = next_trading_day(next_d, 4)
    t5_return = None
    spy_t5_return = None
    if t5 and t5 in agg_by_date:
        t5_return = (agg_by_date[t5]["c"] - ref_close) / ref_close
        if ref_d in spy_by_date and t5 in spy_by_date:
            spy_t5_return = (spy_by_date[t5]["c"] - spy_by_date[ref_d]["c"]) / spy_by_date[ref_d]["c"]
    prints_with_reaction.append({
        **p,
        "ref_date": ref_d,
        "reaction_date": next_d,
        "session": session,
        "reaction_pct": reaction,
        "t5_return_pct": t5_return,
        "spy_t5_return_pct": spy_t5_return,
        "abnormal_t5_pct": (t5_return - spy_t5_return) if t5_return is not None and spy_t5_return is not None else None,
    })

realized_moves = [abs(p["reaction_pct"]) for p in prints_with_reaction if p["reaction_pct"] is not None]
n_q = len(prints_with_reaction)
realized_avg = sum(realized_moves) / len(realized_moves) if realized_moves else None
realized_med = median(realized_moves)

# True beat/miss using Benzinga consensus
beats = [p for p in prints_with_reaction if p["eps_surprise_pct"] is not None and p["eps_surprise_pct"] > 0]
misses = [p for p in prints_with_reaction if p["eps_surprise_pct"] is not None and p["eps_surprise_pct"] <= 0]

best_reaction = max(prints_with_reaction, key=lambda p: p["reaction_pct"] or -999, default=None)
worst_reaction = min(prints_with_reaction, key=lambda p: p["reaction_pct"] or 999, default=None)
largest_beat = max(prints_with_reaction, key=lambda p: p["eps_surprise_pct"] or -999, default=None)
largest_miss = min(prints_with_reaction, key=lambda p: p["eps_surprise_pct"] or 999, default=None)

avg_surprise_eps = (
    sum(p["eps_surprise_pct"] for p in prints_with_reaction if p["eps_surprise_pct"] is not None)
    / sum(1 for p in prints_with_reaction if p["eps_surprise_pct"] is not None)
) if any(p["eps_surprise_pct"] is not None for p in prints_with_reaction) else None
rev_surprise_vals = [p["revenue_surprise_pct"] for p in prints_with_reaction if p["revenue_surprise_pct"] is not None]
avg_surprise_rev = sum(rev_surprise_vals) / len(rev_surprise_vals) if rev_surprise_vals else None

# PEAD using true beat/miss buckets
abnormal_beats = [p["abnormal_t5_pct"] for p in beats if p["abnormal_t5_pct"] is not None]
abnormal_misses = [p["abnormal_t5_pct"] for p in misses if p["abnormal_t5_pct"] is not None]

pead = {
    "on_beats": {
        "n": len(abnormal_beats),
        "avg_t5_return_pct": sum(abnormal_beats) / len(abnormal_beats) if abnormal_beats else None,
        "t_stat": t_stat(abnormal_beats),
        "significant": False,
    } if abnormal_beats else None,
    "on_misses": {
        "n": len(abnormal_misses),
        "avg_t5_return_pct": sum(abnormal_misses) / len(abnormal_misses) if abnormal_misses else None,
        "t_stat": t_stat(abnormal_misses),
        "significant": False,
    } if abnormal_misses else None,
}
for bucket_key in ("on_beats", "on_misses"):
    bucket = pead[bucket_key]
    if bucket and bucket["t_stat"] is not None:
        bucket["significant"] = abs(bucket["t_stat"]) > 2.0

# 6. Options snapshot for implied move + IV30
print("Fetching options snapshot...", file=sys.stderr)
spot_snap = fetch(f"/v2/snapshot/locale/us/markets/stocks/tickers/{TICKER}")["ticker"]
spot = spot_snap["lastQuote"]["p"] if spot_snap.get("lastQuote") else spot_snap["day"]["c"]

# Use Benzinga's next print date to find the earnings-capturing expiry
next_print_date_str = next_print["date"] if next_print else (TODAY + timedelta(days=37)).isoformat()
next_earnings_date = date.fromisoformat(next_print_date_str)
opt_to_date = (next_earnings_date + timedelta(days=14)).isoformat()
opt_from_date = TODAY.isoformat()
strike_band_lo = int(spot * 0.95)
strike_band_hi = int(spot * 1.05)
opts = fetch_all(
    f"/v3/snapshot/options/{TICKER}?expiration_date.gte={opt_from_date}"
    f"&expiration_date.lte={opt_to_date}&strike_price.gte={strike_band_lo}"
    f"&strike_price.lte={strike_band_hi}&limit=250"
)

opts_by_exp = defaultdict(list)
for o in opts:
    exp = o.get("details", {}).get("expiration_date")
    if exp:
        opts_by_exp[exp].append(o)

expiries = sorted(opts_by_exp.keys())
earnings_capturing = [e for e in expiries if e >= next_print_date_str]
chosen_expiry = earnings_capturing[0] if earnings_capturing else (expiries[0] if expiries else None)


def atm_straddle(chain, spot_price):
    calls = [o for o in chain if o["details"]["contract_type"] == "call"]
    puts = [o for o in chain if o["details"]["contract_type"] == "put"]
    if not calls or not puts:
        return None
    call = min(calls, key=lambda o: abs(o["details"]["strike_price"] - spot_price))
    put_at_strike = next((o for o in puts if o["details"]["strike_price"] == call["details"]["strike_price"]), None)
    if not put_at_strike:
        put_at_strike = min(puts, key=lambda o: abs(o["details"]["strike_price"] - spot_price))

    def mid(o):
        q = o.get("last_quote", {})
        bid = q.get("bid")
        ask = q.get("ask")
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            return (bid + ask) / 2
        return o.get("day", {}).get("close") or o.get("last_trade", {}).get("price")

    call_mid = mid(call)
    put_mid = mid(put_at_strike)
    iv_call = call.get("implied_volatility")
    iv_put = put_at_strike.get("implied_volatility")
    return {
        "call_mid": call_mid,
        "put_mid": put_mid,
        "strike": call["details"]["strike_price"],
        "expiration": call["details"]["expiration_date"],
        "iv_call": iv_call,
        "iv_put": iv_put,
    }


straddle_info = atm_straddle(opts_by_exp.get(chosen_expiry, []), spot) if chosen_expiry else None

if straddle_info and straddle_info["iv_call"] and straddle_info["iv_put"]:
    iv30_proxy = (straddle_info["iv_call"] + straddle_info["iv_put"]) / 2 * 100
else:
    iv30_proxy = None

straddle_pct = None
implied_move_pct = None
mispricing_pct = None
if straddle_info and straddle_info["call_mid"] and straddle_info["put_mid"] and spot:
    straddle_pct = (straddle_info["call_mid"] + straddle_info["put_mid"]) / spot
    implied_move_pct = straddle_pct * 0.85
    if realized_avg:
        mispricing_pct = (implied_move_pct - realized_avg) / realized_avg

# 7. Peer reaction note (methodology gap)
peer_reaction_note = (
    f"SKIPPED: AAPL SIC code is {sic_code} ({sic_desc}). SIC-based peer selection "
    f"would return computer hardware peers (IBM/HPE/DELL), not the semis/mega-cap-tech "
    f"basket traders actually compare AAPL against (NVDA/TSM/AVGO/MSFT/GOOGL). "
    f"Methodology fix needed in peer-reaction.md."
)

# 8. Build the JSON payload
payload = {
    "ticker": TICKER,
    "mode": "full",
    "run_at": datetime.now(timezone.utc).isoformat(),
    "print": {
        "date": next_print["date"] if next_print else None,
        "session": "AMC",
        "consensus_eps": next_print.get("estimated_eps") if next_print else None,
        "consensus_revenue": next_print.get("estimated_revenue") if next_print else None,
        "fiscal_period": (
            f"{next_print['fiscal_period']} {next_print['fiscal_year']}"
            if next_print else None
        ),
        "source": "benzinga/v1/earnings",
    },
    "spot": spot,
    "implied_vs_realized": {
        "straddle_pct": straddle_pct,
        "implied_move_pct": implied_move_pct,
        "realized_avg_pct": realized_avg,
        "realized_median_pct": realized_med,
        "n_quarters": n_q,
        "iv30_proxy": iv30_proxy,
        "iv30_source": "Average IV of ATM call+put on earnings-capturing expiry (no dedicated IV30 endpoint discovered yet)",
        "mispricing_pct": mispricing_pct,
        "front_expiry_used": chosen_expiry,
        "atm_strike_used": straddle_info["strike"] if straddle_info else None,
    } if straddle_pct is not None else None,
    "print_history": {
        "n_quarters": n_q,
        "n_beats": len(beats),
        "n_misses": len(misses),
        "avg_surprise_eps_pct": avg_surprise_eps,
        "avg_surprise_revenue_pct": avg_surprise_rev,
        "largest_beat": {
            "period": largest_beat["fiscal_period"] if largest_beat else None,
            "surprise_pct": largest_beat["eps_surprise_pct"] if largest_beat else None,
        } if largest_beat else None,
        "largest_miss": {
            "period": largest_miss["fiscal_period"] if largest_miss else None,
            "surprise_pct": largest_miss["eps_surprise_pct"] if largest_miss else None,
        } if largest_miss else None,
        "best_reaction": {
            "period": best_reaction["fiscal_period"] if best_reaction else None,
            "next_day_return_pct": best_reaction["reaction_pct"] if best_reaction else None,
        } if best_reaction else None,
        "worst_reaction": {
            "period": worst_reaction["fiscal_period"] if worst_reaction else None,
            "next_day_return_pct": worst_reaction["reaction_pct"] if worst_reaction else None,
        } if worst_reaction else None,
        "all_prints": [
            {
                "period": p["fiscal_period"],
                "print_date": p["filing_date"],
                "session": p["session"],
                "eps_actual": p["eps_actual"],
                "eps_estimate": p["eps_estimate"],
                "eps_surprise_pct": p["eps_surprise_pct"],
                "reaction_pct": p["reaction_pct"],
                "t5_abnormal_pct": p["abnormal_t5_pct"],
            }
            for p in prints_with_reaction
        ],
    },
    "post_earnings_drift": pead,
    "peer_reaction": None,
    "peer_reaction_note": peer_reaction_note,
    "sources": [
        {"endpoint": f"/v3/reference/tickers/{TICKER}", "context": "ticker metadata, sic_code"},
        {"endpoint": f"/benzinga/v1/earnings?ticker={TICKER}", "context": "earnings dates, consensus EPS/revenue, actuals, surprise"},
        {"endpoint": f"/v2/aggs/ticker/{TICKER}/range/1/day/...", "context": "daily closes for realized moves and PEAD"},
        {"endpoint": "/v2/aggs/ticker/SPY/range/1/day/...", "context": "SPY for PEAD beta-adjustment"},
        {"endpoint": f"/v2/snapshot/locale/us/markets/stocks/tickers/{TICKER}", "context": "current spot"},
        {"endpoint": f"/v3/snapshot/options/{TICKER}?...", "context": "ATM straddle and IV"},
    ],
}


# 9. Render the note
def fmt_pct(x, decimals=1):
    if x is None:
        return "n/a"
    return f"{x * 100:.{decimals}f}%"


def fmt_signed_pct(x, decimals=1):
    if x is None:
        return "n/a"
    sign = "+" if x >= 0 else "−"
    return f"{sign}{abs(x) * 100:.{decimals}f}%"


lines = []
fiscal_label = payload["print"].get("fiscal_period") or "Next"
lines.append(f"{TICKER}: {fiscal_label} Preview (run {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)")
header_extras = []
if payload["print"].get("date"):
    header_extras.append(f"Print: {payload['print']['date']} AMC")
if payload["print"].get("consensus_eps") is not None:
    rev_b = payload["print"].get("consensus_revenue")
    rev_label = f", ${rev_b / 1e9:.1f}B rev" if rev_b else ""
    header_extras.append(f"Consensus: ${payload['print']['consensus_eps']:.2f} EPS{rev_label}")
header_extras.append(f"Spot: ${spot:.2f}")
lines.append(" · ".join(header_extras))
lines.append("")

# Take generation
take_parts = []
if payload["implied_vs_realized"]:
    iv_r = payload["implied_vs_realized"]
    if iv_r["mispricing_pct"] is not None:
        if iv_r["mispricing_pct"] > 0.15:
            take_parts.append(
                f"Straddle is {iv_r['mispricing_pct'] * 100:.0f}% rich vs {iv_r['n_quarters']}q realized "
                f"(implied ±{iv_r['implied_move_pct'] * 100:.1f}%, realized ±{iv_r['realized_avg_pct'] * 100:.1f}%). "
                f"Premium sellers have a setup."
            )
        elif iv_r["mispricing_pct"] < -0.15:
            take_parts.append(
                f"Straddle is {abs(iv_r['mispricing_pct']) * 100:.0f}% cheap vs {iv_r['n_quarters']}q realized "
                f"(implied ±{iv_r['implied_move_pct'] * 100:.1f}%, realized ±{iv_r['realized_avg_pct'] * 100:.1f}%). "
                f"Premium buyers have a setup."
            )
        else:
            take_parts.append(
                f"Straddle is fair vs realized "
                f"(implied ±{iv_r['implied_move_pct'] * 100:.1f}%, realized ±{iv_r['realized_avg_pct'] * 100:.1f}%)."
            )
if not take_parts:
    take_parts.append("Setup mixed: insufficient data for a strong take.")
lines.append(f"**Take:** {' '.join(take_parts)}")
lines.append("")

if payload["implied_vs_realized"]:
    iv_r = payload["implied_vs_realized"]
    lines.append("Implied vs realized")
    lines.append(
        f"- Implied move (front straddle, 0.85-adj): ±{iv_r['implied_move_pct'] * 100:.1f}% "
        f"(raw straddle ±{iv_r['straddle_pct'] * 100:.1f}%)"
    )
    lines.append(f"- Realized {iv_r['n_quarters']}q avg: ±{iv_r['realized_avg_pct'] * 100:.1f}%")
    if iv_r["iv30_proxy"]:
        lines.append(f"- IV30 (proxy from ATM avg): {iv_r['iv30_proxy']:.1f}")
    lines.append(f"- Expiry used: {iv_r['front_expiry_used']} · ATM strike: ${iv_r['atm_strike_used']}")
    lines.append("")

lines.append(f"Print history (last {n_q} quarters)")
ph = payload["print_history"]
beat_rate_line = f"- Beat rate: {ph['n_beats']}/{ph['n_quarters']}"
if ph["avg_surprise_eps_pct"] is not None:
    beat_rate_line += f" (avg surprise {ph['avg_surprise_eps_pct'] * 100:+.1f}% EPS"
    if ph["avg_surprise_revenue_pct"] is not None:
        beat_rate_line += f", {ph['avg_surprise_revenue_pct'] * 100:+.1f}% rev"
    beat_rate_line += ")"
lines.append(beat_rate_line)
if ph["largest_beat"] and ph["largest_beat"]["surprise_pct"] is not None:
    lines.append(f"- Largest beat: {ph['largest_beat']['period']} ({fmt_signed_pct(ph['largest_beat']['surprise_pct'])})")
if ph["largest_miss"] and ph["largest_miss"]["surprise_pct"] is not None and ph["largest_miss"]["surprise_pct"] < ph["largest_beat"]["surprise_pct"]:
    lines.append(f"- Smallest beat / largest miss: {ph['largest_miss']['period']} ({fmt_signed_pct(ph['largest_miss']['surprise_pct'])})")
if best_reaction:
    lines.append(f"- Best reaction: {best_reaction['fiscal_period']} {fmt_signed_pct(best_reaction['reaction_pct'])} next day")
if worst_reaction:
    lines.append(f"- Worst reaction: {worst_reaction['fiscal_period']} {fmt_signed_pct(worst_reaction['reaction_pct'])} next day")
lines.append("")

if pead.get("on_beats") or pead.get("on_misses"):
    lines.append("Post-earnings drift (T+1 to T+5, SPY-adjusted)")
    if pead.get("on_beats") and pead["on_beats"]["avg_t5_return_pct"] is not None:
        b = pead["on_beats"]
        if b["n"] < 4:
            sig = "sample too small"
        elif b["significant"]:
            sig = "significant"
        elif b["t_stat"] is not None:
            sig = f"t-stat {b['t_stat']:.2f}, not significant"
        else:
            sig = "sample too small"
        lines.append(f"- On beats: {fmt_signed_pct(b['avg_t5_return_pct'])} avg (n={b['n']}, {sig})")
    if pead.get("on_misses") and pead["on_misses"]["avg_t5_return_pct"] is not None:
        m = pead["on_misses"]
        if m["n"] < 4:
            sig = "sample too small"
        elif m["significant"]:
            sig = "significant"
        elif m["t_stat"] is not None:
            sig = f"t-stat {m['t_stat']:.2f}, not significant"
        else:
            sig = "sample too small"
        lines.append(f"- On misses: {fmt_signed_pct(m['avg_t5_return_pct'])} avg (n={m['n']}, {sig})")
    lines.append("")

lines.append("Per-print detail (for inspection)")
lines.append("| Period | Print date | Session | EPS actual | EPS est | Surprise | Day-1 reaction | T+5 abnormal |")
lines.append("|---|---|---|---|---|---|---|---|")
for p in prints_with_reaction:
    eps_a = f"${p['eps_actual']:.2f}" if p["eps_actual"] is not None else "n/a"
    eps_e = f"${p['eps_estimate']:.2f}" if p["eps_estimate"] is not None else "n/a"
    surp = fmt_signed_pct(p["eps_surprise_pct"]) if p["eps_surprise_pct"] is not None else "n/a"
    react = fmt_signed_pct(p["reaction_pct"])
    t5 = fmt_signed_pct(p["abnormal_t5_pct"]) if p["abnormal_t5_pct"] is not None else "n/a"
    lines.append(f"| {p['fiscal_period']} | {p['filing_date']} | {p['session']} | {eps_a} | {eps_e} | {surp} | {react} | {t5} |")

rendered = "\n".join(lines)

# 10. Write output
out_path = os.path.join(os.path.dirname(__file__), "aapl-real-output.md")
with open(out_path, "w") as f:
    f.write("# Real run: earnings-drilldown AAPL\n\n")
    f.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
    f.write(f"Spot at run: ${spot:.2f}\n\n")
    f.write("## Layer 1: canonical JSON (live data)\n\n")
    f.write("```json\n")
    f.write(json.dumps(payload, indent=2, default=str))
    f.write("\n```\n\n")
    f.write("## Layer 2: rendered note (live data)\n\n")
    f.write("```\n")
    f.write(rendered)
    f.write("\n```\n\n")
    f.write("## Gaps surfaced by this run\n\n")
    f.write("- Benzinga `/benzinga/v1/earnings` is the right source for print dates + consensus + actuals. Replaced vX/financials usage. SKILL.md and references should be updated to call it out as the canonical earnings endpoint.\n")
    f.write(f"- Peer reaction skipped: {peer_reaction_note}\n")
    f.write("- No dedicated IV30 endpoint discovered; using ATM call+put average IV as proxy.\n")
    f.write("- DST handling for ET → UTC is heuristic; a proper zoneinfo conversion would be cleaner. The 1-hr error doesn't change AMC/BMO bucketing for AAPL.\n")

print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
print(rendered)
