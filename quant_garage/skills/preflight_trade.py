"""
preflight-trade: before-you-execute sanity check on a single name.

Chains technical-briefing + earnings-blackout (14d) + news-scanner
(last N) + corporate-actions-scanner (90d). Answers "is now a bad
time to buy/sell/reduce this name?" with a verdict + red/green flag
list.

    from quant_garage.skills.preflight_trade import run, render
    payload = run(ticker="ALLO", action="add")
"""
from __future__ import annotations

import sys

from .. import MassiveClient, today, utcnow_iso
from . import (
    technical_briefing,
    earnings_blackout,
    news_scanner,
    corporate_actions_scanner,
)


VALID_ACTIONS = ("buy", "sell", "add", "reduce", "exit")


def run(
    ticker: str,
    action: str = "buy",
    news_last_n: int = 5,
    earnings_window_days: int = 14,
    corp_lookback_days: int = 90,
    client: MassiveClient | None = None,
) -> dict:
    """Preflight check on a specific ticker + intended action."""
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    if action not in VALID_ACTIONS:
        raise ValueError(f"action must be one of {VALID_ACTIONS}, got {action!r}")

    client = client or MassiveClient()
    sections: dict = {}
    errors: list[dict] = []

    def _try(name, fn):
        try:
            sections[name] = fn()
        except Exception as exc:
            errors.append({"section": name, "error": str(exc)})

    print(f"[1/4] technical-briefing {ticker}...", file=sys.stderr)
    _try("technical_briefing", lambda: technical_briefing.run(
        ticker, client=client,
    ))

    print(f"[2/4] earnings-blackout {ticker}...", file=sys.stderr)
    _try("earnings_blackout", lambda: earnings_blackout.run(
        watchlist=[ticker], window_days=earnings_window_days,
        include_past_days=3, client=client,
    ))

    print(f"[3/4] news-scanner {ticker} (last-{news_last_n})...",
          file=sys.stderr)
    _try("news_scanner", lambda: news_scanner.run(
        watchlist=[ticker], last_n=news_last_n, top_n=news_last_n,
        client=client,
    ))

    print(f"[4/4] corporate-actions-scanner {ticker} ({corp_lookback_days}d)...",
          file=sys.stderr)
    _try("corporate_actions", lambda: corporate_actions_scanner.run(
        watchlist=[ticker], lookback_days=corp_lookback_days, client=client,
    ))

    verdict, greens, reds = _build_verdict(sections, action)

    return {
        "scan_params": {
            "ticker": ticker,
            "action": action,
            "as_of": today().isoformat(),
        },
        "verdict": verdict,
        "green_flags": greens,
        "red_flags": reds,
        "sections": sections,
        "errors": errors,
        "generated_at": utcnow_iso(),
    }


def _build_verdict(sections: dict, action: str) -> tuple[str, list[str], list[str]]:
    """Roll up the sub-skills into a verdict + red/green flag lists."""
    greens: list[str] = []
    reds: list[str] = []

    tb = sections.get("technical_briefing")
    if tb:
        trend = (tb.get("trend") or {}).get("regime", "")
        rsi = (tb.get("momentum") or {}).get("rsi_14")
        atr_pct = (tb.get("volatility") or {}).get("atr_pct_of_price")
        if trend in ("bullish_strong", "bullish_weak"):
            greens.append(f"Trend regime: {trend}")
        elif trend in ("bearish_strong", "bearish_weak"):
            reds.append(f"Trend regime: {trend}")
        if rsi is not None:
            if rsi >= 70:
                reds.append(f"RSI {rsi:.1f} — overbought")
            elif rsi <= 30:
                greens.append(f"RSI {rsi:.1f} — oversold (mean-reversion candidate)")
        if atr_pct and atr_pct > 0.05:
            reds.append(f"ATR {atr_pct*100:.1f}% of price — elevated vol")

    eb = sections.get("earnings_blackout")
    if eb:
        r = ((eb.get("results") or [{}])[0])
        status = r.get("status", "")
        days = r.get("days_until")
        if status.startswith("blackout") and days is not None:
            reds.append(f"Earnings in {days} days — event risk")
        elif status == "just_printed":
            reds.append("Just printed — reaction still unfolding")

    ca = sections.get("corporate_actions")
    if ca:
        events = ca.get("events") or []
        recent_material = [
            e for e in events
            if e.get("abnormal_t5_pct") is not None
            and abs(e["abnormal_t5_pct"]) >= 5
        ]
        if recent_material:
            top = recent_material[0]
            flavor = top.get("flavor") or "material"
            reds.append(
                f"Recent 8-K ({top['filing_date']}, {flavor}, "
                f"T+5 abn {top['abnormal_t5_pct']:+.1f}%)"
            )

    ns = sections.get("news_scanner")
    if ns:
        events = ns.get("events") or []
        neg = [e for e in events if (e.get("sentiment_score") or 0) < -0.3]
        pos = [e for e in events if (e.get("sentiment_score") or 0) > 0.3]
        if len(neg) >= 2:
            reds.append(f"{len(neg)} negative-sentiment news items in recent window")
        if len(pos) >= 2:
            greens.append(f"{len(pos)} positive-sentiment news items in recent window")

    # Verdict: red count vs green count
    if len(reds) >= 3 and len(greens) <= 1:
        verdict = "wait"
    elif len(reds) >= 2 and action in ("buy", "add"):
        verdict = "wait"
    elif len(greens) >= 2 and len(reds) <= 1:
        verdict = "go"
    else:
        verdict = "review"

    return verdict, greens, reds


def render(payload: dict) -> str:
    params = payload["scan_params"]
    verdict = payload["verdict"]
    greens = payload["green_flags"]
    reds = payload["red_flags"]
    sections = payload["sections"]
    lines: list[str] = []

    lines.append(
        f"Preflight: {params['ticker']} · Action: {params['action'].upper()}"
    )
    lines.append(f"As of {params['as_of']}")
    lines.append("")

    verdict_display = {
        "go": "GO — no material red flags",
        "wait": "WAIT — multiple red flags, consider deferring",
        "review": "REVIEW — mixed signals, human judgment required",
    }
    lines.append(f"VERDICT: {verdict_display.get(verdict, verdict.upper())}")
    lines.append("")

    if reds:
        lines.append("Red flags:")
        for r in reds:
            lines.append(f"  - {r}")
        lines.append("")
    if greens:
        lines.append("Green flags:")
        for g in greens:
            lines.append(f"  + {g}")
        lines.append("")

    render_map = [
        ("technical_briefing", "TECHNICAL", technical_briefing.render),
        ("earnings_blackout", "EARNINGS BLACKOUT", earnings_blackout.render),
        ("corporate_actions", "CORPORATE ACTIONS (90d)",
         corporate_actions_scanner.render),
        ("news_scanner", "RECENT NEWS", news_scanner.render),
    ]
    for key, title, render_fn in render_map:
        sub = sections.get(key)
        if sub is None:
            continue
        lines.append(title)
        lines.append("═" * 60)
        try:
            lines.append(render_fn(sub))
        except Exception as exc:
            lines.append(f"(render error: {exc})")
        lines.append("")

    errors = payload.get("errors") or []
    if errors:
        lines.append("ERRORS")
        lines.append("─" * 60)
        for e in errors:
            lines.append(f"  {e['section']}: {e['error']}")

    return "\n".join(lines)
