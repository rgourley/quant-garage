"""
filing-triangulation as an importable library function.

Composite workflow that runs five filing / ownership skills on a single
ticker and returns a unified fundamental report:

- 8-k-scanner: material events over the lookback window
- risk-factor-delta: YoY 10-K risk factor changes
- filing-sentiment: LM tone shift on 10-K narrative
- insider-flow: Form 4 signal vs noise
- analyst-tracker: sell-side ratings and consensus PT

Answers "what is the full fundamental picture on this name right now?"

Handles entitlement gaps gracefully: if analyst-tracker returns
NOT_AUTHORIZED, the workflow still completes and reports the other four.

    from quant_garage.skills.filing_triangulation import run, render
    payload = run("AAPL")
"""
from __future__ import annotations

from typing import Any

from .. import MassiveClient, today, utcnow_iso
from . import (
    eight_k_scanner,
    risk_factor_delta,
    filing_sentiment,
    insider_flow,
    analyst_tracker,
)


def _safe_run(fn, *args, **kwargs) -> tuple[dict | None, str | None]:
    """Run a sub-skill with catch-all error handling."""
    try:
        result = fn(*args, **kwargs)
        return result, None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def run(
    ticker: str,
    lookback_days_8k: int = 90,
    lookback_days_insider: int = 180,
    lookback_days_analyst: int = 180,
    exclude_directors: bool = False,
    client: MassiveClient | None = None,
) -> dict:
    """Composite: run five filing/ownership skills on `ticker` and unify.

    Args:
        ticker: single stock ticker.
        lookback_days_8k: window for 8-K scan. Default 90 days.
        lookback_days_insider: window for Form 4 analysis. Default 180.
        lookback_days_analyst: window for analyst ratings. Default 180.
        exclude_directors: pass through to insider-flow (drop pure-
            director rows, useful for VC-heavy boards).
        client: reuse an existing MassiveClient.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")

    client = client or MassiveClient()

    eight_k_out, eight_k_err = _safe_run(
        eight_k_scanner.run,
        tickers=ticker,
        lookback_days=lookback_days_8k,
        client=client,
    )
    rfd_out, rfd_err = _safe_run(
        risk_factor_delta.run,
        ticker,
        client=client,
    )
    fs_out, fs_err = _safe_run(
        filing_sentiment.run,
        ticker,
        client=client,
    )
    ins_out, ins_err = _safe_run(
        insider_flow.run,
        ticker,
        lookback_days=lookback_days_insider,
        exclude_directors=exclude_directors,
        client=client,
    )
    an_out, an_err = _safe_run(
        analyst_tracker.run,
        ticker,
        lookback_days=lookback_days_analyst,
        client=client,
    )

    tier_caveats: list[str] = []
    errors: dict[str, str] = {}
    for name, err in (
        ("8-k-scanner", eight_k_err),
        ("risk-factor-delta", rfd_err),
        ("filing-sentiment", fs_err),
        ("insider-flow", ins_err),
        ("analyst-tracker", an_err),
    ):
        if err:
            errors[name] = err
            tier_caveats.append(f"Sub-skill {name} failed: {err}")

    # Build triangulated take from what we have
    triangulation = _triangulate(eight_k_out, rfd_out, fs_out, ins_out, an_out)

    return {
        "skill": "filing-triangulation",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "eight_k_scanner": eight_k_out,
        "risk_factor_delta": rfd_out,
        "filing_sentiment": fs_out,
        "insider_flow": ins_out,
        "analyst_tracker": an_out,
        "sub_skill_errors": errors,
        "triangulation": triangulation,
        "tier_caveats": tier_caveats,
    }


def _triangulate(eight_k: dict | None, rfd: dict | None, fs: dict | None,
                 ins: dict | None, an: dict | None) -> dict:
    """Derive a cross-source read from the sub-skill outputs."""
    signals: list[str] = []
    concerns: list[str] = []
    bullish: list[str] = []

    # 8-K signals
    if eight_k and eight_k.get("by_bucket"):
        for bucket, count in eight_k["by_bucket"].items():
            if bucket == "M&A / Strategic" and count > 0:
                signals.append(f"{count} M&A / strategic filing(s)")
            elif bucket == "Restatement / Restructuring" and count > 0:
                concerns.append(f"{count} restatement / restructuring filing(s)")
            elif bucket == "Leadership change" and count > 0:
                signals.append(f"{count} leadership change filing(s)")

    # Risk factor delta
    if rfd and rfd.get("summary"):
        s = rfd["summary"]
        n_added = s.get("n_added")
        n_removed = s.get("n_removed")
        n_changed = s.get("n_materially_changed")
        if n_added and n_added > 5:
            concerns.append(
                f"{n_added} new risk category(ies) added YoY"
                + (f" (concentrated in {s.get('largest_new_primary_category', 'various')})"
                   if s.get('largest_new_primary_category') else "")
            )
        if n_changed and n_changed > 3:
            signals.append(f"{n_changed} risk category text materially changed")

    # Filing sentiment
    if fs and fs.get("yoy_deltas"):
        for section, delta in fs["yoy_deltas"].items():
            cats = delta.get("categories") or {}
            for cat, d in cats.items():
                if d.get("label") in ("material", "dramatic"):
                    direction = "up" if d["delta_per_10k"] > 0 else "down"
                    detail = f"{section} {cat} {direction} {abs(d.get('delta_pct') or 0):.0f}%"
                    if cat in ("negative", "uncertain", "litigious") and direction == "up":
                        concerns.append(detail)
                    elif cat == "modal_strong" and direction == "down":
                        concerns.append(detail + " (management more hedged)")

    # Insider flow
    if ins and ins.get("summary"):
        sent = ins["summary"].get("sentiment")
        net = ins["summary"].get("net_conviction_dollars", 0)
        if sent in ("strong_bullish", "bullish"):
            bullish.append(f"insider net conviction +${abs(net):,.0f}")
        elif sent in ("strong_bearish", "bearish"):
            concerns.append(f"insider net conviction -${abs(net):,.0f}")
        clusters = ins.get("clusters") or []
        if clusters:
            bullish.append(f"{len(clusters)} insider cluster buy(s) detected")

    # Analyst tracker
    if an and an.get("summary"):
        s = an["summary"]
        n_up = s.get("n_upgrades", 0)
        n_dn = s.get("n_downgrades", 0)
        rd = s.get("rating_distribution_latest_per_firm", {})
        if n_up > n_dn:
            bullish.append(f"analyst net-bullish ({n_up} up vs {n_dn} down)")
        elif n_dn > n_up:
            concerns.append(f"analyst net-bearish ({n_dn} down vs {n_up} up)")
        if rd.get("sell", 0) > rd.get("buy", 0):
            concerns.append(f"more Sell than Buy ratings (Sell {rd['sell']}, Buy {rd['buy']})")

    # Verdict
    if len(concerns) >= 3 and len(bullish) == 0:
        verdict = "predominantly_concerning"
    elif len(bullish) >= 2 and len(concerns) <= 1:
        verdict = "predominantly_constructive"
    elif len(bullish) >= 1 and len(concerns) >= 2:
        verdict = "mixed"
    else:
        verdict = "no_clear_signal"

    return {
        "verdict": verdict,
        "bullish_signals": bullish,
        "concerns": concerns,
        "other_signals": signals,
        "n_bullish": len(bullish),
        "n_concerns": len(concerns),
    }


# ----- Renderer -----

_VERDICT_TAG = {
    "predominantly_constructive": "PREDOMINANTLY CONSTRUCTIVE",
    "predominantly_concerning": "PREDOMINANTLY CONCERNING",
    "mixed": "MIXED SIGNALS",
    "no_clear_signal": "no clear signal",
}


def render(payload: dict) -> str:
    lines: list[str] = []
    ticker = payload["ticker"]

    lines.append(f"Filing triangulation: {ticker}")
    triangulation = payload.get("triangulation") or {}
    tag = _VERDICT_TAG.get(triangulation.get("verdict"), triangulation.get("verdict", ""))
    lines.append(f"Verdict: {tag}")
    lines.append("")

    if triangulation.get("bullish_signals"):
        lines.append("Bullish signals")
        for b in triangulation["bullish_signals"]:
            lines.append(f"  · {b}")
        lines.append("")

    if triangulation.get("concerns"):
        lines.append("Concerns")
        for c in triangulation["concerns"]:
            lines.append(f"  · {c}")
        lines.append("")

    if triangulation.get("other_signals"):
        lines.append("Other")
        for o in triangulation["other_signals"]:
            lines.append(f"  · {o}")
        lines.append("")

    # Per-sub-skill summaries
    if payload.get("eight_k_scanner"):
        e = payload["eight_k_scanner"]
        if e.get("n_filings"):
            lines.append(f"8-K activity ({e['n_filings']} filings in {e.get('lookback_days', 0)}d)")
            for f in (e.get("filings") or [])[:3]:
                lines.append(f"  · {f['filing_date']} · {f['headline_bucket']}")
            lines.append("")

    if payload.get("risk_factor_delta") and payload["risk_factor_delta"].get("summary"):
        r = payload["risk_factor_delta"]["summary"]
        filings = payload["risk_factor_delta"].get("filings") or {}
        prior = filings.get("prior", {}).get("filing_date") if filings.get("prior") else None
        cur = filings.get("current", {}).get("filing_date") if filings.get("current") else None
        if prior and cur:
            lines.append(f"10-K risk factor delta ({prior} → {cur})")
            lines.append(
                f"  +{r.get('n_added', 0)} added, "
                f"-{r.get('n_removed', 0)} removed, "
                f"{r.get('n_materially_changed', 0)} materially changed, "
                f"{r.get('n_retained_unchanged', 0)} retained"
            )
            lines.append("")

    if payload.get("filing_sentiment") and payload["filing_sentiment"].get("yoy_deltas"):
        lines.append("10-K filing sentiment (YoY tone shifts)")
        for section, delta in payload["filing_sentiment"]["yoy_deltas"].items():
            cats = delta.get("categories") or {}
            material = [
                f"{cat} {d['label']}"
                for cat, d in cats.items()
                if d.get("label") in ("material", "dramatic")
            ]
            if material:
                lines.append(f"  · {section}: {', '.join(material)}")
        lines.append("")

    if payload.get("insider_flow") and payload["insider_flow"].get("summary"):
        s = payload["insider_flow"]["summary"]
        sent = s.get("sentiment", "unknown").upper().replace("_", " ")
        net = s.get("net_conviction_dollars", 0)
        lines.append(f"Insider flow ({payload['insider_flow'].get('lookback_days', 0)}d)")
        lines.append(f"  Sentiment: {sent} · net conviction ${net:+,.0f}")
        lines.append("")

    if payload.get("analyst_tracker") and payload["analyst_tracker"].get("summary"):
        s = payload["analyst_tracker"]["summary"]
        rd = s.get("rating_distribution_latest_per_firm", {})
        lines.append(f"Analyst tracker ({payload['analyst_tracker'].get('lookback_days', 0)}d)")
        lines.append(
            f"  {s.get('n_events', 0)} events · "
            f"Buy {rd.get('buy', 0)} · Hold {rd.get('hold', 0)} · Sell {rd.get('sell', 0)}"
        )
        if s.get("consensus_price_target_median"):
            ensemble = s.get("ensemble_weighted_price_target")
            lines.append(
                f"  Consensus PT (median): ${s['consensus_price_target_median']:.2f}"
                + (f" · ensemble-weighted: ${ensemble:.2f}" if ensemble else "")
            )
        lines.append("")

    if payload.get("sub_skill_errors"):
        lines.append("Sub-skill errors")
        for name, err in payload["sub_skill_errors"].items():
            lines.append(f"  · {name}: {err[:150]}")
        lines.append("")

    if payload.get("tier_caveats"):
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")

    return "\n".join(lines).rstrip()
