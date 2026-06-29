#!/usr/bin/env python3
"""
Reference implementation of the technical-briefing skill.

Single ticker in → composite trend regime, RSI momentum read, MACD
cross status, key MAs (20/50/200), Bollinger position, ATR as % of
price, ADV-bucketed liquidity context. The first question a retail
trader or junior analyst asks about a name: "what does the chart
say right now?" — answered in a sell-side-quality briefing.

This script does NOT predict direction. It reads standard textbook
indicators (Wilder RSI, MACD 12/26/9, Bollinger 20/2σ, ATR 14) and
labels the composite trend regime. The Take is computed from the
actual readings, not hardcoded.

Two output layers:
  Layer 1: canonical JSON matching skills/technical-briefing/output-schema.json
  Layer 2: rendered briefing block to examples/technical-briefing-output.md

Usage:
    python3 examples/run-technical-briefing.py \\
      --ticker NVDA \\
      --lookback-days 252 \\
      --format render

Reads MASSIVE_API_KEY from env.
"""
from __future__ import annotations

import os
import sys
import json
import argparse
from datetime import datetime, timezone, timedelta

import numpy as np

# Make `lib.quant_garage` importable when running from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    FetchError,
    today,
    utcnow_iso,
    resolve_output_format,
    emit_to_stdout,
    sma,
    ema,
    rsi,
    macd,
    bollinger,
    atr,
)


client = MassiveClient()
NOW_UTC = datetime.now(timezone.utc)
TODAY = today()


# ----- HTTP -----

def fetch_daily_aggs(ticker: str, lookback_days: int) -> list[dict]:
    """Pull daily OHLC for `ticker` covering ~lookback_days trading days.

    Overshoots by 1.6x calendar days to handle weekends/holidays. Returns
    sorted-ascending records {date, open, high, low, close, volume}.
    """
    end = TODAY
    start = end - timedelta(days=int(lookback_days * 1.6) + 10)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true"
    )
    doc, _ = client.get(path)
    results = doc.get("results") or []
    rows: list[dict] = []
    for r in results:
        ts_ms = r.get("t")
        o, h, l, c, v = r.get("o"), r.get("h"), r.get("l"), r.get("c"), r.get("v")
        if ts_ms is None or c is None or h is None or l is None:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({
            "date": d,
            "open": float(o) if o is not None else float(c),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v) if v is not None else 0.0,
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def fetch_snapshot(ticker: str) -> dict | None:
    """Pull /v2/snapshot for current bid/ask/spread. Returns None on failure."""
    path = f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
    try:
        doc, _ = client.get(path)
    except FetchError as exc:
        print(f"  WARN: snapshot for {ticker}: {exc}", file=sys.stderr)
        return None
    return doc.get("ticker") if isinstance(doc, dict) else None


# ----- Derivation -----

def classify_trend(
    price: float,
    sma20: float,
    sma50: float,
    sma200: float,
    macd_line: float,
    rsi_val: float,
) -> tuple[str, list[str]]:
    """Composite trend regime label + reasons. Five buckets per the spec."""
    reasons: list[str] = []

    nans = [v for v in (sma20, sma50, sma200, macd_line, rsi_val) if not np.isfinite(v)]
    if nans:
        return "neutral", ["insufficient history for full trend read"]

    # bullish_strong: stacked MAs + positive MACD + RSI not weak (>= 50).
    # RSI > 70 doesn't invalidate the trend — it just means it's hot;
    # the momentum read carries the "overbought" detail separately.
    if (
        price > sma20 > sma50 > sma200
        and macd_line > 0
        and rsi_val >= 50.0
    ):
        reasons.append(f"price > SMA(20) {sma20:.2f} > SMA(50) {sma50:.2f} > SMA(200) {sma200:.2f}")
        reasons.append(f"MACD {macd_line:+.2f} positive")
        reasons.append(f"RSI {rsi_val:.0f} ≥ 50")
        return "bullish_strong", reasons

    # bearish_strong: stacked down + negative MACD + RSI not firm (<= 50)
    if (
        price < sma20 < sma50 < sma200
        and macd_line < 0
        and rsi_val <= 50.0
    ):
        reasons.append(f"price < SMA(20) {sma20:.2f} < SMA(50) {sma50:.2f} < SMA(200) {sma200:.2f}")
        reasons.append(f"MACD {macd_line:+.2f} negative")
        reasons.append(f"RSI {rsi_val:.0f} ≤ 50")
        return "bearish_strong", reasons

    # bullish_weak: above SMA(50) but ≤ SMA(20), OR RSI < 50 in otherwise constructive tape
    if price > sma50 and (price <= sma20 or rsi_val < 50.0):
        if price > sma200:
            reasons.append(f"price > SMA(200) {sma200:.2f} but < SMA(50) {sma50:.2f}"
                           if price < sma50
                           else f"price > SMA(50) {sma50:.2f} but ≤ SMA(20) {sma20:.2f}")
        else:
            reasons.append(f"price > SMA(50) {sma50:.2f} but < SMA(200) {sma200:.2f}")
        if rsi_val < 50.0:
            reasons.append(f"RSI {rsi_val:.0f} < 50")
        return "bullish_weak", reasons

    # bearish_weak: below SMA(50) but above SMA(200), OR RSI > 50 in down-trending tape
    if price < sma50 and price > sma200:
        reasons.append(f"price < SMA(50) {sma50:.2f} but > SMA(200) {sma200:.2f}")
        if rsi_val > 50.0:
            reasons.append(f"RSI {rsi_val:.0f} > 50 (countertrend bid)")
        return "bearish_weak", reasons
    if price < sma200 and rsi_val > 50.0:
        reasons.append(f"price < SMA(200) {sma200:.2f}")
        reasons.append(f"RSI {rsi_val:.0f} > 50 (countertrend bid)")
        return "bearish_weak", reasons

    # neutral fallback: mixed signals
    reasons.append("mixed signals; no clean stacked-MA + momentum agreement")
    return "neutral", reasons


def classify_momentum(rsi_val: float) -> str:
    if not np.isfinite(rsi_val):
        return "neutral"
    if rsi_val < 30.0:
        return "oversold"
    if rsi_val < 45.0:
        return "weak"
    if rsi_val < 55.0:
        return "neutral"
    if rsi_val < 70.0:
        return "firm"
    return "overbought"


def classify_macd_cross(line: float, signal: float, prev_line: float, prev_signal: float) -> str:
    if not all(np.isfinite(v) for v in (line, signal, prev_line, prev_signal)):
        return "holding_above" if (np.isfinite(line) and np.isfinite(signal) and line >= signal) else "holding_below"
    crossed_up = prev_line <= prev_signal and line > signal
    crossed_down = prev_line >= prev_signal and line < signal
    if crossed_up:
        return "bullish_cross"
    if crossed_down:
        return "bearish_cross"
    return "holding_above" if line >= signal else "holding_below"


def classify_bollinger_position(price: float, upper: float, mid: float, lower: float) -> tuple[str, float]:
    """Label + price's percentile within the band (0 = lower, 1 = upper)."""
    if not all(np.isfinite(v) for v in (price, upper, mid, lower)) or upper <= lower:
        return "mid_range", 0.5
    pct = (price - lower) / (upper - lower)
    if price > upper:
        return "above_upper", float(pct)
    if price < lower:
        return "below_lower", float(pct)
    if pct >= 0.80:
        return "near_upper", float(pct)
    if pct <= 0.20:
        return "near_lower", float(pct)
    return "mid_range", float(pct)


def classify_adv_bucket(adv_dollars: float) -> str:
    if adv_dollars < 1_000_000:
        return "thin"
    if adv_dollars < 50_000_000:
        return "medium"
    if adv_dollars < 500_000_000:
        return "liquid"
    return "mega"


# ----- Adaptive take -----

# Closing-sentence map keyed on (regime, momentum). Falls back to a
# neutral "no edge" read for any pair not in the table.
TAKE_MAP: dict[tuple[str, str], str] = {
    ("bullish_strong", "firm"):       "Trend and momentum aligned; respect the setup.",
    ("bullish_strong", "overbought"): "Trend intact but momentum stretched; mean-revert risk on shorter timeframes.",
    ("bullish_strong", "neutral"):    "Trend strong; momentum cooling but not broken.",
    ("bullish_weak", "weak"):         "Pullback inside an uptrend, not a clean momentum breakout.",
    ("bullish_weak", "neutral"):      "Constructive tape losing thrust; wait for momentum to confirm.",
    ("bullish_weak", "oversold"):     "Pullback in an uptrend hitting oversold; classic dip-buy setup if the higher-timeframe trend holds.",
    ("bullish_weak", "firm"):         "Above the 50-day with firm momentum; trend repairing.",
    ("bearish_strong", "oversold"):   "Trend is down; oversold readings here are noise, not reversal.",
    ("bearish_strong", "weak"):       "Downtrend intact; momentum has room to deteriorate further.",
    ("bearish_strong", "neutral"):    "Downtrend intact; counter-trend bounce attempt without confirmation.",
    ("bearish_weak", "firm"):         "Countertrend bid in a tape still below the 50-day; treat as a bounce, not a reversal.",
    ("bearish_weak", "neutral"):      "Below the 50-day; momentum lifting but trend not repaired.",
    ("bearish_weak", "overbought"):   "Stretched countertrend bounce; reversion risk into resistance.",
    ("neutral", "neutral"):           "No edge from technicals; wait for confirmation.",
    ("neutral", "firm"):              "Momentum building inside a range; watch the upper edge.",
    ("neutral", "weak"):              "Momentum fading inside a range; watch the lower edge.",
    ("neutral", "overbought"):        "Range top with stretched momentum; reversion-friendly.",
    ("neutral", "oversold"):          "Range bottom with stretched momentum; reversion-friendly.",
}


def build_take(
    ticker: str,
    regime: str,
    momentum_read: str,
    rsi_val: float,
    price: float,
    sma20: float,
    sma50: float,
    atr_pct: float,
) -> str:
    """Adaptive take built from the actual readings (not hardcoded)."""
    # Lead with trend regime label in plain English
    regime_words = {
        "bullish_strong": "looks constructive",
        "bullish_weak":   "looks mixed",
        "bearish_strong": "looks weak",
        "bearish_weak":   "looks soft",
        "neutral":        "looks rangebound",
    }
    lead = f"{ticker} {regime_words.get(regime, 'looks mixed')}."

    # Momentum + MA structure sentence
    if np.isfinite(rsi_val) and np.isfinite(sma20) and np.isfinite(sma50):
        if price < sma20 and price > sma50:
            ma_phrase = "price below the 20-day EMA but holding above the 50-day SMA"
        elif price < sma50:
            ma_phrase = "price below the 50-day SMA"
        elif price > sma20:
            ma_phrase = "price above the 20-day EMA"
        else:
            ma_phrase = "price hugging the 20-day EMA"
        momentum_sentence = f"RSI {rsi_val:.0f} {momentum_read}, {ma_phrase}."
    else:
        momentum_sentence = f"Momentum read: {momentum_read}."

    # ATR bucket sentence
    if np.isfinite(atr_pct):
        if atr_pct > 0.05:
            atr_sentence = f"ATR elevated at ~{atr_pct*100:.1f}% of price."
        elif atr_pct >= 0.02:
            atr_sentence = f"ATR normal at ~{atr_pct*100:.1f}% of price."
        else:
            atr_sentence = f"ATR quiet at ~{atr_pct*100:.1f}% of price."
    else:
        atr_sentence = ""

    # Closing sentence from (regime, momentum) map
    closing = TAKE_MAP.get((regime, momentum_read), "Mixed setup; no edge from technicals alone.")

    parts = [lead, momentum_sentence, atr_sentence, closing]
    return " ".join(p for p in parts if p)


# ----- CLI -----

ap = argparse.ArgumentParser(description="technical-briefing reference")
ap.add_argument("--ticker", type=str, required=True, help="US ticker")
ap.add_argument("--lookback-days", type=int, default=252,
                help="Trading days of daily OHLC to use. Default 252.")
ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT. Default: render.")
args = ap.parse_args()
fmt = resolve_output_format(args.format)

ticker = args.ticker.strip().upper()
lookback = max(60, int(args.lookback_days))


# ----- Data pull -----

print(f"Pulling daily aggs for {ticker} (lookback {lookback}d)...", file=sys.stderr)
rows = fetch_daily_aggs(ticker, lookback)
sources: list[dict] = [{
    "endpoint": f"/v2/aggs/ticker/{ticker}/range/1/day/{{from}}/{{to}}",
    "fetched_at": utcnow_iso(),
    "context": f"daily aggs for {ticker}",
}]
if len(rows) < 60:
    print(f"ERROR: only {len(rows)} bars returned for {ticker}; need at least 60", file=sys.stderr)
    sys.exit(1)

# Trim to last `lookback` sessions
if len(rows) > lookback:
    rows = rows[-lookback:]

print(f"Pulling snapshot for {ticker}...", file=sys.stderr)
snap = fetch_snapshot(ticker)
sources.append({
    "endpoint": f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
    "fetched_at": utcnow_iso(),
    "context": f"snapshot for {ticker}",
})


# ----- Compute -----

closes = np.array([r["close"] for r in rows], dtype=float)
highs = np.array([r["high"] for r in rows], dtype=float)
lows = np.array([r["low"] for r in rows], dtype=float)
volumes = np.array([r["volume"] for r in rows], dtype=float)
n_bars = closes.size
current_price = float(closes[-1])
as_of_date = rows[-1]["date"]

sma20 = float(sma(closes, 20)[-1])
sma50 = float(sma(closes, 50)[-1]) if n_bars >= 50 else float("nan")
sma200 = float(sma(closes, 200)[-1]) if n_bars >= 200 else float("nan")
ema20 = float(ema(closes, 20)[-1])
ema50 = float(ema(closes, 50)[-1]) if n_bars >= 50 else float("nan")

rsi_series = rsi(closes, 14)
rsi_latest = float(rsi_series[-1])
rsi_5d_avg_vals = rsi_series[~np.isnan(rsi_series)][-5:]
rsi_5d_avg = float(rsi_5d_avg_vals.mean()) if rsi_5d_avg_vals.size > 0 else float("nan")

macd_line, signal_line, hist = macd(closes, 12, 26, 9)
macd_latest = float(macd_line[-1])
signal_latest = float(signal_line[-1])
hist_latest = float(hist[-1])
macd_prev = float(macd_line[-2]) if n_bars >= 2 else float("nan")
signal_prev = float(signal_line[-2]) if n_bars >= 2 else float("nan")
cross_status = classify_macd_cross(macd_latest, signal_latest, macd_prev, signal_prev)

bb_upper, bb_mid, bb_lower = bollinger(closes, 20, 2.0)
bb_u = float(bb_upper[-1])
bb_m = float(bb_mid[-1])
bb_l = float(bb_lower[-1])
bb_position, bb_pct = classify_bollinger_position(current_price, bb_u, bb_m, bb_l)

atr_series = atr(highs, lows, closes, 14)
atr_latest = float(atr_series[-1])
atr_pct = atr_latest / current_price if current_price > 0 and np.isfinite(atr_latest) else float("nan")

# Liquidity: 30d avg dollar volume
window = min(30, n_bars)
adv_dollars = float((closes[-window:] * volumes[-window:]).mean())
adv_bucket = classify_adv_bucket(adv_dollars)

# Spread from snapshot
spread_bps: float | None = None
spread_source: str | None = None
if snap:
    last_quote = snap.get("lastQuote") or {}
    bid = last_quote.get("p") or last_quote.get("bp") or last_quote.get("bid")
    ask = last_quote.get("P") or last_quote.get("ap") or last_quote.get("ask")
    try:
        if bid and ask and float(bid) > 0 and float(ask) > 0:
            mid = (float(bid) + float(ask)) / 2.0
            spread_bps = round(((float(ask) - float(bid)) / mid) * 10_000.0, 2)
            spread_source = "snapshot.lastQuote"
    except (TypeError, ValueError):
        spread_bps = None


# ----- Classify -----

regime, regime_reasons = classify_trend(
    current_price, sma20, sma50, sma200, macd_latest, rsi_latest,
)
momentum_read = classify_momentum(rsi_latest)


# ----- Payload -----

def safe_round(v: float, n: int = 2) -> float | None:
    return round(float(v), n) if np.isfinite(v) else None


take = build_take(
    ticker=ticker,
    regime=regime,
    momentum_read=momentum_read,
    rsi_val=rsi_latest,
    price=current_price,
    sma20=sma20,
    sma50=sma50,
    atr_pct=atr_pct,
)

tier_caveats: list[str] = [
    "Single-name snapshot; pair with universe-builder or factor-research for market context",
    "ATR-based vol does not anticipate event-driven jumps (earnings, FDA, macro print)",
]
if not np.isfinite(sma200):
    tier_caveats.append(
        f"SMA(200) unavailable: only {n_bars} bars; long-term trend label is approximate"
    )
if spread_bps is None:
    tier_caveats.append("Live spread unavailable from snapshot; liquidity read uses ADV only")

payload = {
    "skill": "technical-briefing",
    "ticker": ticker,
    "as_of": as_of_date,
    "fetched_at": NOW_UTC.isoformat(),
    "lookback_days": int(lookback),
    "n_bars": int(n_bars),
    "current_price": safe_round(current_price, 4),
    "trend": {
        "regime": regime,
        "reasons": regime_reasons,
    },
    "momentum": {
        "rsi_14": safe_round(rsi_latest, 2),
        "rsi_5d_avg": safe_round(rsi_5d_avg, 2),
        "read": momentum_read,
    },
    "moving_averages": {
        "sma_20": safe_round(sma20, 4),
        "sma_50": safe_round(sma50, 4),
        "sma_200": safe_round(sma200, 4),
        "ema_20": safe_round(ema20, 4),
        "ema_50": safe_round(ema50, 4),
        "price_vs_sma_20_pct": safe_round((current_price / sma20 - 1.0) if np.isfinite(sma20) and sma20 > 0 else float("nan"), 4),
        "price_vs_sma_50_pct": safe_round((current_price / sma50 - 1.0) if np.isfinite(sma50) and sma50 > 0 else float("nan"), 4),
        "price_vs_sma_200_pct": safe_round((current_price / sma200 - 1.0) if np.isfinite(sma200) and sma200 > 0 else float("nan"), 4),
    },
    "macd": {
        "line": safe_round(macd_latest, 4),
        "signal": safe_round(signal_latest, 4),
        "histogram": safe_round(hist_latest, 4),
        "cross_status": cross_status,
    },
    "bollinger": {
        "upper": safe_round(bb_u, 4),
        "mid": safe_round(bb_m, 4),
        "lower": safe_round(bb_l, 4),
        "position": bb_position,
        "pct_of_band_width": safe_round(bb_pct, 4),
    },
    "volatility": {
        "atr_14": safe_round(atr_latest, 4),
        "atr_pct_of_price": safe_round(atr_pct, 4),
    },
    "liquidity": {
        "adv_30d_dollars": int(adv_dollars) if np.isfinite(adv_dollars) else 0,
        "adv_bucket": adv_bucket,
        "current_spread_bps": spread_bps,
        "spread_source": spread_source,
    },
    "take": take,
    "tier_caveats": tier_caveats,
    "sources": sources,
}


# ----- Renderer -----

def fmt_money(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"${v:,.2f}"


def fmt_adv(dollars: float) -> str:
    if dollars >= 1_000_000_000:
        return f"${dollars/1_000_000_000:.1f}B"
    if dollars >= 1_000_000:
        return f"${dollars/1_000_000:.0f}M"
    return f"${dollars:,.0f}"


def fmt_pct(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "n/a"
    val = v * 100.0
    if signed:
        sign = "+" if val >= 0 else ""
        return f"{sign}{val:.1f}%"
    return f"{val:.1f}%"


REGIME_LABELS = {
    "bullish_strong": "BULLISH (strong)",
    "bullish_weak":   "BULLISH (weak)",
    "bearish_strong": "BEARISH (strong)",
    "bearish_weak":   "BEARISH (weak)",
    "neutral":        "NEUTRAL",
}

MOMENTUM_LABELS = {
    "oversold":   "OVERSOLD",
    "weak":       "WEAK",
    "neutral":    "NEUTRAL",
    "firm":       "FIRM",
    "overbought": "OVERBOUGHT",
}

BB_POSITION_LABEL = {
    "above_upper": "above upper band",
    "near_upper":  "near upper band",
    "mid_range":   "mid-range",
    "near_lower":  "near lower band",
    "below_lower": "below lower band",
}


def render(p: dict) -> str:
    ma = p["moving_averages"]
    mo = p["momentum"]
    md = p["macd"]
    bb = p["bollinger"]
    vol = p["volatility"]
    liq = p["liquidity"]
    trend = p["trend"]

    lines: list[str] = []
    lines.append(f"{p['ticker']} Technical Briefing — {p['as_of']}")
    liq_part = f"Liquidity: {liq['adv_bucket']} ({fmt_adv(liq['adv_30d_dollars'])} ADV)"
    if liq.get("current_spread_bps") is not None:
        liq_part += f", spread {liq['current_spread_bps']:.1f} bps"
    lines.append(
        f"Price {fmt_money(p['current_price'])} · {p['lookback_days']}-day lookback · {liq_part}"
    )
    lines.append("")

    # Trend regime
    lines.append(f"Trend regime: {REGIME_LABELS.get(trend['regime'], trend['regime'].upper())}")
    sma200_part = (f"Above 200-day SMA ({fmt_money(ma['sma_200'])})"
                   if ma.get("sma_200") is not None and p["current_price"] > ma["sma_200"]
                   else f"Below 200-day SMA ({fmt_money(ma['sma_200'])})"
                   if ma.get("sma_200") is not None
                   else "200-day SMA unavailable")
    sma50_part = (f"above 50-day ({fmt_money(ma['sma_50'])})"
                  if ma.get("sma_50") is not None and p["current_price"] > ma["sma_50"]
                  else f"below 50-day ({fmt_money(ma['sma_50'])})"
                  if ma.get("sma_50") is not None
                  else "")
    sma20_part = (f"above 20-day ({fmt_money(ma['sma_20'])})"
                  if ma.get("sma_20") is not None and p["current_price"] > ma["sma_20"]
                  else f"below 20-day ({fmt_money(ma['sma_20'])})"
                  if ma.get("sma_20") is not None
                  else "")
    parts = [s for s in (sma200_part, sma50_part, sma20_part) if s]
    if parts:
        lines.append(f"  {', '.join(parts)}")
    if trend.get("reasons"):
        lines.append(f"  Read: {trend['reasons'][0]}")
    lines.append("")

    # Momentum
    rsi_val = mo.get("rsi_14")
    rsi_avg = mo.get("rsi_5d_avg")
    lines.append(
        f"Momentum (RSI 14): {rsi_val:.1f} → {MOMENTUM_LABELS.get(mo['read'], mo['read'].upper())}"
        if rsi_val is not None else
        "Momentum (RSI 14): n/a"
    )
    if rsi_avg is not None:
        lines.append(f"  5-day RSI avg: {rsi_avg:.1f}")
    lines.append("")

    # MACD
    if md.get("line") is not None and md.get("signal") is not None:
        cross_txt = md["cross_status"].replace("_", " ")
        lines.append(
            f"MACD (12/26/9): {md['line']:+.2f} line vs {md['signal']:+.2f} signal → {cross_txt}"
        )
        if md.get("histogram") is not None:
            direction = "expanding" if abs(md["histogram"]) > abs(md["line"] - md["signal"]) * 0 else "holding"
            # Simpler: just print histogram value
            lines.append(f"  Histogram {md['histogram']:+.2f}")
    lines.append("")

    # Bollinger
    if bb.get("upper") is not None:
        pct_band = bb.get("pct_of_band_width") or 0.0
        pct_pretty = max(0.0, min(1.0, pct_band)) * 100.0
        lines.append(
            f"Bollinger (20, 2σ): {fmt_money(p['current_price'])} at {pct_pretty:.0f}% of band width"
        )
        lines.append(
            f"  Upper {fmt_money(bb['upper'])} · Mid {fmt_money(bb['mid'])} · Lower {fmt_money(bb['lower'])}"
        )
        lines.append(f"  Read: {BB_POSITION_LABEL.get(bb['position'], bb['position'])}")
    lines.append("")

    # ATR
    if vol.get("atr_14") is not None:
        atr_pct_val = vol.get("atr_pct_of_price") or 0.0
        if atr_pct_val > 0.05:
            atr_word = "Elevated — sizing should reflect"
        elif atr_pct_val >= 0.02:
            atr_word = "Normal"
        else:
            atr_word = "Quiet"
        lines.append(
            f"Volatility (ATR 14): {fmt_money(vol['atr_14'])} ({atr_pct_val*100:.1f}% of price)"
        )
        lines.append(f"  {atr_word}")
    lines.append("")

    # Take
    lines.append(f"Take: {p['take']}")

    # Caveats
    if p.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in p["tier_caveats"]:
            lines.append(f"- {c}")

    return "\n".join(lines)


rendered = render(payload)


# ----- Write output -----

out_name = "technical-briefing-output.md"
out_path = os.path.join(os.path.dirname(__file__), out_name)
with open(out_path, "w") as fout:
    fout.write("# technical-briefing run\n\n")
    fout.write(f"Generated: {NOW_UTC.isoformat()}\n")
    fout.write(f"Ticker: {payload['ticker']}\n")
    fout.write(f"Lookback: {payload['lookback_days']}d  ")
    fout.write(f"Bars: {payload['n_bars']}\n\n")
    fout.write("## Layer 1: canonical JSON (live data)\n\n")
    fout.write("```json\n")
    fout.write(json.dumps(payload, indent=2, default=str))
    fout.write("\n```\n\n")
    fout.write("## Layer 2: rendered briefing (live data)\n\n")
    fout.write("```\n")
    fout.write(rendered)
    fout.write("\n```\n")

print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
emit_to_stdout(rendered, payload, fmt)
