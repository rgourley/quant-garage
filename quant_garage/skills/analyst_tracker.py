"""
analyst-tracker as an importable library function.

Pulls Benzinga analyst ratings history for a ticker over the lookback
window, classifies each event (upgrade / downgrade / reiteration /
initiation / drop_coverage / PT raise / PT cut), aggregates by firm,
and reports the trajectory. Optional overlay on Bulls-Bears-Say when
that endpoint is entitled.

    from quant_garage.skills.analyst_tracker import run, render
    payload = run("NVDA", lookback_days=180)

Reads MASSIVE_API_KEY from env. Requires Stocks Basic + Benzinga
analyst ratings entitlement.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from .. import MassiveClient, FetchError, today, utcnow_iso


# Standardized rating buckets. Benzinga's raw rating strings vary by
# firm (buy, outperform, overweight, market perform, hold, sell, etc.)
# so we bucket to a 5-point scale for comparability.
_BUY_SET = frozenset({
    "buy", "strong buy", "outperform", "overweight", "positive",
    "market outperform", "sector outperform", "conviction buy",
    "add", "accumulate", "top pick",
})
_HOLD_SET = frozenset({
    "hold", "neutral", "market perform", "sector perform", "equal-weight",
    "equal weight", "in-line", "mixed", "peer perform",
})
_SELL_SET = frozenset({
    "sell", "underperform", "underweight", "reduce", "negative",
    "market underperform", "sector underperform",
})


def _bucket_rating(raw: str | None) -> str:
    if not raw:
        return "unknown"
    s = raw.strip().lower()
    if s in _BUY_SET:
        return "buy"
    if s in _SELL_SET:
        return "sell"
    if s in _HOLD_SET:
        return "hold"
    return "other"


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


def _fetch_ratings(
    client: MassiveClient, ticker: str, from_date: str, sources: _Sources,
) -> tuple[list[dict], bool]:
    """
    Returns (rows, entitled). entitled=False when the account lacks the
    Benzinga analyst ratings entitlement.
    """
    path = (
        f"/benzinga/v1/ratings?ticker={ticker}"
        f"&date.gte={from_date}&limit=1000&sort=date.desc"
    )
    rows: list[dict] = []
    try:
        for page, fetched_at in client.paginate(path):
            rows.extend(page)
            sources.record(
                f"/benzinga/v1/ratings?ticker={ticker}&date.gte={from_date}",
                fetched_at,
                f"analyst ratings history for {ticker}",
            )
            if len(rows) >= 5000:
                break
        return rows, True
    except FetchError as e:
        msg = str(e).lower()
        if "not_authorized" in msg or "not entitled" in msg or (
            getattr(e, "status_code", None) in (401, 402, 403)
        ):
            return [], False
        raise


# ----- Classification -----

def _classify_event(row: dict) -> str:
    """
    Combine rating_action + rating/previous_rating buckets into one
    of: upgrade, downgrade, reiteration, initiation, drop_coverage,
    price_target_change_only, other.
    """
    action = (row.get("rating_action") or "").strip().lower()
    cur = _bucket_rating(row.get("rating"))
    prior = _bucket_rating(row.get("previous_rating")) if row.get("previous_rating") else None

    if "drop" in action or "cease" in action or "suspend" in action:
        return "drop_coverage"
    if "initiat" in action:
        return "initiation"
    if "upgrade" in action:
        return "upgrade"
    if "downgrade" in action:
        return "downgrade"
    if prior and prior != cur and cur != "unknown" and prior != "unknown":
        _rank = {"buy": 3, "hold": 2, "sell": 1, "other": 0, "unknown": 0}
        if _rank[cur] > _rank[prior]:
            return "upgrade"
        if _rank[cur] < _rank[prior]:
            return "downgrade"
    if "maintain" in action or "reiterat" in action:
        cur_pt = row.get("adjusted_price_target")
        prior_pt = row.get("previous_adjusted_price_target")
        if cur_pt is not None and prior_pt is not None and cur_pt != prior_pt:
            return "price_target_change_only"
        return "reiteration"
    return "other"


def _pt_direction(row: dict) -> tuple[str, float | None]:
    cur = row.get("adjusted_price_target")
    prior = row.get("previous_adjusted_price_target")
    if cur is None:
        return ("no_pt", None)
    if prior is None:
        return ("new_pt", None)
    if prior == 0:
        return ("new_pt", None)
    delta_pct = (cur - prior) / abs(prior)
    if abs(delta_pct) < 0.005:
        return ("unchanged", round(delta_pct, 4))
    if delta_pct > 0:
        return ("raise", round(delta_pct, 4))
    return ("cut", round(delta_pct, 4))


# ----- Public API -----

def run(
    ticker: str,
    lookback_days: int = 180,
    client: MassiveClient | None = None,
) -> dict:
    """Pull Benzinga analyst ratings history for `ticker`.

    Args:
        ticker: single stock ticker.
        lookback_days: calendar-day window back from today. Default 180.
        client: reuse an existing MassiveClient.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")
    if lookback_days < 7:
        raise ValueError("lookback_days must be at least 7")

    client = client or MassiveClient()
    sources = _Sources()

    from_date = (today() - timedelta(days=lookback_days)).isoformat()
    rows, entitled = _fetch_ratings(client, ticker, from_date, sources)

    tier_caveats: list[str] = []

    if not entitled:
        tier_caveats.append(
            "This key is NOT entitled to the Benzinga Analyst Ratings product. "
            "The endpoint returned NOT_AUTHORIZED. Add Analyst Ratings at "
            "massive.com/pricing to unlock this skill."
        )
        return {
            "skill": "analyst-tracker",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "entitled": False,
            "lookback_days": lookback_days,
            "from_date": from_date,
            "n_events": 0,
            "events": [],
            "summary": None,
            "tier_caveats": tier_caveats,
            "sources": sources.to_list(),
        }

    if not rows:
        tier_caveats.append(
            f"No analyst ratings for {ticker} in the last {lookback_days} days."
        )
        return {
            "skill": "analyst-tracker",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "entitled": True,
            "lookback_days": lookback_days,
            "from_date": from_date,
            "n_events": 0,
            "events": [],
            "summary": None,
            "tier_caveats": tier_caveats,
            "sources": sources.to_list(),
        }

    events: list[dict] = []
    for r in rows:
        event_label = _classify_event(r)
        pt_dir, pt_delta = _pt_direction(r)
        events.append({
            "date": r.get("date"),
            "time": r.get("time"),
            "firm": r.get("firm"),
            "analyst": r.get("analyst"),
            "rating_raw": r.get("rating"),
            "previous_rating_raw": r.get("previous_rating"),
            "rating_bucket": _bucket_rating(r.get("rating")),
            "previous_rating_bucket": _bucket_rating(r.get("previous_rating")),
            "rating_action_raw": r.get("rating_action"),
            "event_label": event_label,
            "price_target": r.get("adjusted_price_target"),
            "previous_price_target": r.get("previous_adjusted_price_target"),
            "pt_direction": pt_dir,
            "pt_delta_pct": pt_delta,
            "currency": r.get("currency"),
            "importance": r.get("importance"),
        })

    label_counts: dict[str, int] = defaultdict(int)
    for e in events:
        label_counts[e["event_label"]] += 1

    n_upgrades = label_counts["upgrade"]
    n_downgrades = label_counts["downgrade"]
    net_direction = n_upgrades - n_downgrades

    # Price target statistics on events that have a PT
    pt_events = [e for e in events if e["price_target"] is not None]
    pt_raises = [e for e in events if e["pt_direction"] == "raise"]
    pt_cuts = [e for e in events if e["pt_direction"] == "cut"]

    # Current PT consensus (median of most recent non-null PT per firm)
    latest_pt_by_firm: dict[str, float] = {}
    for e in events:  # events are newest first
        firm = e.get("firm")
        if firm and firm not in latest_pt_by_firm and e.get("price_target"):
            latest_pt_by_firm[firm] = float(e["price_target"])
    consensus_pt = None
    consensus_pt_lo = None
    consensus_pt_hi = None
    if latest_pt_by_firm:
        pts = sorted(latest_pt_by_firm.values())
        mid = len(pts) // 2
        consensus_pt = pts[mid] if len(pts) % 2 == 1 else (pts[mid - 1] + pts[mid]) / 2
        consensus_pt_lo = min(pts)
        consensus_pt_hi = max(pts)

    # Ratings distribution (latest per firm)
    latest_rating_by_firm: dict[str, str] = {}
    for e in events:
        firm = e.get("firm")
        if firm and firm not in latest_rating_by_firm and e["rating_bucket"] != "unknown":
            latest_rating_by_firm[firm] = e["rating_bucket"]
    rating_counts = {"buy": 0, "hold": 0, "sell": 0, "other": 0}
    for r in latest_rating_by_firm.values():
        rating_counts[r] = rating_counts.get(r, 0) + 1

    summary = {
        "n_events": len(events),
        "n_firms": len(latest_rating_by_firm),
        "by_event_label": dict(label_counts),
        "n_upgrades": n_upgrades,
        "n_downgrades": n_downgrades,
        "net_direction": net_direction,
        "n_pt_raises": len(pt_raises),
        "n_pt_cuts": len(pt_cuts),
        "consensus_price_target_median": consensus_pt,
        "consensus_price_target_low": consensus_pt_lo,
        "consensus_price_target_high": consensus_pt_hi,
        "rating_distribution_latest_per_firm": rating_counts,
    }

    tier_caveats.append(
        "Analyst ratings reflect what sell-side firms said, not what the "
        "market believed. Track record varies enormously by firm and analyst."
    )
    if n_upgrades > n_downgrades:
        tier_caveats.append(
            "Note: sell-side upgrades tend to lag price into strength. "
            "A bullish tilt after a run-up is momentum, not signal."
        )
    if n_downgrades > n_upgrades:
        tier_caveats.append(
            "Note: sell-side downgrades tend to lag price into weakness. "
            "A bearish tilt after a drawdown is often already priced in."
        )

    return {
        "skill": "analyst-tracker",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "entitled": True,
        "lookback_days": lookback_days,
        "from_date": from_date,
        "n_events": len(events),
        "events": events,
        "summary": summary,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

_LABEL_TAG = {
    "upgrade": "UPGRADE",
    "downgrade": "DOWNGRADE",
    "initiation": "initiated",
    "reiteration": "reiterated",
    "price_target_change_only": "PT change",
    "drop_coverage": "DROPPED",
    "other": "other",
}


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return ""
    sign = "+" if x >= 0 else ""
    return f"({sign}{x*100:.1f}%)"


def _fmt_pt(cur, prior, delta) -> str:
    if cur is None:
        return ""
    if prior is None:
        return f"PT ${cur:.2f}"
    return f"PT ${prior:.2f} → ${cur:.2f} {_fmt_pct(delta)}"


def render(payload: dict) -> str:
    lines: list[str] = []
    ticker = payload["ticker"]

    if not payload.get("entitled", True):
        lines.append(f"Analyst tracker: {ticker} — ENTITLEMENT REQUIRED")
        lines.append("")
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    n = payload["n_events"]
    lookback = payload["lookback_days"]
    lines.append(f"Analyst tracker: {ticker} · {lookback}d lookback · {n} rating event(s)")

    summary = payload.get("summary")
    if summary and n > 0:
        counts = summary["by_event_label"]
        parts = []
        for label in ("upgrade", "downgrade", "initiation", "reiteration",
                      "price_target_change_only", "drop_coverage", "other"):
            if counts.get(label):
                tag = "PT" if label == "price_target_change_only" else label
                parts.append(f"{tag}: {counts[label]}")
        if parts:
            lines.append("By action: " + " · ".join(parts))

        rd = summary["rating_distribution_latest_per_firm"]
        n_firms = summary["n_firms"]
        buy_pct = (rd["buy"] / n_firms * 100) if n_firms else 0
        hold_pct = (rd["hold"] / n_firms * 100) if n_firms else 0
        sell_pct = (rd["sell"] / n_firms * 100) if n_firms else 0
        lines.append(
            f"Latest per firm ({n_firms}): "
            f"Buy {rd['buy']} ({buy_pct:.0f}%) · "
            f"Hold {rd['hold']} ({hold_pct:.0f}%) · "
            f"Sell {rd['sell']} ({sell_pct:.0f}%)"
        )

        if summary["consensus_price_target_median"]:
            lines.append(
                f"Consensus PT (median of latest per firm): "
                f"${summary['consensus_price_target_median']:.2f} "
                f"(low ${summary['consensus_price_target_low']:.2f}, "
                f"high ${summary['consensus_price_target_high']:.2f})"
            )
    lines.append("")

    if not payload["events"]:
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    lines.append("Timeline (most recent first)")
    for e in payload["events"][:25]:
        tag = _LABEL_TAG.get(e["event_label"], e["event_label"])
        line = f"  {e['date']} · {e['firm'] or '?'} ({e['analyst'] or '?'}) · {tag}"
        if e["rating_bucket"] != "unknown":
            rating_str = e["rating_raw"] or e["rating_bucket"]
            if e["previous_rating_raw"] and e["previous_rating_raw"] != e["rating_raw"]:
                rating_str = f"{e['previous_rating_raw']} → {rating_str}"
            line += f" · {rating_str}"
        lines.append(line)
        pt_str = _fmt_pt(e["price_target"], e["previous_price_target"], e["pt_delta_pct"])
        if pt_str:
            lines.append(f"    {pt_str}")
    if len(payload["events"]) > 25:
        lines.append(f"  ... and {len(payload['events']) - 25} more")
    lines.append("")

    if summary:
        take_parts: list[str] = []
        if summary["n_upgrades"] > summary["n_downgrades"]:
            take_parts.append(
                f"Sell-side net-bullish over the window: "
                f"{summary['n_upgrades']} upgrades vs "
                f"{summary['n_downgrades']} downgrades."
            )
        elif summary["n_downgrades"] > summary["n_upgrades"]:
            take_parts.append(
                f"Sell-side net-bearish: "
                f"{summary['n_downgrades']} downgrades vs "
                f"{summary['n_upgrades']} upgrades."
            )
        elif summary["n_pt_raises"] > summary["n_pt_cuts"]:
            take_parts.append(
                f"No rating changes but price targets are rising: "
                f"{summary['n_pt_raises']} raises vs {summary['n_pt_cuts']} cuts. "
                "Sell-side is adjusting numbers, not conviction."
            )
        elif summary["n_pt_cuts"] > summary["n_pt_raises"]:
            take_parts.append(
                f"No rating changes but price targets are falling: "
                f"{summary['n_pt_cuts']} cuts vs {summary['n_pt_raises']} raises."
            )
        else:
            take_parts.append("Sell-side positioning stable; no meaningful direction.")

        if summary["consensus_price_target_median"]:
            take_parts.append(
                f"Consensus PT ${summary['consensus_price_target_median']:.2f} "
                f"across {summary['n_firms']} firms."
            )
        lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
