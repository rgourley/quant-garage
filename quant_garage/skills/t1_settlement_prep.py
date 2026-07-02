"""
t+1-settlement-prep as an importable library function.

CSV of pending trades in → T+1 settlement date + holiday/corp-action
flags per trade → exception report.

    from quant_garage.skills.t1_settlement_prep import run, render
    payload = run("examples/sample-trades.csv")
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from .. import MassiveClient, FetchError, utcnow_iso
from ..timezones import utc_to_et

# ----- Config -----

# DTCC half-day cutoff (hard-coded per references/holiday-calendar.md)
DTCC_HALF_DAY_CUTOFF_ET = "12:30"

# Fallback NYSE holiday calendar; exercised only if the live endpoint fails.
NYSE_HOLIDAYS_FALLBACK = {
    "2026-01-01": ("New Year's Day", "closed"),
    "2026-01-19": ("Martin Luther King, Jr. Day", "closed"),
    "2026-02-16": ("Washington's Birthday", "closed"),
    "2026-04-03": ("Good Friday", "closed"),
    "2026-05-25": ("Memorial Day", "closed"),
    "2026-06-19": ("Juneteenth", "closed"),
    "2026-07-03": ("Independence Day (observed)", "closed"),
    "2026-09-07": ("Labor Day", "closed"),
    "2026-11-26": ("Thanksgiving", "closed"),
    "2026-11-27": ("Day after Thanksgiving", "early-close"),
    "2026-12-24": ("Christmas Eve", "early-close"),
    "2026-12-25": ("Christmas", "closed"),
    "2027-01-01": ("New Year's Day", "closed"),
    "2027-01-18": ("Martin Luther King, Jr. Day", "closed"),
    "2027-02-15": ("Washington's Birthday", "closed"),
    "2027-03-26": ("Good Friday", "closed"),
    "2027-05-31": ("Memorial Day", "closed"),
    "2027-06-18": ("Juneteenth", "closed"),
    "2027-07-05": ("Independence Day (observed)", "closed"),
    "2027-09-06": ("Labor Day", "closed"),
    "2027-11-25": ("Thanksgiving", "closed"),
    "2027-11-26": ("Day after Thanksgiving", "early-close"),
    "2027-12-23": ("Christmas Eve", "early-close"),
    "2027-12-24": ("Christmas (observed)", "closed"),
}


client = MassiveClient()


def paginate_all(path, params=None):
    """Collect every page from the Massive client. Returns (results, last_fetched_at)."""
    out = []
    last_fetched = utcnow_iso()
    try:
        for page, fetched_at in client.paginate(path, params):
            out.extend(page)
            last_fetched = fetched_at
    except FetchError as e:
        print(f"  WARN: paginated fetch failed on {path}: {e}", file=sys.stderr)
    return out, last_fetched


# ----- CSV loading -----

VALID_SIDES = {"BUY", "SELL", "SHORT", "COVER"}


def load_trades(path):
    trades = []
    with open(path) as f:
        for row in csv.DictReader(f):
            side = row["side"].strip().upper()
            if side not in VALID_SIDES:
                raise ValueError(f"unknown side '{side}' in trade row {row}")
            trades.append({
                "ticker": row["ticker"].strip().upper(),
                "side": side,
                "qty": float(row["qty"]),
                "trade_date": date.fromisoformat(row["trade_date"].strip()),
            })
    return trades


# ----- Holiday calendar -----

def fetch_holiday_calendar():
    """
    Pull /v1/marketstatus/upcoming and return:
      ({ "YYYY-MM-DD": {"name": ..., "status": "closed"|"early-close",
                        "close_et": "HH:MM" or None } },
       "path",
       "fetched_at")
    De-dupes NYSE/NASDAQ rows by date (treats NYSE as canonical).
    Falls back to NYSE_HOLIDAYS_FALLBACK if the endpoint fails.

    UTC → ET conversion of `close` timestamps uses zoneinfo via utc_to_et
    so DST is correct year-round (H1 in the 2026-06-26 audit).
    """
    path = "/v1/marketstatus/upcoming"
    try:
        body, fetched_at = client.get(path)
    except FetchError as e:
        print(f"  WARN: /v1/marketstatus/upcoming failed ({e}); using fallback", file=sys.stderr)
        return _build_calendar_from_fallback(), "fallback", utcnow_iso()

    # The endpoint returns a bare JSON array, not a {results: [...]} envelope.
    # `client.get` returns whatever the API returned, so `body` is the list.
    if not isinstance(body, list):
        print(f"  WARN: /v1/marketstatus/upcoming returned non-list; using fallback", file=sys.stderr)
        return _build_calendar_from_fallback(), "fallback", fetched_at

    cal = {}
    for ev in body:
        d = ev.get("date")
        if not d or d in cal:
            continue  # First-occurrence wins; NYSE precedes NASDAQ in the response
        status_str = ev.get("status")
        close_et = None
        if status_str == "early-close" and ev.get("close"):
            close_dt = datetime.fromisoformat(ev["close"].replace("Z", "+00:00"))
            close_et_dt = utc_to_et(close_dt)
            close_et = close_et_dt.strftime("%H:%M")
        cal[d] = {
            "name": ev.get("name", ""),
            "status": status_str,
            "close_et": close_et,
        }
    return cal, path, fetched_at


def _build_calendar_from_fallback():
    cal = {}
    for d, (name, status) in NYSE_HOLIDAYS_FALLBACK.items():
        cal[d] = {
            "name": name,
            "status": status,
            "close_et": "13:00" if status == "early-close" else None,
        }
    return cal


def is_business_day(d, calendar):
    """A US equity-market business day: not Sat/Sun, not a closed holiday."""
    if d.weekday() >= 5:  # 5 = Sat, 6 = Sun
        return False
    holiday = calendar.get(d.isoformat())
    if holiday and holiday["status"] == "closed":
        return False
    return True


def compute_settlement_date(trade_date, calendar):
    """
    Walk forward from trade_date + 1 calendar day until we hit a business
    day. Return:
      (naive_settlement, computed_settlement,
       crossed_weekend, holiday_name_or_none)

    crossed_weekend is True if the naive T+1 was Sat or Sun.
    holiday_name_or_none is the name of any holiday hit during the walk
    (the first one encountered).
    """
    naive = trade_date + timedelta(days=1)
    crossed_weekend = naive.weekday() >= 5
    holiday_hit = None
    candidate = naive
    while not is_business_day(candidate, calendar):
        if candidate.weekday() < 5:
            event = calendar.get(candidate.isoformat())
            if event and event["status"] == "closed" and holiday_hit is None:
                holiday_hit = event["name"]
        candidate += timedelta(days=1)
    return naive, candidate, crossed_weekend, holiday_hit


# ----- Ref-data caches -----

_div_cache = {}
_split_cache = {}


def fetch_dividends_for_ticker(ticker, window_start):
    """
    Pull dividends with ex_dividend_date.gte=window_start - 30 days
    (some slack to catch ex-dates announced after as well as before).
    Returns (results, endpoint_path, fetched_at).
    """
    if ticker in _div_cache:
        return _div_cache[ticker]
    cutoff = (window_start - timedelta(days=30)).isoformat()
    path = "/v3/reference/dividends"
    params = {
        "ticker": ticker,
        "ex_dividend_date.gte": cutoff,
        "limit": 100,
        "order": "asc",
        "sort": "ex_dividend_date",
    }
    results, fetched_at = paginate_all(path, params)
    _div_cache[ticker] = (results, path, fetched_at)
    return _div_cache[ticker]


def fetch_splits_for_ticker(ticker, window_start):
    if ticker in _split_cache:
        return _split_cache[ticker]
    cutoff = (window_start - timedelta(days=7)).isoformat()
    path = "/v3/reference/splits"
    params = {
        "ticker": ticker,
        "execution_date.gte": cutoff,
        "limit": 50,
        "order": "asc",
        "sort": "execution_date",
    }
    results, fetched_at = paginate_all(path, params)
    _split_cache[ticker] = (results, path, fetched_at)
    return _split_cache[ticker]


# ----- Per-trade flagging -----

def flag_trade(trade, calendar, fetched_at_calendar):
    ticker = trade["ticker"]
    side = trade["side"]
    qty = trade["qty"]
    td = trade["trade_date"]

    naive, settle, crossed_weekend, holiday_name = compute_settlement_date(td, calendar)
    push_days = (settle - naive).days

    reasons = []
    impact_bits = []
    suggest_bits = []
    extras = {}

    # Weekend crossing
    if crossed_weekend:
        reasons.append("weekend_crossing")
        impact_bits.append(
            f"Cash needed {settle.strftime('%A %Y-%m-%d')}, "
            f"not {naive.strftime('%A %Y-%m-%d')}"
        )
        suggest_bits.append("Update cash forecast for adjusted settlement date")

    # Holiday adjacency (overlaps with weekend on long weekends)
    if holiday_name:
        reasons.append("holiday_adjacency")
        impact_bits.append(
            f"Cash needed {settle.strftime('%A %Y-%m-%d')}, "
            f"not {naive.strftime('%A %Y-%m-%d')}"
        )
        suggest_bits.append("Update cash forecast; notify treasury for adjusted funding")
        extras["holiday_name"] = holiday_name

    # Short-sale locate
    if side == "SHORT":
        reasons.append("short_sale_locate")
        impact_bits.append("Trade may fail without locate on file before T+1 cutoff")
        suggest_bits.append("Confirm locate ticket with prime broker before EOD")

    # Half-day settlement
    settle_holiday = calendar.get(settle.isoformat())
    if settle_holiday and settle_holiday["status"] == "early-close":
        reasons.append("half_day_settlement")
        close_et = settle_holiday.get("close_et") or "13:00"
        impact_bits.append(f"DTCC cutoff is {DTCC_HALF_DAY_CUTOFF_ET} ET instead of 15:00 ET")
        suggest_bits.append(
            f"Confirm trade is in DTCC queue before {DTCC_HALF_DAY_CUTOFF_ET} ET; "
            f"tighter cash-management window"
        )
        extras["session_info"] = {
            "close_et": close_et,
            "dtcc_cutoff_et": DTCC_HALF_DAY_CUTOFF_ET,
            "reason": settle_holiday["name"],
        }

    # Ex-dividend in window.
    #
    # Cum-dividend entitlement: a buyer who trades STRICTLY BEFORE the
    # ex-date gets the dividend. A trade ON the ex-date is "ex" - the
    # buyer is NOT entitled, even though settlement may be after the
    # ex-date. Entitlement is locked at the trade-date side; holidays
    # that push settle out do NOT widen the entitlement window.
    # (Audit C10, 2026-06-26.)
    #
    # The right edge of the scan is the true T+1 boundary (`settle`,
    # which compute_settlement_date returns as the next business day).
    # Holiday-pushed settlements don't extend dividend entitlement.
    divs, div_path, _ = fetch_dividends_for_ticker(ticker, td)
    div_in_window = None
    ex_date_trade = None  # buyer NOT entitled (informational, not a break)
    for d in divs:
        ex_str = d.get("ex_dividend_date")
        if not ex_str:
            continue
        try:
            ex_dt = date.fromisoformat(ex_str)
        except ValueError:
            continue
        if td < ex_dt <= settle:
            # Trade strictly before ex-date: buyer IS entitled.
            div_in_window = d
            break
        if td == ex_dt and ex_date_trade is None:
            # Trade ON ex-date: buyer is NOT entitled. Record for the
            # informational notice; do not flag as a break.
            ex_date_trade = d
    if div_in_window:
        reasons.append("ex_dividend_in_window")
        amt = float(div_in_window.get("cash_amount") or 0)
        dollar_impact = round(qty * amt, 2)
        impact_bits.append(
            f"${amt:.2f}/share dividend (~${dollar_impact:,.0f}) allocated to buyer"
        )
        suggest_bits.append("Verify dividend entitlement flag on trade ticket")
        extras["dividend"] = {
            "ex_date": div_in_window["ex_dividend_date"],
            "pay_date": div_in_window.get("pay_date"),
            "cash_amount_per_share": amt,
            "currency": div_in_window.get("currency", "USD"),
            "dividend_type": div_in_window.get("dividend_type"),
            "dollar_impact": dollar_impact,
        }
    elif ex_date_trade is not None:
        # Informational: trade is on ex-date, buyer is NOT entitled.
        # Surface it so operators don't expect the dividend, but do not
        # add to reasons[] (no break).
        amt = float(ex_date_trade.get("cash_amount") or 0)
        dollar_impact = round(qty * amt, 2)
        extras["dividend_ex_date_notice"] = {
            "ex_date": ex_date_trade["ex_dividend_date"],
            "pay_date": ex_date_trade.get("pay_date"),
            "cash_amount_per_share": amt,
            "currency": ex_date_trade.get("currency", "USD"),
            "dividend_type": ex_date_trade.get("dividend_type"),
            "dollar_impact": dollar_impact,
            "note": (
                "Trade is on ex-date; buyer is NOT entitled to the "
                "dividend. Cum-dividend entitlement requires a trade "
                "STRICTLY BEFORE the ex-date."
            ),
        }

    # Corp-action overlap (splits)
    splits, split_path, _ = fetch_splits_for_ticker(ticker, td)
    split_in_window = None
    for s in splits:
        ex_str = s.get("execution_date")
        if not ex_str:
            continue
        try:
            ex_dt = date.fromisoformat(ex_str)
        except ValueError:
            continue
        if td <= ex_dt <= settle:
            split_in_window = s
            break
    if split_in_window:
        reasons.append("corp_action_overlap")
        st = float(split_in_window["split_to"])
        sf = float(split_in_window["split_from"])
        is_reverse = st < sf
        kind = "reverse_split" if is_reverse else "split"
        if is_reverse:
            ratio_str = f"{int(sf)}-for-{int(st)}"
            direction = "reverse"
        else:
            ratio_str = f"{int(st)}-for-{int(sf)}"
            direction = "forward"
        post_qty = qty * (st / sf)
        impact_bits.append(
            f"DTCC delivers {post_qty:,.0f} shares post-adjustment instead of {qty:,.0f}"
        )
        suggest_bits.append("Confirm position-reconciliation system reflects the split")
        extras["corp_action"] = {
            "ex_date": split_in_window["execution_date"],
            "kind": kind,
            "ratio": ratio_str,
            "direction": direction,
            "split_to": st,
            "split_from": sf,
            "post_action_qty": post_qty,
        }

    if not reasons:
        return None

    impact_text = "; ".join(impact_bits)
    suggested = "; ".join(suggest_bits)

    flagged = {
        "ticker": ticker,
        "side": side,
        "qty": qty,
        "trade_date": td.isoformat(),
        "naive_settlement_date": naive.isoformat(),
        "computed_settlement_date": settle.isoformat(),
        "calendar_push_days": push_days,
        "reason_codes": reasons,
        "impact_text": impact_text,
        "suggested_next_action": suggested,
        "dividend": extras.get("dividend"),
        "dividend_ex_date_notice": extras.get("dividend_ex_date_notice"),
        "corp_action": extras.get("corp_action"),
        "session_info": extras.get("session_info"),
        "source": {
            "endpoint": "/v1/marketstatus/upcoming, /v3/reference/dividends, /v3/reference/splits",
            "fetched_at": fetched_at_calendar,
        },
    }
    if extras.get("holiday_name"):
        flagged["holiday_name"] = extras["holiday_name"]
    return flagged


# ----- Rendering -----

REASON_TEXT = {
    "weekend_crossing": "Settlement falls on weekend; pushed to next business day",
    "short_sale_locate": "Short sale; locate confirmation required",
    "ex_dividend_in_window": "Buyer entitled to dividend (purchased strictly before ex-date)",
    "half_day_settlement": "Settlement date is a half-day session",
    "symbol_change": "Ticker changed symbol between trade and settlement",
}


def reason_text_for(flagged):
    bits = []
    for code in flagged["reason_codes"]:
        if code == "holiday_adjacency":
            name = flagged.get("holiday_name") or "market holiday"
            bits.append(f"Settlement crosses {name}; pushed to next business day")
        elif code == "corp_action_overlap":
            ca = flagged["corp_action"]
            bits.append(
                f"{ca['ratio']} {ca['direction']} split with ex-date in settlement window"
            )
        elif code == "half_day_settlement":
            si = flagged["session_info"]
            bits.append(f"Settlement date is a half-day session ({si['reason']})")
        else:
            bits.append(REASON_TEXT.get(code, code))
    return "; ".join(bits)


def fmt_qty(x):
    return f"{int(x):,}" if x == int(x) else f"{x:,.2f}"


def render(payload):
    out = []
    if payload.get("take"):
        out.append(payload["take"])
        out.append("")

    trades_checked = payload["trades_checked"]
    flagged_count = payload["flagged_count"]
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    if flagged_count == 0:
        out.append(
            f"T+1 settlement prep: {trades_checked} trades checked · "
            f"No breaks flagged · run {as_of} UTC"
        )
    else:
        out.append(
            f"T+1 settlement prep: {trades_checked} trades checked · "
            f"{flagged_count} BREAKs flagged · run {as_of} UTC"
        )
    out.append("")

    sp = payload["scan_params"]
    sw = payload["summary"]["settlement_window"]
    out.append(
        f"Universe: {trades_checked} trades from {sp['file_in']} "
        f"{sw['from']} → {sw['to']}"
    )
    out.append("Settlement cycle: T+1 (US equities, post-May 2024)")

    if flagged_count == 0:
        out.append("")
        return "\n".join(out) + "\n"

    out.append("")
    out.append(f"FLAGGED TRADES ({flagged_count})")
    out.append("")

    for i, f in enumerate(payload["flagged"], 1):
        header = (
            f"BREAK {i}: {f['ticker']} {f['side']} {fmt_qty(f['qty'])} · "
            f"trade {f['trade_date']} · settlement "
        )
        if f["computed_settlement_date"] != f["naive_settlement_date"]:
            header += f"{f['naive_settlement_date']} → {f['computed_settlement_date']}"
        else:
            header += f"{f['computed_settlement_date']}"
        # Append context tail per reason
        if "half_day_settlement" in f["reason_codes"] and f.get("session_info"):
            header += f" (half-day, {f['session_info']['close_et']} ET close)"
        elif "ex_dividend_in_window" in f["reason_codes"] and f.get("dividend"):
            header += f" · ex-div {f['dividend']['ex_date']}"
        elif "corp_action_overlap" in f["reason_codes"] and f.get("corp_action"):
            header += f" · split ex-date {f['corp_action']['ex_date']}"
        out.append(header)

        out.append(f"  Reason:        {reason_text_for(f)}")
        out.append(f"  Impact:        {f['impact_text']}")
        out.append(f"  Suggest:       {f['suggested_next_action']}")
        notice = f.get("dividend_ex_date_notice")
        if notice:
            amt = notice.get("cash_amount_per_share", 0) or 0
            dollar = notice.get("dollar_impact", 0) or 0
            out.append(
                f"  Notice:        ${amt:.2f}/share dividend "
                f"(~${dollar:,.0f}) NOT allocated to buyer "
                f"(ex-date trade {notice['ex_date']})"
            )
        out.append("")

    # Summary block
    s = payload["summary"]
    by = s["by_reason"]
    out.append("Summary")
    out.append(
        f"- {flagged_count} flagged of {trades_checked} "
        f"({s['break_rate_pct']:.0f}% break rate)"
    )
    rendered_rows = []
    if by.get("holiday_adjacency"):
        rendered_rows.append(f"- Holiday adjacency: {by['holiday_adjacency']} trade(s)")
    if by.get("weekend_crossing"):
        rendered_rows.append(f"- Weekend crossing: {by['weekend_crossing']} trade(s)")
    if by.get("short_sale_locate"):
        rendered_rows.append(f"- Short sale locate: {by['short_sale_locate']} trade(s)")
    if by.get("ex_dividend_in_window"):
        rendered_rows.append(f"- Ex-dividend timing: {by['ex_dividend_in_window']} trade(s)")
    if by.get("half_day_settlement"):
        rendered_rows.append(f"- Half-day session: {by['half_day_settlement']} trade(s)")
    if by.get("corp_action_overlap"):
        rendered_rows.append(
            f"- Corporate-action overlap: {by['corp_action_overlap']} trade(s)"
        )
    else:
        rendered_rows.append("- 0 corporate-action overlap detected")
    out.extend(rendered_rows)
    out.append(
        f"- Settlement window: {s['settlement_window']['from']} "
        f"through {s['settlement_window']['to']}"
    )

    return "\n".join(out) + "\n"


# ----- Public API -----

def run(csv_path: str, client_: MassiveClient | None = None) -> dict:
    """Analyze a trades CSV for T+1 settlement risk. Returns exception report."""
    global client
    if client_ is not None:
        client = client_
    trades = load_trades(csv_path)
    if not trades:
        raise ValueError("no trades in input")

    print(f"Loaded {len(trades)} trades from {csv_path}", file=sys.stderr)

    # Fetch the holiday calendar once.
    calendar, calendar_path, cal_fetched_at = fetch_holiday_calendar()
    print(f"Holiday calendar: {len(calendar)} events from {calendar_path}", file=sys.stderr)

    sources = [{
        "endpoint": f"https://api.polygon.io{calendar_path}" if calendar_path.startswith("/") else calendar_path,
        "fetched_at": cal_fetched_at,
        "ticker": None,
    }]

    flagged = []
    settlement_dates = []
    for i, trade in enumerate(trades, 1):
        ticker = trade["ticker"]
        print(
            f"  [{i}/{len(trades)}] {ticker} {trade['side']} "
            f"{int(trade['qty'])} trade {trade['trade_date']}",
            file=sys.stderr,
        )
        try:
            result = flag_trade(trade, calendar, cal_fetched_at)
            # Always compute settlement date for window math, flagged or not
            _, settle, _, _ = compute_settlement_date(trade["trade_date"], calendar)
            settlement_dates.append(settle)
        except Exception as e:
            print(f"    ERROR: {e}", file=sys.stderr)
            continue
        if result:
            flagged.append(result)
            print(f"    FLAG: {', '.join(result['reason_codes'])}", file=sys.stderr)

    # Record dividend + split sources actually hit (per-call fetched_at, M8)
    for ticker, (_, path, fetched_at) in _div_cache.items():
        sources.append({
            "endpoint": f"https://api.polygon.io{path}",
            "fetched_at": fetched_at,
            "ticker": ticker,
        })
    for ticker, (_, path, fetched_at) in _split_cache.items():
        sources.append({
            "endpoint": f"https://api.polygon.io{path}",
            "fetched_at": fetched_at,
            "ticker": ticker,
        })

    # Build summary
    by_reason = defaultdict(int)
    for f in flagged:
        for r in f["reason_codes"]:
            by_reason[r] += 1

    trade_dates = [t["trade_date"] for t in trades]
    window_from = min(trade_dates).isoformat()
    window_to = max(settlement_dates).isoformat() if settlement_dates else window_from

    # Take line
    if not flagged:
        take = f"No breaks across {len(trades)} trades · all settlements fall on full business days"
    else:
        bits = []
        if by_reason.get("holiday_adjacency") or by_reason.get("weekend_crossing"):
            bits.append("settlement-window push")
        if by_reason.get("short_sale_locate"):
            bits.append(f"{by_reason['short_sale_locate']} short-locate prompt(s)")
        if by_reason.get("ex_dividend_in_window"):
            bits.append(f"{by_reason['ex_dividend_in_window']} ex-div allocation question(s)")
        if by_reason.get("corp_action_overlap"):
            bits.append(f"{by_reason['corp_action_overlap']} split overlap(s)")
        if by_reason.get("half_day_settlement"):
            bits.append(f"{by_reason['half_day_settlement']} half-day session(s)")
        take_tail = " · ".join(bits) if bits else "see flags"
        take = (
            f"{len(flagged)} of {len(trades)} trades flagged · {take_tail}"
        )

    payload = {
        "tier": "A",
        "mode": "exception-report",
        "scan_params": {
            "file_in": csv_path,
            "settlement_cycle": "T+1",
            "as_of_utc": cal_fetched_at,
        },
        "trades_checked": len(trades),
        "flagged_count": len(flagged),
        "flagged": flagged,
        "summary": {
            "by_reason": dict(by_reason),
            "settlement_window": {"from": window_from, "to": window_to},
            "break_rate_pct": round(len(flagged) / len(trades) * 100, 1),
        },
        "take": take,
        "sources": dedupe_sources(sources),
    }
    return payload


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


# CLI removed — see examples/run-t1-settlement-prep.py
