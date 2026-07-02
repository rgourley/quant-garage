"""
stock-one-pager: beginner-friendly single-name snapshot.

Composes technical_briefing + earnings_blackout + market_regime into a
plain-language snapshot card. The thing a retail trader or a media
reader wants before buying something they saw on social: what does the
chart say, when's the next thing that could move it, how does the
overall market feel, and what could go wrong.

This is a RETAIL-TIER skill. The design bar: a first-time trader
understands the output without a glossary, and it never implies
certainty the data doesn't support.

    from quant_garage.skills.stock_one_pager import run, render
    payload = run("NVDA")
    print(render(payload))

Design note: this is a *composed* skill. It calls run() on each
underlying skill and reads their JSON. Because each is now
library-importable, no shelling out, no file I/O, no re-fetches beyond
what the underlying skills already cache.
"""
from __future__ import annotations

from datetime import date

from .. import MassiveClient, today, utcnow_iso
from . import technical_briefing, earnings_blackout, market_regime


# ----- Plain-language translation helpers -----

TREND_PLAIN = {
    "bullish_strong": "trending up strongly",
    "bullish_weak":   "in a fragile uptrend",
    "bearish_strong": "trending down strongly",
    "bearish_weak":   "in a soft downtrend",
    "neutral":        "range-bound (no clear trend)",
}

MOMENTUM_PLAIN = {
    "oversold":   "beaten up — momentum is stretched to the downside",
    "weak":       "losing steam",
    "neutral":    "in-between (no strong momentum signal)",
    "firm":       "showing solid buying interest",
    "overbought": "stretched to the upside — could pull back",
}

REGIME_PLAIN = {
    "risk_on":         "The overall market is risk-on: uptrend with broad participation.",
    "mixed_risk_on":   "The overall market is leaning risk-on but confirmation is incomplete.",
    "risk_off":        "The overall market is risk-off: downtrend with defensive leadership.",
    "mixed_risk_off":  "The overall market is leaning risk-off but starting to show cracks in the bearish read.",
    "neutral":         "The overall market has no clear direction.",
}

ADV_PLAIN = {
    "thin":   "thinly traded — hard to buy or sell in size without moving the price",
    "medium": "moderately liquid — fine for retail size",
    "liquid": "liquid — easy to trade at retail size",
    "mega":   "extremely liquid — one of the most-traded names",
}


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "n/a"
    if abs(v) < 10:
        return f"${v:,.2f}"
    return f"${v:,.2f}"


def _range_position(price: float, low: float, high: float) -> str:
    """Very rough 52-week-ish read using the 200-day SMA + Bollinger as anchors."""
    if high <= low or price is None:
        return "range read unavailable"
    pct = (price - low) / (high - low)
    if pct >= 0.85:
        return "near the top of its recent range"
    if pct >= 0.65:
        return "in the upper half of its recent range"
    if pct >= 0.35:
        return "mid-range"
    if pct >= 0.15:
        return "in the lower half of its recent range"
    return "near the bottom of its recent range"


def _next_catalyst(eb_result: dict, today_iso: str) -> dict:
    """Extract the single most important upcoming catalyst from earnings-blackout."""
    r = eb_result
    status = r["status"]
    if status in ("blackout_imminent", "blackout_soon", "blackout_extended") and r.get("next_earnings_date"):
        days = r["days_until"]
        return {
            "type": "earnings",
            "date": r["next_earnings_date"],
            "days_until": days,
            "release_time": r.get("expected_release_time"),
            "note": f"Earnings in {days} day{'s' if days != 1 else ''}.",
        }
    if status in ("just_printed", "recent_print") and r.get("most_recent_earnings_date"):
        days = abs(r["days_until"]) if r["days_until"] is not None else None
        return {
            "type": "earnings_recent",
            "date": r["most_recent_earnings_date"],
            "days_since": days,
            "release_time": r.get("expected_release_time"),
            "note": f"Just reported earnings {days} day{'s' if days != 1 else ''} ago.",
        }
    return {"type": "none", "note": "No earnings inside the next 30 days."}


def _risks(tb: dict, eb_result: dict, mr: dict) -> list[str]:
    """The 'what could go wrong' list. Reads the actual data — no boilerplate."""
    risks: list[str] = []

    # Trend risk
    regime = tb["trend"]["regime"]
    if regime.startswith("bearish"):
        risks.append(
            "The chart is in a downtrend. Buying a downtrending name means you're "
            "betting on a reversal that hasn't shown up yet."
        )
    if regime == "neutral":
        risks.append(
            "The chart has no clear direction. Range-bound names can chop for months "
            "before a new trend."
        )

    # Volatility risk
    atr_pct = tb["volatility"].get("atr_pct_of_price") or 0.0
    if atr_pct > 0.05:
        risks.append(
            f"High day-to-day volatility (~{atr_pct*100:.1f}% ATR). Expect ±"
            f"{atr_pct*100:.0f}% swings on average days, more on news days."
        )

    # Momentum risk
    rsi_val = tb["momentum"].get("rsi_14")
    if rsi_val is not None and rsi_val > 70:
        risks.append(
            f"Momentum is overbought (RSI {rsi_val:.0f}). Even in a strong uptrend, "
            "overbought names often pull back before continuing."
        )
    if rsi_val is not None and rsi_val < 30:
        risks.append(
            f"Momentum is oversold (RSI {rsi_val:.0f}). Oversold names can stay oversold "
            "for weeks in a downtrend."
        )

    # Liquidity risk
    adv_bucket = tb["liquidity"].get("adv_bucket")
    spread_bps = tb["liquidity"].get("current_spread_bps")
    if adv_bucket == "thin":
        risks.append(
            "Thinly traded. Bid-ask spread will eat into small trades. Consider "
            "limit orders, not market orders."
        )
    if spread_bps is not None and spread_bps > 50:
        risks.append(
            f"Wide spread right now ({spread_bps:.0f} basis points, i.e. ~"
            f"{spread_bps/100:.1f}% cost round-trip). Trade with limits."
        )

    # Earnings risk
    if eb_result["status"] in ("blackout_imminent", "blackout_soon"):
        days = eb_result["days_until"]
        risks.append(
            f"Earnings in {days} day{'s' if days != 1 else ''}. Stocks can move ±10% "
            "or more on earnings, in either direction. Don't buy for the print unless "
            "you're comfortable with binary outcomes."
        )

    # Market context risk
    regime_label = mr["composite_regime"]["label"]
    if regime_label in ("risk_off", "mixed_risk_off"):
        risks.append(
            "The overall market is in a defensive posture. Even good individual names "
            "get sold in risk-off tapes."
        )

    if not risks:
        risks.append(
            "No screaming red flags in the standard checks — but nothing in the market "
            "is risk-free. Position size accordingly."
        )
    return risks


def _plain_trend_read(tb: dict) -> str:
    regime = tb["trend"]["regime"]
    momentum = tb["momentum"]["read"]
    trend = TREND_PLAIN.get(regime, regime)
    mo = MOMENTUM_PLAIN.get(momentum, momentum)
    return f"{tb['ticker']} is {trend}, and momentum is {mo}."


def _key_levels(tb: dict) -> dict:
    """Support/resistance in plain English, sourced from MAs + Bollinger."""
    price = tb["current_price"]
    ma = tb["moving_averages"]
    bb = tb["bollinger"]

    levels: list[dict] = []
    for name, value, why in [
        ("50-day average", ma.get("sma_50"), "watched by short-term traders"),
        ("200-day average", ma.get("sma_200"), "the classic long-term trend line"),
        ("upper Bollinger band", bb.get("upper"), "recent high-side band"),
        ("lower Bollinger band", bb.get("lower"), "recent low-side band"),
    ]:
        if value is None:
            continue
        role = "support" if value < price else "resistance" if value > price else "at price"
        levels.append({
            "name": name,
            "price": value,
            "role": role,
            "distance_pct": round((value / price - 1.0) * 100, 2) if price else None,
            "why_it_matters": why,
        })
    return {"current_price": price, "levels": levels}


# ----- Public API -----

def run(
    ticker: str,
    include_market_context: bool = True,
    client: MassiveClient | None = None,
) -> dict:
    """Build a beginner-friendly single-name snapshot.

    Composes technical_briefing + earnings_blackout + optionally market_regime.
    Set include_market_context=False when running this in a tight loop over
    many tickers (skip the 12-ETF market_regime pull per call).
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    client = client or MassiveClient()
    today_iso = today().isoformat()

    tb = technical_briefing.run(ticker, client=client)
    eb = earnings_blackout.run([ticker], window_days=30, include_past_days=30, client=client)
    eb_result = eb["results"][0]
    mr = market_regime.run(client=client) if include_market_context else None

    price = tb["current_price"]
    bb = tb["bollinger"]
    range_pos = _range_position(price, bb.get("lower") or price, bb.get("upper") or price)

    catalyst = _next_catalyst(eb_result, today_iso)
    plain_trend = _plain_trend_read(tb)
    key_levels = _key_levels(tb)
    liquidity_plain = ADV_PLAIN.get(tb["liquidity"].get("adv_bucket"), "liquidity read unavailable")
    market_context = REGIME_PLAIN.get(mr["composite_regime"]["label"]) if mr else None
    risks = _risks(tb, eb_result, mr) if mr else _risks(tb, eb_result, {"composite_regime": {"label": "neutral"}})

    tier_caveats: list[str] = []
    if not mr:
        tier_caveats.append("Market context skipped (include_market_context=False).")
    if bb.get("upper") is None or bb.get("lower") is None:
        tier_caveats.append("Recent range read is approximate (Bollinger band data missing).")

    return {
        "skill": "stock-one-pager",
        "ticker": ticker,
        "as_of": tb["as_of"],
        "fetched_at": utcnow_iso(),
        "current_price": price,
        "trend_plain": plain_trend,
        "recent_range_position": range_pos,
        "liquidity_plain": liquidity_plain,
        "next_catalyst": catalyst,
        "key_levels": key_levels,
        "market_context_plain": market_context,
        "what_could_go_wrong": risks,
        "tier_caveats": tier_caveats,
        "components": {
            "technical_briefing": {
                "regime": tb["trend"]["regime"],
                "momentum": tb["momentum"]["read"],
                "atr_pct_of_price": tb["volatility"].get("atr_pct_of_price"),
                "adv_bucket": tb["liquidity"].get("adv_bucket"),
            },
            "earnings_blackout": {
                "status": eb_result["status"],
                "next_earnings_date": eb_result.get("next_earnings_date"),
                "most_recent_earnings_date": eb_result.get("most_recent_earnings_date"),
            },
            "market_regime": {
                "label": mr["composite_regime"]["label"] if mr else None,
                "spy_trend": mr["spy_trend"]["trend"] if mr and mr.get("spy_trend") else None,
            } if include_market_context else None,
        },
        "sources": tb["sources"] + eb["sources"] + (mr["sources"] if mr else []),
    }


# ----- Renderer -----

def render(p: dict) -> str:
    lines: list[str] = []
    lines.append(f"{p['ticker']} — plain-language snapshot")
    lines.append(f"As of {p['as_of']} · Price {_fmt_money(p['current_price'])}")
    lines.append("")

    # Trend
    lines.append("What the chart says:")
    lines.append(f"  {p['trend_plain']}")
    lines.append(f"  It's {p['recent_range_position']}.")
    lines.append(f"  Liquidity: {p['liquidity_plain']}.")
    lines.append("")

    # Next catalyst
    cat = p["next_catalyst"]
    lines.append("Next thing that could move the stock:")
    if cat["type"] == "earnings":
        rt = f" ({cat['release_time']})" if cat.get("release_time") and cat["release_time"] != "unknown" else ""
        lines.append(f"  Earnings on {cat['date']}{rt} — {cat['note']}")
    elif cat["type"] == "earnings_recent":
        lines.append(f"  Just reported on {cat['date']} — {cat['note']}")
        lines.append("  Watch for post-earnings drift over the next 1-2 weeks.")
    else:
        lines.append(f"  {cat['note']}")
    lines.append("")

    # Key levels
    kl = p["key_levels"]
    if kl["levels"]:
        lines.append("Key price levels:")
        for lvl in kl["levels"]:
            dist = f" ({lvl['distance_pct']:+.1f}% from here)" if lvl.get("distance_pct") is not None else ""
            lines.append(f"  {lvl['role'].title():<10} {_fmt_money(lvl['price'])} — {lvl['name']}{dist}")
        lines.append("  (levels are reference points, not guarantees)")
        lines.append("")

    # Market context
    if p.get("market_context_plain"):
        lines.append("Market context:")
        lines.append(f"  {p['market_context_plain']}")
        lines.append("")

    # What could go wrong
    lines.append("What could go wrong:")
    for r in p["what_could_go_wrong"]:
        lines.append(f"  • {r}")
    lines.append("")

    # Caveats
    lines.append("This is not investment advice. Data has lags and gaps.")
    if p.get("tier_caveats"):
        for c in p["tier_caveats"]:
            lines.append(f"  Note: {c}")

    return "\n".join(lines).rstrip()
