#!/usr/bin/env python3
"""
Tier B run of the earnings-drilldown skill against AAPL.

Tier B = Stocks Starter + SEC EDGAR (no Benzinga add-on).
- Print dates come from SEC EDGAR 8-K filings filtered to item 2.02.
- EPS actuals come from Massive /vX/reference/financials.
- No consensus EPS → no beat rate, no surprise size.
- PEAD bucketed by reaction sign (next-day return sign) instead of surprise sign.

Reads MASSIVE_API_KEY from env, never from a file.
Writes output to examples/aapl-tier-b-output.md (gitignored).
"""
import os
import sys
import json
import math
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict

KEY = os.environ.get("MASSIVE_API_KEY")
if not KEY:
    print("ERROR: MASSIVE_API_KEY not set", file=sys.stderr)
    sys.exit(1)

BASE = "https://api.polygon.io"
HEADERS = {"Authorization": f"Bearer {KEY}"}
SEC_HEADERS = {"User-Agent": "Rob Gourley rgourley@gmail.com"}
TICKER = "AAPL"
TODAY = date(2026, 6, 23)

# Curated peer override per references/peer-reaction.md
PEER_OVERRIDES = {
    "AAPL": ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSM", "AVGO"],
}


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


def fetch_sec(url):
    req = urllib.request.Request(url, headers=SEC_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


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


# 1. Ticker metadata (need CIK + sic for EDGAR + peer fallback)
print("Fetching ticker metadata...", file=sys.stderr)
ticker_meta = fetch(f"/v3/reference/tickers/{TICKER}")["results"]
cik_raw = ticker_meta.get("cik")
if not cik_raw:
    print("ERROR: no CIK in ticker metadata", file=sys.stderr)
    sys.exit(1)
cik_padded = str(cik_raw).zfill(10)
sic_code = ticker_meta.get("sic_code")
sic_desc = ticker_meta.get("sic_description")

# 2. SEC EDGAR submissions: pull recent 8-K filings filtered to item 2.02
print(f"Fetching SEC EDGAR submissions for CIK {cik_padded}...", file=sys.stderr)
sec_doc = fetch_sec(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
recent = sec_doc["filings"]["recent"]


def is_earnings_8k(form, items):
    if form != "8-K":
        return False
    items_list = [i.strip() for i in (items or "").split(",")]
    return "2.02" in items_list


n_recent = len(recent["form"])
earnings_8ks = []
for i in range(n_recent):
    if is_earnings_8k(recent["form"][i], recent.get("items", [""] * n_recent)[i]):
        earnings_8ks.append({
            "filing_date": recent["filingDate"][i],
            "acceptance_dt": recent["acceptanceDateTime"][i],  # UTC ISO
            "accession": recent["accessionNumber"][i],
            "report_date": recent.get("reportDate", [""] * n_recent)[i],
        })

# Sort ascending and keep last 8
earnings_8ks.sort(key=lambda x: x["acceptance_dt"])
earnings_8ks = earnings_8ks[-8:]

# 3. EPS actuals from Massive /vX/reference/financials
# Pull both quarterly and annual: Q4 is reported as the 10-K annual filing
# (no quarterly Q4 record exists). For annual records we back out Q4 EPS as
# annual_eps - sum(Q1+Q2+Q3) of the same fiscal year.
print("Fetching financials for EPS actuals...", file=sys.stderr)
fin_results = fetch_all(
    f"/vX/reference/financials?ticker={TICKER}&limit=40&order=desc&sort=filing_date"
)

# Normalize financials records. The financials `filing_date` is the 10-Q
# filing date, which is typically 1-2 calendar days AFTER the 8-K item 2.02
# release for the same quarter. Match each 8-K to the financials record whose
# filing_date is in [8K_filing_date, 8K_filing_date + 3 days].
fin_records = []
for f in fin_results:
    fd = f.get("filing_date")
    if not fd:
        continue
    fin = (f.get("financials") or {}).get("income_statement") or {}
    eps_obj = fin.get("diluted_earnings_per_share") or fin.get("basic_earnings_per_share")
    eps_val = eps_obj.get("value") if eps_obj else None
    fin_records.append({
        "filing_date": fd,
        "fiscal_period": f.get("fiscal_period"),
        "fiscal_year": f.get("fiscal_year"),
        "end_date": f.get("end_date"),
        "eps_actual": eps_val,
        "timeframe": f.get("timeframe"),
    })

# Index quarterly records by (fiscal_year, fiscal_period) for Q4 backout
quarterly_by_fy_period = {}
for fr in fin_records:
    if fr["timeframe"] == "quarterly":
        quarterly_by_fy_period[(fr["fiscal_year"], fr["fiscal_period"])] = fr


def match_financials(eight_k_filing_date):
    """Pick the financials record filed within 0-3 days after the 8-K.
    For annual records (Q4), back out Q4 EPS as FY - (Q1+Q2+Q3).
    """
    target = date.fromisoformat(eight_k_filing_date)
    best = None
    best_gap = None
    for fr in fin_records:
        fr_d = date.fromisoformat(fr["filing_date"])
        gap = (fr_d - target).days
        if 0 <= gap <= 3 and (best_gap is None or gap < best_gap):
            best = fr
            best_gap = gap
    if not best:
        return None
    if best["timeframe"] == "annual":
        # Back out Q4 EPS
        fy = best["fiscal_year"]
        try:
            fy_int = int(str(fy))
        except (TypeError, ValueError):
            fy_int = fy
        q1 = quarterly_by_fy_period.get((fy_int, "Q1")) or quarterly_by_fy_period.get((fy, "Q1"))
        q2 = quarterly_by_fy_period.get((fy_int, "Q2")) or quarterly_by_fy_period.get((fy, "Q2"))
        q3 = quarterly_by_fy_period.get((fy_int, "Q3")) or quarterly_by_fy_period.get((fy, "Q3"))
        q4_eps = None
        if (best["eps_actual"] is not None and q1 and q2 and q3
                and all(q["eps_actual"] is not None for q in (q1, q2, q3))):
            q4_eps = best["eps_actual"] - (q1["eps_actual"] + q2["eps_actual"] + q3["eps_actual"])
        return {
            **best,
            "fiscal_period": "Q4",
            "eps_actual": q4_eps,
            "eps_method": "Q4 = FY - (Q1+Q2+Q3)" if q4_eps is not None else None,
        }
    return best


# 4. Build prints from EDGAR 8-Ks, attach EPS from financials
prints = []
for f8k in earnings_8ks:
    fd = f8k["filing_date"]
    acc_dt_str = f8k["acceptance_dt"]
    fin_match = match_financials(fd)
    fiscal_period = (
        f"{fin_match['fiscal_period']} {fin_match['fiscal_year']}"
        if fin_match else f"Print {fd}"
    )
    prints.append({
        "fiscal_period": fiscal_period,
        "filing_dt": acc_dt_str if acc_dt_str.endswith("Z") else acc_dt_str + "Z",
        "filing_date": fd,
        "eps_actual": fin_match["eps_actual"] if fin_match else None,
    })

# 5. Daily aggregates (3 years to cover 8 prints + buffer)
end_date = TODAY.isoformat()
start_date = (TODAY - timedelta(days=365 * 3)).isoformat()
print(f"Fetching AAPL daily aggregates {start_date} to {end_date}...", file=sys.stderr)
aggs = fetch_all(
    f"/v2/aggs/ticker/{TICKER}/range/1/day/{start_date}/{end_date}?adjusted=true&sort=asc&limit=50000"
)
agg_by_date = {}
for a in aggs:
    d = datetime.fromtimestamp(a["t"] / 1000, tz=timezone.utc).date()
    agg_by_date[d.isoformat()] = a

trading_dates = sorted(agg_by_date.keys())


def next_trading_day(d_str, offset=1):
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


# 6. SPY aggregates for PEAD beta-adjustment
print("Fetching SPY aggregates for PEAD adjustment...", file=sys.stderr)
spy_aggs = fetch_all(
    f"/v2/aggs/ticker/SPY/range/1/day/{start_date}/{end_date}?adjusted=true&sort=asc&limit=50000"
)
spy_by_date = {
    datetime.fromtimestamp(a["t"] / 1000, tz=timezone.utc).date().isoformat(): a
    for a in spy_aggs
}


# 7. Reaction window helpers
def session_window(filing_dt_iso):
    """
    Use the UTC acceptance time to classify AMC/BMO/Intraday.
    AAPL prints AMC; acceptance ~20:30 UTC = 16:30 ET in EDT, 15:30 ET in EST.
    Translate to ET hour by subtracting 4 (EDT) most of the year; the date
    boundary doesn't shift for AAPL's print pattern.
    """
    dt = datetime.fromisoformat(filing_dt_iso.replace("Z", "+00:00"))
    filing_d_str = dt.date().isoformat()
    # Approximate ET hour: subtract 4 in summer, 5 in winter
    month = dt.month
    is_dst = 3 <= month <= 10 or (month == 11 and dt.day < 6) or (month == 3 and dt.day >= 13)
    et_offset = 4 if is_dst else 5
    hour_et = (dt.hour - et_offset) % 24

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
    ref_d, next_d, session = session_window(p["filing_dt"])
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
            spy_t5_return = (
                spy_by_date[t5]["c"] - spy_by_date[ref_d]["c"]
            ) / spy_by_date[ref_d]["c"]
    prints_with_reaction.append({
        **p,
        "ref_date": ref_d,
        "reaction_date": next_d,
        "session": session,
        "reaction_pct": reaction,
        "t5_return_pct": t5_return,
        "spy_t5_return_pct": spy_t5_return,
        "abnormal_t5_pct": (
            (t5_return - spy_t5_return)
            if t5_return is not None and spy_t5_return is not None
            else None
        ),
    })

# 8. Print history aggregates (Tier B: reaction distribution only, no beat rate)
realized_moves = [
    abs(p["reaction_pct"]) for p in prints_with_reaction if p["reaction_pct"] is not None
]
n_q = len(prints_with_reaction)
realized_avg = sum(realized_moves) / len(realized_moves) if realized_moves else None
realized_med = median(realized_moves)

positive_reactions = [p for p in prints_with_reaction if p["reaction_pct"] > 0]
negative_reactions = [p for p in prints_with_reaction if p["reaction_pct"] <= 0]
best_reaction = max(prints_with_reaction, key=lambda p: p["reaction_pct"], default=None)
worst_reaction = min(prints_with_reaction, key=lambda p: p["reaction_pct"], default=None)

# 9. PEAD bucketed by reaction sign (Tier B substitute)
abnormal_pos = [
    p["abnormal_t5_pct"] for p in positive_reactions if p["abnormal_t5_pct"] is not None
]
abnormal_neg = [
    p["abnormal_t5_pct"] for p in negative_reactions if p["abnormal_t5_pct"] is not None
]

pead = {
    "on_positive_reactions": {
        "n": len(abnormal_pos),
        "avg_t5_return_pct": sum(abnormal_pos) / len(abnormal_pos) if abnormal_pos else None,
        "t_stat": t_stat(abnormal_pos),
        "significant": False,
    } if abnormal_pos else None,
    "on_negative_reactions": {
        "n": len(abnormal_neg),
        "avg_t5_return_pct": sum(abnormal_neg) / len(abnormal_neg) if abnormal_neg else None,
        "t_stat": t_stat(abnormal_neg),
        "significant": False,
    } if abnormal_neg else None,
}
for bucket_key in ("on_positive_reactions", "on_negative_reactions"):
    bucket = pead[bucket_key]
    if bucket and bucket["t_stat"] is not None:
        bucket["significant"] = abs(bucket["t_stat"]) > 2.0

# 10. Options snapshot for implied move + IV30 proxy
print("Fetching options snapshot...", file=sys.stderr)
spot_snap = fetch(f"/v2/snapshot/locale/us/markets/stocks/tickers/{TICKER}")["ticker"]
spot = spot_snap["lastQuote"]["p"] if spot_snap.get("lastQuote") else spot_snap["day"]["c"]

# Next print date: project from the latest acceptance (~91 days). EDGAR doesn't
# expose future dates, so this is a heuristic only used to bound the options query.
if prints_with_reaction:
    last_dt = datetime.fromisoformat(
        prints_with_reaction[-1]["filing_dt"].replace("Z", "+00:00")
    ).date()
    next_earnings_date = last_dt + timedelta(days=91)
else:
    next_earnings_date = TODAY + timedelta(days=37)
next_print_date_str = next_earnings_date.isoformat()

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
    put_at_strike = next(
        (o for o in puts if o["details"]["strike_price"] == call["details"]["strike_price"]),
        None,
    )
    if not put_at_strike:
        put_at_strike = min(puts, key=lambda o: abs(o["details"]["strike_price"] - spot_price))

    def mid(o):
        q = o.get("last_quote", {})
        bid = q.get("bid")
        ask = q.get("ask")
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            return (bid + ask) / 2
        return o.get("day", {}).get("close") or o.get("last_trade", {}).get("price")

    return {
        "call_mid": mid(call),
        "put_mid": mid(put_at_strike),
        "strike": call["details"]["strike_price"],
        "expiration": call["details"]["expiration_date"],
        "iv_call": call.get("implied_volatility"),
        "iv_put": put_at_strike.get("implied_volatility"),
    }


straddle_info = atm_straddle(opts_by_exp.get(chosen_expiry, []), spot) if chosen_expiry else None

iv30_proxy = None
if straddle_info and straddle_info["iv_call"] and straddle_info["iv_put"]:
    iv30_proxy = (straddle_info["iv_call"] + straddle_info["iv_put"]) / 2 * 100

straddle_pct = None
implied_move_pct = None
mispricing_pct = None
if straddle_info and straddle_info["call_mid"] and straddle_info["put_mid"] and spot:
    straddle_pct = (straddle_info["call_mid"] + straddle_info["put_mid"]) / spot
    implied_move_pct = straddle_pct * 0.85
    if realized_avg:
        mispricing_pct = (implied_move_pct - realized_avg) / realized_avg

# 11. Peer reaction using curated override
peer_tickers = PEER_OVERRIDES.get(TICKER, [])
peer_aggs = {}
peer_reaction_block = None
if peer_tickers:
    print(f"Fetching peer aggregates: {peer_tickers}...", file=sys.stderr)
    for peer in peer_tickers:
        peer_data = fetch_all(
            f"/v2/aggs/ticker/{peer}/range/1/day/{start_date}/{end_date}?adjusted=true&sort=asc&limit=50000"
        )
        peer_aggs[peer] = {
            datetime.fromtimestamp(a["t"] / 1000, tz=timezone.utc).date().isoformat(): a
            for a in peer_data
        }

    # Per-print peer returns, bucketed by reaction sign (Tier B substitute)
    peer_returns_on_pos = defaultdict(list)
    peer_returns_on_neg = defaultdict(list)
    peer_print_day_returns = defaultdict(list)
    name_print_day_returns = []

    for p in prints_with_reaction:
        ref_d = p["ref_date"]
        next_d = p["reaction_date"]
        name_ret = p["reaction_pct"]
        name_print_day_returns.append(name_ret)
        for peer in peer_tickers:
            pa = peer_aggs[peer]
            if ref_d in pa and next_d in pa:
                peer_ret = (pa[next_d]["c"] - pa[ref_d]["c"]) / pa[ref_d]["c"]
                peer_print_day_returns[peer].append(peer_ret)
                if name_ret > 0:
                    peer_returns_on_pos[peer].append(peer_ret)
                else:
                    peer_returns_on_neg[peer].append(peer_ret)
            else:
                peer_print_day_returns[peer].append(None)

    # Aggregate peer return on positive/negative reactions
    all_pos = [v for peer in peer_tickers for v in peer_returns_on_pos[peer]]
    all_neg = [v for peer in peer_tickers for v in peer_returns_on_neg[peer]]
    avg_peer_pos = sum(all_pos) / len(all_pos) if all_pos else None
    avg_peer_neg = sum(all_neg) / len(all_neg) if all_neg else None

    # Per-peer print-day beta
    def beta(peer_rets, name_rets):
        pairs = [
            (pr, nr)
            for pr, nr in zip(peer_rets, name_rets)
            if pr is not None and nr is not None
        ]
        if len(pairs) < 2:
            return None
        n = len(pairs)
        mean_p = sum(p for p, _ in pairs) / n
        mean_n = sum(nr for _, nr in pairs) / n
        cov = sum((p - mean_p) * (nr - mean_n) for p, nr in pairs) / (n - 1)
        var_n = sum((nr - mean_n) ** 2 for _, nr in pairs) / (n - 1)
        return cov / var_n if var_n > 0 else None

    peer_betas = []
    for peer in peer_tickers:
        b = beta(peer_print_day_returns[peer], name_print_day_returns)
        if b is not None:
            peer_betas.append({"ticker": peer, "beta": b})
    peer_betas.sort(key=lambda x: -abs(x["beta"]))

    peer_reaction_block = {
        "peer_selection_method": "curated_override",
        "peers_used": peer_tickers,
        "n_peers": len(peer_tickers),
        "n_cycles": n_q,
        "bucketing": "reaction_sign",
        "avg_peer_return_on_positive_reaction_pct": avg_peer_pos,
        "avg_peer_return_on_negative_reaction_pct": avg_peer_neg,
        "top_peers": peer_betas[:3],
    }

# 12. Build JSON payload
tier_caveats = [
    "No consensus EPS available; print history shows reaction distribution only.",
    "PEAD bucketed by reaction sign, not surprise sign.",
    "Peer reaction bucketed by reaction sign, not surprise sign.",
    "Next print date is a heuristic projection (~91d from last 8-K); EDGAR only exposes filed events.",
    "EPS actuals are GAAP (from 10-Q/10-K); Benzinga's Tier A actuals are non-GAAP adjusted, so Q4 2024 reads $0.97 (GAAP, after EU tax charge) vs Tier A's $1.64 (adjusted).",
    "Q4 EPS is derived from the annual 10-K minus Q1+Q2+Q3 quarterlies (no standalone Q4 record).",
]

payload = {
    "ticker": TICKER,
    "tier": "B",
    "tier_caveats": tier_caveats,
    "mode": "full",
    "run_at": datetime.now(timezone.utc).isoformat(),
    "print": {
        "date": next_print_date_str,
        "session": "AMC",
        "consensus_eps": None,
        "consensus_revenue": None,
        "fiscal_period": None,
        "source": "projected (~91d from last 8-K acceptance)",
    },
    "spot": spot,
    "implied_vs_realized": {
        "straddle_pct": straddle_pct,
        "implied_move_pct": implied_move_pct,
        "realized_avg_pct": realized_avg,
        "realized_median_pct": realized_med,
        "n_quarters": n_q,
        "iv30_proxy": iv30_proxy,
        "iv30_source": "Average IV of ATM call+put on earnings-capturing expiry",
        "mispricing_pct": mispricing_pct,
        "front_expiry_used": chosen_expiry,
        "atm_strike_used": straddle_info["strike"] if straddle_info else None,
    } if straddle_pct is not None else None,
    "print_history": {
        "n_quarters": n_q,
        "n_positive_reactions": len(positive_reactions),
        "n_negative_reactions": len(negative_reactions),
        "best_reaction": {
            "period": best_reaction["fiscal_period"] if best_reaction else None,
            "print_date": best_reaction["filing_date"] if best_reaction else None,
            "next_day_return_pct": best_reaction["reaction_pct"] if best_reaction else None,
        } if best_reaction else None,
        "worst_reaction": {
            "period": worst_reaction["fiscal_period"] if worst_reaction else None,
            "print_date": worst_reaction["filing_date"] if worst_reaction else None,
            "next_day_return_pct": worst_reaction["reaction_pct"] if worst_reaction else None,
        } if worst_reaction else None,
        "all_prints": [
            {
                "period": p["fiscal_period"],
                "print_date": p["filing_date"],
                "acceptance_dt_utc": p["filing_dt"],
                "session": p["session"],
                "eps_actual": p["eps_actual"],
                "reaction_pct": p["reaction_pct"],
                "t5_abnormal_pct": p["abnormal_t5_pct"],
            }
            for p in prints_with_reaction
        ],
    },
    "post_earnings_drift": pead,
    "peer_reaction": peer_reaction_block,
    "sources": [
        {"endpoint": f"/v3/reference/tickers/{TICKER}", "context": "ticker metadata, CIK, SIC"},
        {"endpoint": f"https://data.sec.gov/submissions/CIK{cik_padded}.json", "context": "8-K item 2.02 filings → print dates (free, public)"},
        {"endpoint": f"/vX/reference/financials?ticker={TICKER}", "context": "EPS actuals matched by filing_date"},
        {"endpoint": f"/v2/aggs/ticker/{TICKER}/range/1/day/...", "context": "daily closes for realized moves and PEAD"},
        {"endpoint": "/v2/aggs/ticker/SPY/range/1/day/...", "context": "SPY for PEAD beta-adjustment"},
        {"endpoint": f"/v2/snapshot/locale/us/markets/stocks/tickers/{TICKER}", "context": "current spot"},
        {"endpoint": f"/v3/snapshot/options/{TICKER}?...", "context": "ATM straddle and IV"},
        {"endpoint": "/v2/aggs/ticker/{PEER}/range/1/day/... (curated peers)", "context": "peer reaction & beta"},
    ],
}


# 13. Render the note
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
lines.append(
    f"{TICKER}: Tier B Preview (run {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)"
)
header_extras = []
header_extras.append(f"Next print (projected): {payload['print']['date']} AMC")
header_extras.append(f"Spot: ${spot:.2f}")
header_extras.append("Tier B (SEC EDGAR + Stocks Starter, no Benzinga)")
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

ph = payload["print_history"]
lines.append(f"Print history (last {n_q} quarters, Tier B: reaction distribution only)")
lines.append(
    f"- Reactions: {ph['n_positive_reactions']} positive / {ph['n_negative_reactions']} negative"
)
if best_reaction:
    lines.append(
        f"- Best reaction: {best_reaction['fiscal_period']} {fmt_signed_pct(best_reaction['reaction_pct'])} next day"
    )
if worst_reaction:
    lines.append(
        f"- Worst reaction: {worst_reaction['fiscal_period']} {fmt_signed_pct(worst_reaction['reaction_pct'])} next day"
    )
lines.append("")

if pead.get("on_positive_reactions") or pead.get("on_negative_reactions"):
    lines.append("Post-earnings drift (T+1 to T+5, SPY-adjusted, bucketed by reaction sign)")
    if pead.get("on_positive_reactions") and pead["on_positive_reactions"]["avg_t5_return_pct"] is not None:
        b = pead["on_positive_reactions"]
        if b["n"] < 4:
            sig = "sample too small"
        elif b["significant"]:
            sig = "significant"
        elif b["t_stat"] is not None:
            sig = f"t-stat {b['t_stat']:.2f}, not significant"
        else:
            sig = "sample too small"
        lines.append(f"- After positive reactions: {fmt_signed_pct(b['avg_t5_return_pct'])} avg (n={b['n']}, {sig})")
    if pead.get("on_negative_reactions") and pead["on_negative_reactions"]["avg_t5_return_pct"] is not None:
        m = pead["on_negative_reactions"]
        if m["n"] < 4:
            sig = "sample too small"
        elif m["significant"]:
            sig = "significant"
        elif m["t_stat"] is not None:
            sig = f"t-stat {m['t_stat']:.2f}, not significant"
        else:
            sig = "sample too small"
        lines.append(f"- After negative reactions: {fmt_signed_pct(m['avg_t5_return_pct'])} avg (n={m['n']}, {sig})")
    lines.append("")

if peer_reaction_block:
    pr = peer_reaction_block
    lines.append(f"Peer reaction ({pr['peer_selection_method']}, bucketed by {pr['bucketing']})")
    lines.append(f"- Peers: {', '.join(pr['peers_used'])}")
    if pr["avg_peer_return_on_positive_reaction_pct"] is not None:
        lines.append(
            f"- Peers on positive AAPL reactions: {fmt_signed_pct(pr['avg_peer_return_on_positive_reaction_pct'])} avg"
        )
    if pr["avg_peer_return_on_negative_reaction_pct"] is not None:
        lines.append(
            f"- Peers on negative AAPL reactions: {fmt_signed_pct(pr['avg_peer_return_on_negative_reaction_pct'])} avg"
        )
    if pr["top_peers"]:
        top_str = ", ".join(f"{t['ticker']} β={t['beta']:.2f}" for t in pr["top_peers"])
        lines.append(f"- Top peers by print-day β: {top_str}")
    lines.append("")

lines.append("Per-print detail (for inspection)")
lines.append("| Period | Print date | Acceptance (UTC) | Session | EPS actual | Day-1 reaction | T+5 abnormal |")
lines.append("|---|---|---|---|---|---|---|")
for p in prints_with_reaction:
    eps_a = f"${p['eps_actual']:.2f}" if p["eps_actual"] is not None else "n/a"
    react = fmt_signed_pct(p["reaction_pct"])
    t5 = fmt_signed_pct(p["abnormal_t5_pct"]) if p["abnormal_t5_pct"] is not None else "n/a"
    acc = p["filing_dt"].replace("T", " ").replace(".000Z", "Z").replace("Z", "")
    lines.append(
        f"| {p['fiscal_period']} | {p['filing_date']} | {acc} | {p['session']} | {eps_a} | {react} | {t5} |"
    )

lines.append("")
lines.append("Tier B caveats")
for c in tier_caveats:
    lines.append(f"- {c}")

rendered = "\n".join(lines)

# 14. Write output
out_path = os.path.join(os.path.dirname(__file__), "aapl-tier-b-output.md")
with open(out_path, "w") as f:
    f.write("# Tier B run: earnings-drilldown AAPL\n\n")
    f.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
    f.write(f"Spot at run: ${spot:.2f}\n")
    f.write("Tier: B (SEC EDGAR submissions + Massive Stocks Starter, no Benzinga)\n\n")
    f.write("## Layer 1: canonical JSON (live data)\n\n")
    f.write("```json\n")
    f.write(json.dumps(payload, indent=2, default=str))
    f.write("\n```\n\n")
    f.write("## Layer 2: rendered note (live data)\n\n")
    f.write("```\n")
    f.write(rendered)
    f.write("\n```\n\n")
    f.write("## Tier B vs Tier A\n\n")
    f.write("- Print dates: derived from SEC 8-K item 2.02 acceptance times (UTC).\n")
    f.write("- EPS actuals: from Massive `/vX/reference/financials` matched by `filing_date`.\n")
    f.write("- Beat rate / consensus / surprise: NOT available; this is the cost of dropping Benzinga.\n")
    f.write("- PEAD and peer reaction: bucketed by next-day return sign instead of surprise sign.\n")
    f.write("- Implied vs realized block: identical methodology to Tier A (same options + aggs).\n")

print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
print(rendered)
