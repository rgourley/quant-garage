"""
pre-earnings-full-stack as an importable library function.

Workflow composite for a single ticker heading into an earnings print.
Chains five sub-skills:

- earnings-blackout: is this ticker printing soon?
- event-study: what has the reaction distribution looked like on
  prior prints? (aggregate over last 8 quarters)
- guidance-tracker: has management been raising or cutting? (Benzinga
  add-on required; skipped gracefully if missing)
- analyst-tracker: sell-side positioning (Benzinga add-on required;
  skipped gracefully if missing)
- mc-portfolio-simulator: given a proposed weight in this book, what
  is the P&L distribution over the next 10 trading days including
  the print?

Answers "should I trade this print, and if so, how do I size it?"

    from quant_garage.skills.pre_earnings_full_stack import run, render
    payload = run("NVDA", proposed_weight=0.10)
"""
from __future__ import annotations

from .. import MassiveClient, today, utcnow_iso
from . import (
    earnings_blackout,
    event_study,
    guidance_tracker,
    analyst_tracker,
    mc_portfolio_simulator,
)


def _safe_run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def run(
    ticker: str,
    proposed_weight: float = 0.10,
    n_prior_quarters: int = 8,
    horizon_days: int = 10,
    n_paths: int = 10_000,
    client: MassiveClient | None = None,
) -> dict:
    """Full pre-earnings prep on a single ticker.

    Args:
        ticker: single ticker.
        proposed_weight: weight if you were to add this position. Used
            for MC sizing simulation. Default 10%.
        n_prior_quarters: number of prior earnings events to include in
            the event study aggregate. Default 8.
        horizon_days: forward horizon for the MC sizing. Default 10
            trading days (~2 weeks, covers the print).
        n_paths: MC path count. Default 10,000.
        client: reuse an existing MassiveClient.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")
    if not (0 < proposed_weight <= 1.0):
        raise ValueError("proposed_weight must be in (0, 1]")

    client = client or MassiveClient()

    # 1. Earnings blackout: is it printing soon?
    eb_out, eb_err = _safe_run(
        earnings_blackout.run,
        watchlist=ticker,
        client=client,
    )

    # 2. Event study on prior prints (aggregate mode, last ~2 years)
    from datetime import timedelta
    from_date = (today() - timedelta(days=n_prior_quarters * 100)).isoformat()
    to_date = today().isoformat()
    es_out, es_err = _safe_run(
        event_study.run,
        tickers=ticker,
        event_class="earnings",
        window=f"{from_date}..{to_date}",
        client=client,
    )

    # 3. Guidance tracker (Benzinga add-on)
    gt_out, gt_err = _safe_run(
        guidance_tracker.run,
        ticker,
        client=client,
    )

    # 4. Analyst tracker
    at_out, at_err = _safe_run(
        analyst_tracker.run,
        ticker,
        lookback_days=180,
        client=client,
    )

    # 5. MC sizing: single-name book at proposed_weight, cash for the rest
    positions_str = f"{ticker}={proposed_weight:.4f}"
    mc_out, mc_err = _safe_run(
        mc_portfolio_simulator.run,
        positions_str,
        simulation_days=horizon_days,
        n_paths=n_paths,
        client=client,
    )

    errors: dict[str, str] = {}
    for name, err in (
        ("earnings-blackout", eb_err),
        ("event-study", es_err),
        ("guidance-tracker", gt_err),
        ("analyst-tracker", at_err),
        ("mc-portfolio-simulator", mc_err),
    ):
        if err:
            errors[name] = err

    # Derive posture
    posture = _derive_posture(eb_out, es_out, gt_out, at_out, mc_out, ticker)

    tier_caveats: list[str] = [
        "This is a chained composite; each sub-skill has its own caveats and "
        "entitlement requirements.",
        "MC sizing assumes constant covariance and returns; earnings prints are "
        "regime shifts that MC cannot model. Read the event-study distribution "
        "for the actual reaction shape.",
    ]
    if errors:
        for name, err in errors.items():
            tier_caveats.append(f"Sub-skill {name} failed: {err}")

    return {
        "skill": "pre-earnings-full-stack",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "proposed_weight": float(proposed_weight),
        "horizon_days": int(horizon_days),
        "earnings_blackout": eb_out,
        "event_study": es_out,
        "guidance_tracker": gt_out,
        "analyst_tracker": at_out,
        "mc_sizing": mc_out,
        "posture": posture,
        "sub_skill_errors": errors,
        "tier_caveats": tier_caveats,
    }


def _derive_posture(eb, es, gt, at, mc, ticker: str) -> dict:
    """Combine sub-skill outputs into a trade-posture recommendation."""
    signals: list[str] = []
    warnings: list[str] = []

    # Is a print imminent?
    print_imminent = False
    print_status = None
    if eb and eb.get("results"):
        for r in eb["results"]:
            if r.get("ticker") == ticker:
                print_status = r.get("status")
                if print_status in ("blackout_imminent", "blackout_soon", "just_printed", "recent_print"):
                    print_imminent = True
                    signals.append(f"earnings status: {print_status}")
                break

    # Event study reaction distribution
    reaction_take = None
    if es and es.get("summary"):
        s = es["summary"]
        mean_car = s.get("mean_t5_car_pct")
        t_stat = s.get("t_stat_avg_vs_zero")
        sig = s.get("significant")
        n = s.get("n_subjects")
        shape = s.get("distribution_shape") or {}
        if mean_car is not None:
            reaction_take = f"prior print avg T+5 CAR {mean_car*100:+.2f}% (t={t_stat:.2f}, n={n})"
            if sig:
                signals.append(f"prior prints statistically significant ({reaction_take})")
            if shape.get("warn_mean_misleading"):
                warnings.append(
                    f"prior print reactions are {shape.get('modality_label', 'bimodal or fat-tailed')} - "
                    "the mean is misleading, expect asymmetric outcomes"
                )

    # Guidance trajectory
    if gt and gt.get("summary"):
        gsum = gt["summary"]
        last_label = gsum.get("last_event_label")
        raised = (gsum.get("counts_by_label") or {}).get("raised", 0)
        lowered = (gsum.get("counts_by_label") or {}).get("lowered", 0)
        if last_label:
            signals.append(f"most recent guidance event: {last_label}")
        if raised > lowered:
            signals.append(f"management raising guidance consistently ({raised} raises, {lowered} cuts)")
        elif lowered > raised:
            warnings.append(f"management cutting guidance ({lowered} cuts, {raised} raises)")

    # Analyst positioning
    if at and at.get("summary"):
        asum = at["summary"]
        n_up = asum.get("n_upgrades", 0)
        n_dn = asum.get("n_downgrades", 0)
        if n_up > n_dn:
            signals.append(f"analyst net-bullish ({n_up} up vs {n_dn} down)")
        elif n_dn > n_up:
            warnings.append(f"analyst net-bearish ({n_dn} down vs {n_up} up)")
        if asum.get("consensus_price_target_median"):
            signals.append(f"analyst consensus PT ${asum['consensus_price_target_median']:.2f}")

    # MC sizing tail read
    tail_p5 = None
    tail_pct = None
    if mc and mc.get("cumulative_return_distribution"):
        cum = mc["cumulative_return_distribution"]
        tail_p5 = cum.get("p5")
        if tail_p5 is not None:
            tail_pct = f"{tail_p5*100:+.1f}%"
            if tail_p5 < -0.05:
                warnings.append(
                    f"MC p5 tail is {tail_pct} over the horizon at proposed weight; consider sizing down"
                )

    # Posture verdict
    n_pos = len(signals)
    n_neg = len(warnings)
    if not print_imminent:
        verdict = "no_imminent_print"
    elif n_neg >= 2 and n_pos <= 1:
        verdict = "avoid_or_hedge"
    elif n_pos >= 2 and n_neg == 0:
        verdict = "constructive_setup"
    else:
        verdict = "mixed_setup"

    return {
        "verdict": verdict,
        "print_imminent": print_imminent,
        "print_status": print_status,
        "signals": signals,
        "warnings": warnings,
        "reaction_take": reaction_take,
        "mc_p5_return": tail_p5,
    }


# ----- Renderer -----

_VERDICT_TAG = {
    "no_imminent_print": "NO IMMINENT PRINT",
    "avoid_or_hedge": "AVOID OR HEDGE",
    "constructive_setup": "CONSTRUCTIVE SETUP",
    "mixed_setup": "MIXED SETUP",
}


def _fmt_pct(x, signed=False, dec=1):
    if x is None:
        return "n/a"
    sign = "+" if signed and x >= 0 else ""
    return f"{sign}{x*100:.{dec}f}%"


def render(payload: dict) -> str:
    lines: list[str] = []
    ticker = payload["ticker"]
    weight = payload["proposed_weight"]
    horizon = payload["horizon_days"]

    lines.append(
        f"Pre-earnings full stack: {ticker} · proposed weight {weight*100:.1f}% · "
        f"horizon {horizon}d"
    )
    posture = payload.get("posture") or {}
    tag = _VERDICT_TAG.get(posture.get("verdict"), posture.get("verdict", ""))
    lines.append(f"Posture: {tag}")
    if posture.get("print_status"):
        lines.append(f"Earnings status: {posture['print_status']}")
    lines.append("")

    if posture.get("signals"):
        lines.append("Signals")
        for s in posture["signals"]:
            lines.append(f"  · {s}")
        lines.append("")

    if posture.get("warnings"):
        lines.append("Warnings")
        for w in posture["warnings"]:
            lines.append(f"  · {w}")
        lines.append("")

    if payload.get("event_study") and payload["event_study"].get("summary"):
        s = payload["event_study"]["summary"]
        lines.append(f"Prior earnings reactions (n={s.get('n_subjects')})")
        lines.append(
            f"  T+5 CAR: mean {_fmt_pct(s.get('mean_t5_car_pct'), signed=True)} · "
            f"median {_fmt_pct(s.get('median_t5_car_pct'), signed=True)} · "
            f"t-stat {s.get('t_stat_avg_vs_zero', 0):+.2f}"
        )
        pctl = s.get("percentiles") or {}
        if pctl:
            lines.append(
                f"  Distribution: p10 {_fmt_pct(pctl.get('p10_pct'), signed=True)} · "
                f"p50 {_fmt_pct(pctl.get('p50_pct'), signed=True)} · "
                f"p90 {_fmt_pct(pctl.get('p90_pct'), signed=True)}"
            )
        shape = s.get("distribution_shape") or {}
        if shape.get("sparkline"):
            lines.append(f"  Shape: {shape.get('modality_label')} · {shape['sparkline']}")
        lines.append("")

    if payload.get("guidance_tracker") and payload["guidance_tracker"].get("entitled"):
        g = payload["guidance_tracker"]
        if g.get("summary"):
            gsum = g["summary"]
            counts = gsum.get("counts_by_label", {})
            lines.append(f"Guidance track record ({g.get('n_events')} events)")
            counts_parts = [f"{k}: {v}" for k, v in counts.items() if v]
            lines.append("  " + " · ".join(counts_parts))
            lines.append("")

    if payload.get("analyst_tracker") and payload["analyst_tracker"].get("entitled"):
        a = payload["analyst_tracker"]
        if a.get("summary"):
            asum = a["summary"]
            rd = asum.get("rating_distribution_latest_per_firm", {})
            lines.append(f"Analyst positioning ({asum.get('n_events')} events)")
            lines.append(
                f"  Buy {rd.get('buy', 0)} · Hold {rd.get('hold', 0)} · Sell {rd.get('sell', 0)}"
                + (f" · Consensus PT ${asum.get('consensus_price_target_median'):.2f}" if asum.get('consensus_price_target_median') else "")
            )
            lines.append("")

    if payload.get("mc_sizing") and payload["mc_sizing"].get("cumulative_return_distribution"):
        cum = payload["mc_sizing"]["cumulative_return_distribution"]
        loss = payload["mc_sizing"].get("loss_probabilities", {})
        lines.append(f"MC sizing ({horizon}d, {payload['mc_sizing'].get('n_paths'):,} paths)")
        lines.append(
            f"  Return distribution: p5 {_fmt_pct(cum.get('p5'), signed=True)} · "
            f"p50 {_fmt_pct(cum.get('p50'), signed=True)} · "
            f"p95 {_fmt_pct(cum.get('p95'), signed=True)}"
        )
        lines.append(
            f"  P(loss > 5%): {loss.get('P_loss_gt_5pct', 0)*100:.1f}% · "
            f"P(loss > 10%): {loss.get('P_loss_gt_10pct', 0)*100:.1f}%"
        )
        lines.append("")

    # Take
    take_parts: list[str] = []
    if posture.get("verdict") == "constructive_setup":
        take_parts.append(
            "Constructive setup: multiple positive signals, no material warnings. "
            "The MC tail informs sizing but doesn't model the print itself."
        )
    elif posture.get("verdict") == "avoid_or_hedge":
        take_parts.append(
            "Avoid or hedge: multiple warnings suggest the risk/reward asymmetry "
            "is unfavorable. Consider reducing size or hedging with puts."
        )
    elif posture.get("verdict") == "mixed_setup":
        take_parts.append(
            "Mixed setup: both bullish and bearish evidence. Size conservatively "
            "and read the event-study distribution before committing."
        )
    elif posture.get("verdict") == "no_imminent_print":
        take_parts.append(
            "No imminent earnings print; the full-stack read is context, not a trade."
        )
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
