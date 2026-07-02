"""
technical-briefing as an importable library function.

Callers can do:

    from quant_garage.skills.technical_briefing import run, render
    payload = run("NVDA", lookback_days=252)
    print(render(payload))

or compose it into another skill (stock-one-pager, scan-timerange) without
shelling out to a script.

The CLI wrapper lives at examples/run-technical-briefing.py.

Payload shape matches skills/technical-briefing/output-schema.json.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np

from .. import (
    MassiveClient,
    today,
    utcnow_iso,
    sma,
    ema,
    rsi,
    macd,
    bollinger,
    atr,
)


# ----- HTTP -----

def _fetch_daily_aggs(client: MassiveClient, ticker: str, lookback_days: int) -> list[dict]:
    end = today()
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


def _fetch_snapshot(client: MassiveClient, ticker: str) -> dict | None:
    try:
        doc, _ = client.get(
            f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
        )
        return (doc.get("ticker") or doc) if doc else None
    except Exception:
        return None


# ----- Classifiers -----

def _classify_trend(
    price: float, sma20: float, sma50: float, sma200: float,
    macd_val: float, rsi_val: float,
) -> tuple[str, list[str]]:
    has200 = np.isfinite(sma200)
    has50 = np.isfinite(sma50)
    reasons: list[str] = []

    if has200 and has50 and price > sma20 > sma50 > sma200:
        reasons.append("stacked MAs bullish (price > 20 > 50 > 200)")
        if macd_val > 0:
            reasons.append("MACD positive")
        if rsi_val > 55:
            reasons.append(f"RSI firm ({rsi_val:.0f})")
        return "bullish_strong", reasons

    if has200 and has50 and price < sma20 < sma50 < sma200:
        reasons.append("stacked MAs bearish (price < 20 < 50 < 200)")
        if macd_val < 0:
            reasons.append("MACD negative")
        if rsi_val < 45:
            reasons.append(f"RSI weak ({rsi_val:.0f})")
        return "bearish_strong", reasons

    if has200 and price > sma200 and has50 and price > sma50:
        reasons.append(f"price > SMA(50) {sma50:.2f} > SMA(200) {sma200:.2f}")
        return "bullish_weak", reasons

    if has200 and price < sma200 and has50 and price < sma50:
        reasons.append(f"price < SMA(50) {sma50:.2f} < SMA(200) {sma200:.2f}")
        return "bearish_weak", reasons

    if has50 and has200:
        if price < sma50 and price > sma200:
            reasons.append(f"price < SMA(50) {sma50:.2f} but > SMA(200) {sma200:.2f}")
            return "bearish_weak", reasons
        if price > sma50 and price < sma200:
            reasons.append(f"price > SMA(50) {sma50:.2f} but < SMA(200) {sma200:.2f}")
            return "bullish_weak", reasons

    reasons.append("MAs mixed, no clear regime")
    return "neutral", reasons


def _classify_momentum(rsi_val: float) -> str:
    if not np.isfinite(rsi_val):
        return "neutral"
    if rsi_val < 30: return "oversold"
    if rsi_val < 45: return "weak"
    if rsi_val < 55: return "neutral"
    if rsi_val < 70: return "firm"
    return "overbought"


def _classify_macd_cross(line: float, signal: float, prev_line: float, prev_signal: float) -> str:
    if not (np.isfinite(line) and np.isfinite(signal)):
        return "unavailable"
    if np.isfinite(prev_line) and np.isfinite(prev_signal):
        if prev_line <= prev_signal and line > signal:
            return "bullish_cross"
        if prev_line >= prev_signal and line < signal:
            return "bearish_cross"
    return "holding_above" if line > signal else "holding_below"


def _classify_bollinger_position(price: float, upper: float, mid: float, lower: float) -> tuple[str, float]:
    band_width = upper - lower
    if band_width <= 0 or not np.isfinite(band_width):
        return "mid_range", 0.5
    pct = (price - lower) / band_width
    if price > upper: return "above_upper", pct
    if pct > 0.8: return "near_upper", pct
    if pct < 0.2: return "near_lower", pct
    if price < lower: return "below_lower", pct
    return "mid_range", pct


def _classify_adv_bucket(adv_dollars: float) -> str:
    if adv_dollars >= 500_000_000: return "mega"
    if adv_dollars >= 50_000_000: return "liquid"
    if adv_dollars >= 1_000_000: return "medium"
    return "thin"


def _build_take(
    ticker: str, regime: str, momentum_read: str, rsi_val: float,
    price: float, sma20: float, sma50: float, atr_pct: float,
) -> str:
    tone = {
        ("bullish_strong", "firm"):      f"{ticker} is trending. Stacked MAs, RSI firm at {rsi_val:.0f}, MACD positive.",
        ("bullish_strong", "overbought"): f"{ticker} is trending but overbought — RSI {rsi_val:.0f} argues for a pullback before adding.",
        ("bullish_weak", "firm"):        f"{ticker} above 200-day but not yet stacked. RSI {rsi_val:.0f} constructive; trend recovering.",
        ("bullish_weak", "neutral"):     f"{ticker} above 200-day, mixed shorter MAs. RSI neutral. Constructive but unconfirmed.",
        ("bearish_strong", "weak"):      f"{ticker} in a clean downtrend. Stacked MAs bearish, RSI {rsi_val:.0f}. Don't fade.",
        ("bearish_strong", "oversold"):  f"{ticker} downtrending and oversold (RSI {rsi_val:.0f}) — reflex bounces come to sell.",
        ("bearish_weak", "weak"):        f"{ticker} looks soft. RSI {rsi_val:.0f} weak, price below the 50-day SMA.",
        ("bearish_weak", "neutral"):     f"{ticker} looks soft. RSI {rsi_val:.0f} neutral, price below the 50-day SMA.",
        ("neutral", "neutral"):          f"{ticker} is range-bound. No trend signal, RSI at {rsi_val:.0f}.",
    }
    lead = tone.get((regime, momentum_read), f"{ticker} — {regime.replace('_',' ')}, momentum {momentum_read}.")

    parts = [lead]
    if np.isfinite(atr_pct) and atr_pct > 0.05:
        parts.append(f"ATR elevated at ~{atr_pct*100:.1f}% of price.")

    if np.isfinite(sma50):
        if regime.startswith("bullish") and price < sma50:
            parts.append("Below the 50-day; the setup wants a reclaim to confirm.")
        elif regime.startswith("bearish") and price > sma50:
            parts.append("Above the 50-day; momentum lifting but trend not repaired.")

    return " ".join(parts)


def _safe_round(v: float, n: int = 2) -> float | None:
    return round(float(v), n) if np.isfinite(v) else None


def _spread_from_snapshot(snap: dict | None) -> tuple[float | None, str | None]:
    if not snap:
        return None, None
    last_quote = snap.get("lastQuote") or {}
    bid = last_quote.get("p") or last_quote.get("bp") or last_quote.get("bid")
    ask = last_quote.get("P") or last_quote.get("ap") or last_quote.get("ask")
    try:
        if bid and ask and float(bid) > 0 and float(ask) > 0:
            mid = (float(bid) + float(ask)) / 2.0
            return round(((float(ask) - float(bid)) / mid) * 10_000.0, 2), "snapshot.lastQuote"
    except (TypeError, ValueError):
        pass
    return None, None


# ----- Public API -----

def run(
    ticker: str,
    lookback_days: int = 252,
    client: MassiveClient | None = None,
) -> dict:
    """Compute the technical-briefing payload for one US ticker.

    Returns a dict matching skills/technical-briefing/output-schema.json.
    Raises RuntimeError if fewer than 60 bars are available.
    """
    ticker = ticker.strip().upper()
    lookback = max(60, int(lookback_days))
    client = client or MassiveClient()
    now_iso = datetime.now(timezone.utc).isoformat()

    rows = _fetch_daily_aggs(client, ticker, lookback)
    sources: list[dict] = [{
        "endpoint": f"/v2/aggs/ticker/{ticker}/range/1/day/{{from}}/{{to}}",
        "fetched_at": utcnow_iso(),
        "context": f"daily aggs for {ticker}",
    }]
    if len(rows) < 60:
        raise RuntimeError(
            f"only {len(rows)} bars returned for {ticker}; need at least 60"
        )
    if len(rows) > lookback:
        rows = rows[-lookback:]

    snap = _fetch_snapshot(client, ticker)
    sources.append({
        "endpoint": f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
        "fetched_at": utcnow_iso(),
        "context": f"snapshot for {ticker}",
    })

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
    cross_status = _classify_macd_cross(macd_latest, signal_latest, macd_prev, signal_prev)

    bb_upper, bb_mid, bb_lower = bollinger(closes, 20, 2.0)
    bb_u = float(bb_upper[-1])
    bb_m = float(bb_mid[-1])
    bb_l = float(bb_lower[-1])
    bb_position, bb_pct = _classify_bollinger_position(current_price, bb_u, bb_m, bb_l)

    atr_series = atr(highs, lows, closes, 14)
    atr_latest = float(atr_series[-1])
    atr_pct = atr_latest / current_price if current_price > 0 and np.isfinite(atr_latest) else float("nan")

    window = min(30, n_bars)
    adv_dollars = float((closes[-window:] * volumes[-window:]).mean())
    adv_bucket = _classify_adv_bucket(adv_dollars)

    spread_bps, spread_source = _spread_from_snapshot(snap)

    regime, regime_reasons = _classify_trend(
        current_price, sma20, sma50, sma200, macd_latest, rsi_latest,
    )
    momentum_read = _classify_momentum(rsi_latest)

    take = _build_take(
        ticker=ticker, regime=regime, momentum_read=momentum_read,
        rsi_val=rsi_latest, price=current_price,
        sma20=sma20, sma50=sma50, atr_pct=atr_pct,
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

    return {
        "skill": "technical-briefing",
        "ticker": ticker,
        "as_of": as_of_date,
        "fetched_at": now_iso,
        "lookback_days": int(lookback),
        "n_bars": int(n_bars),
        "current_price": _safe_round(current_price, 4),
        "trend": {
            "regime": regime,
            "reasons": regime_reasons,
        },
        "momentum": {
            "rsi_14": _safe_round(rsi_latest, 2),
            "rsi_5d_avg": _safe_round(rsi_5d_avg, 2),
            "read": momentum_read,
        },
        "moving_averages": {
            "sma_20": _safe_round(sma20, 4),
            "sma_50": _safe_round(sma50, 4),
            "sma_200": _safe_round(sma200, 4),
            "ema_20": _safe_round(ema20, 4),
            "ema_50": _safe_round(ema50, 4),
            "price_vs_sma_20_pct": _safe_round((current_price / sma20 - 1.0) if np.isfinite(sma20) and sma20 > 0 else float("nan"), 4),
            "price_vs_sma_50_pct": _safe_round((current_price / sma50 - 1.0) if np.isfinite(sma50) and sma50 > 0 else float("nan"), 4),
            "price_vs_sma_200_pct": _safe_round((current_price / sma200 - 1.0) if np.isfinite(sma200) and sma200 > 0 else float("nan"), 4),
        },
        "macd": {
            "line": _safe_round(macd_latest, 4),
            "signal": _safe_round(signal_latest, 4),
            "histogram": _safe_round(hist_latest, 4),
            "cross_status": cross_status,
        },
        "bollinger": {
            "upper": _safe_round(bb_u, 4),
            "mid": _safe_round(bb_m, 4),
            "lower": _safe_round(bb_l, 4),
            "position": bb_position,
            "pct_of_band_width": _safe_round(bb_pct, 4),
        },
        "volatility": {
            "atr_14": _safe_round(atr_latest, 4),
            "atr_pct_of_price": _safe_round(atr_pct, 4),
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

def _fmt_money(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"${v:,.2f}"


def _fmt_adv(dollars: float) -> str:
    if dollars >= 1_000_000_000:
        return f"${dollars/1_000_000_000:.1f}B"
    if dollars >= 1_000_000:
        return f"${dollars/1_000_000:.0f}M"
    return f"${dollars:,.0f}"


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
    liq_part = f"Liquidity: {liq['adv_bucket']} ({_fmt_adv(liq['adv_30d_dollars'])} ADV)"
    if liq.get("current_spread_bps") is not None:
        liq_part += f", spread {liq['current_spread_bps']:.1f} bps"
    lines.append(
        f"Price {_fmt_money(p['current_price'])} · {p['lookback_days']}-day lookback · {liq_part}"
    )
    lines.append("")

    lines.append(f"Trend regime: {REGIME_LABELS.get(trend['regime'], trend['regime'].upper())}")
    sma200_part = (f"Above 200-day SMA ({_fmt_money(ma['sma_200'])})"
                   if ma.get("sma_200") is not None and p["current_price"] > ma["sma_200"]
                   else f"Below 200-day SMA ({_fmt_money(ma['sma_200'])})"
                   if ma.get("sma_200") is not None
                   else "200-day SMA unavailable")
    sma50_part = (f"above 50-day ({_fmt_money(ma['sma_50'])})"
                  if ma.get("sma_50") is not None and p["current_price"] > ma["sma_50"]
                  else f"below 50-day ({_fmt_money(ma['sma_50'])})"
                  if ma.get("sma_50") is not None
                  else "")
    sma20_part = (f"above 20-day ({_fmt_money(ma['sma_20'])})"
                  if ma.get("sma_20") is not None and p["current_price"] > ma["sma_20"]
                  else f"below 20-day ({_fmt_money(ma['sma_20'])})"
                  if ma.get("sma_20") is not None
                  else "")
    parts = [s for s in (sma200_part, sma50_part, sma20_part) if s]
    if parts:
        lines.append(f"  {', '.join(parts)}")
    if trend.get("reasons"):
        lines.append(f"  Read: {trend['reasons'][0]}")
    lines.append("")

    rsi_val = mo.get("rsi_14")
    rsi_avg = mo.get("rsi_5d_avg")
    if rsi_val is not None:
        lines.append(f"Momentum (RSI 14): {rsi_val:.1f} → {MOMENTUM_LABELS.get(mo['read'], mo['read'].upper())}")
    else:
        lines.append("Momentum (RSI 14): n/a")
    if rsi_avg is not None:
        lines.append(f"  5-day RSI avg: {rsi_avg:.1f}")
    lines.append("")

    if md.get("line") is not None and md.get("signal") is not None:
        cross_txt = md["cross_status"].replace("_", " ")
        lines.append(f"MACD (12/26/9): {md['line']:+.2f} line vs {md['signal']:+.2f} signal → {cross_txt}")
        if md.get("histogram") is not None:
            lines.append(f"  Histogram {md['histogram']:+.2f}")
    lines.append("")

    if bb.get("upper") is not None:
        pct_band = bb.get("pct_of_band_width") or 0.0
        pct_pretty = max(0.0, min(1.0, pct_band)) * 100.0
        lines.append(f"Bollinger (20, 2σ): {_fmt_money(p['current_price'])} at {pct_pretty:.0f}% of band width")
        lines.append(f"  Upper {_fmt_money(bb['upper'])} · Mid {_fmt_money(bb['mid'])} · Lower {_fmt_money(bb['lower'])}")
        lines.append(f"  Read: {BB_POSITION_LABEL.get(bb['position'], bb['position'])}")
    lines.append("")

    if vol.get("atr_14") is not None:
        atr_pct_val = vol.get("atr_pct_of_price") or 0.0
        if atr_pct_val > 0.05:
            atr_word = "Elevated — sizing should reflect"
        elif atr_pct_val >= 0.02:
            atr_word = "Normal"
        else:
            atr_word = "Quiet"
        lines.append(f"Volatility (ATR 14): {_fmt_money(vol['atr_14'])} ({atr_pct_val*100:.1f}% of price)")
        lines.append(f"  {atr_word}")
    lines.append("")

    lines.append(f"Take: {p['take']}")

    if p.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in p["tier_caveats"]:
            lines.append(f"- {c}")

    return "\n".join(lines)
