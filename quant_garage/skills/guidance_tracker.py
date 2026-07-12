"""
guidance-tracker as an importable library function.

Pulls Benzinga Corporate Guidance history for a ticker, classifies each
event as raise / lower / reaffirm / initiation against the prior figure,
groups by fiscal period, and reports the trajectory. Answers "how has
management's own view of the year evolved?"

    from quant_garage.skills.guidance_tracker import run, render
    payload = run("NVDA", lookback_days=540)

Reads MASSIVE_API_KEY from env. Requires the Benzinga Corporate
Guidance add-on (~$99/mo). Rob's default Stocks Business key does NOT
include this endpoint; the skill returns a clear tier_caveats block
when the entitlement is missing.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from .. import MassiveClient, FetchError, today, utcnow_iso


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


def _fetch_guidance(
    client: MassiveClient, ticker: str, from_date: str, sources: _Sources,
) -> tuple[list[dict], bool]:
    """
    Returns (rows, entitled). `entitled` is False when the account
    lacks the Benzinga Corporate Guidance add-on (403/NOT_AUTHORIZED).
    """
    path = (
        f"/benzinga/v1/guidance?ticker={ticker}"
        f"&date.gte={from_date}&limit=1000&sort=date.desc"
    )
    rows: list[dict] = []
    try:
        for page, fetched_at in client.paginate(path):
            rows.extend(page)
            sources.record(
                f"/benzinga/v1/guidance?ticker={ticker}&date.gte={from_date}",
                fetched_at,
                f"corporate guidance history for {ticker}",
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


def _pick_midpoint(row: dict, metric: str) -> float | None:
    """
    metric ∈ {"eps", "revenue"}. Prefer estimated_ midpoint, fall back
    to (min + max) / 2, then to min or max alone. Returns None when
    nothing is present.
    """
    est = row.get(f"estimated_{metric}_guidance")
    if est is not None:
        return float(est)
    lo = row.get(f"min_{metric}_guidance")
    hi = row.get(f"max_{metric}_guidance")
    if lo is not None and hi is not None:
        return (float(lo) + float(hi)) / 2.0
    if lo is not None:
        return float(lo)
    if hi is not None:
        return float(hi)
    return None


def _pick_prior_midpoint(row: dict, metric: str) -> float | None:
    lo = row.get(f"previous_min_{metric}_guidance")
    hi = row.get(f"previous_max_{metric}_guidance")
    if lo is not None and hi is not None:
        return (float(lo) + float(hi)) / 2.0
    if lo is not None:
        return float(lo)
    if hi is not None:
        return float(hi)
    return None


def _classify_event(row: dict) -> tuple[str, dict]:
    """
    Returns (label, deltas). Label ∈ {initiation, raised, lowered,
    reaffirmed, mixed, unclear}.
    """
    eps_cur = _pick_midpoint(row, "eps")
    rev_cur = _pick_midpoint(row, "revenue")
    eps_prior = _pick_prior_midpoint(row, "eps")
    rev_prior = _pick_prior_midpoint(row, "revenue")

    def _delta(cur: float | None, prior: float | None) -> tuple[str | None, float | None]:
        if cur is None:
            return (None, None)
        if prior is None:
            return ("initiated", None)
        if prior == 0:
            return ("initiated", None)
        delta_pct = (cur - prior) / abs(prior)
        if abs(delta_pct) < 0.005:
            return ("reaffirmed", round(delta_pct, 4))
        if delta_pct > 0:
            return ("raised", round(delta_pct, 4))
        return ("lowered", round(delta_pct, 4))

    eps_label, eps_delta = _delta(eps_cur, eps_prior)
    rev_label, rev_delta = _delta(rev_cur, rev_prior)

    labels = {l for l in (eps_label, rev_label) if l is not None}
    if not labels:
        headline = "unclear"
    elif labels == {"initiated"}:
        headline = "initiation"
    elif labels == {"raised"}:
        headline = "raised"
    elif labels == {"lowered"}:
        headline = "lowered"
    elif labels == {"reaffirmed"}:
        headline = "reaffirmed"
    elif "raised" in labels and "lowered" in labels:
        headline = "mixed"
    elif labels == {"raised", "initiated"} or labels == {"initiated", "raised"}:
        headline = "raised"
    elif labels == {"lowered", "initiated"} or labels == {"initiated", "lowered"}:
        headline = "lowered"
    elif labels == {"reaffirmed", "initiated"} or labels == {"initiated", "reaffirmed"}:
        headline = "reaffirmed"
    else:
        headline = "mixed"

    return headline, {
        "eps_current_midpoint": eps_cur,
        "eps_prior_midpoint": eps_prior,
        "eps_direction": eps_label,
        "eps_delta_pct": eps_delta,
        "revenue_current_midpoint": rev_cur,
        "revenue_prior_midpoint": rev_prior,
        "revenue_direction": rev_label,
        "revenue_delta_pct": rev_delta,
    }


def run(
    ticker: str,
    lookback_days: int = 540,
    client: MassiveClient | None = None,
) -> dict:
    """Pull Benzinga corporate guidance history for `ticker` over the window.

    Args:
        ticker: single stock ticker.
        lookback_days: calendar-day window back from today. Default 540
            (covers 1.5 years, enough for multiple guidance updates per
            fiscal period).
        client: reuse an existing MassiveClient.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")
    if lookback_days < 30:
        raise ValueError("lookback_days must be at least 30")

    client = client or MassiveClient()
    sources = _Sources()

    from_date = (today() - timedelta(days=lookback_days)).isoformat()
    rows, entitled = _fetch_guidance(client, ticker, from_date, sources)

    tier_caveats: list[str] = []

    if not entitled:
        tier_caveats.append(
            "This key is NOT entitled to the Benzinga Corporate Guidance add-on. "
            "The endpoint returned NOT_AUTHORIZED. Add the Corporate Guidance product "
            "(around $99/month) at massive.com/pricing to unlock this skill."
        )
        return {
            "skill": "guidance-tracker",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "entitled": False,
            "lookback_days": lookback_days,
            "from_date": from_date,
            "n_events": 0,
            "events": [],
            "by_period": {},
            "summary": None,
            "tier_caveats": tier_caveats,
            "sources": sources.to_list(),
        }

    if not rows:
        tier_caveats.append(
            f"No corporate guidance events for {ticker} in the last {lookback_days} days."
        )
        return {
            "skill": "guidance-tracker",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "entitled": True,
            "lookback_days": lookback_days,
            "from_date": from_date,
            "n_events": 0,
            "events": [],
            "by_period": {},
            "summary": None,
            "tier_caveats": tier_caveats,
            "sources": sources.to_list(),
        }

    events: list[dict] = []
    for r in rows:
        label, deltas = _classify_event(r)
        events.append({
            "date": r.get("date"),
            "release_type": r.get("release_type"),
            "fiscal_year": r.get("fiscal_year"),
            "fiscal_period": r.get("fiscal_period"),
            "importance": r.get("importance"),
            "eps_method": r.get("eps_method"),
            "revenue_method": r.get("revenue_method"),
            "label": label,
            **deltas,
            "notes": r.get("notes"),
        })

    def _fp_key(e: dict) -> tuple[int, str]:
        fy = e.get("fiscal_year") or 0
        fp = e.get("fiscal_period") or "?"
        return (int(fy), fp)

    by_period: dict[str, dict] = defaultdict(lambda: {"events": [], "trajectory": []})
    for e in events:
        period_key = f"FY{e.get('fiscal_year') or '?'} {e.get('fiscal_period') or '?'}"
        by_period[period_key]["events"].append(e)
        by_period[period_key]["trajectory"].append(e["label"])

    counts: dict[str, int] = defaultdict(int)
    for e in events:
        counts[e["label"]] += 1

    last_event = events[0] if events else None
    summary = {
        "n_events": len(events),
        "counts_by_label": dict(counts),
        "last_event_date": last_event["date"] if last_event else None,
        "last_event_label": last_event["label"] if last_event else None,
        "n_periods": len(by_period),
    }

    tier_caveats.append(
        "Guidance events are what management said; they are not what the company delivered. "
        "For actual vs guided, chain with earnings-drilldown."
    )

    return {
        "skill": "guidance-tracker",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "entitled": True,
        "lookback_days": lookback_days,
        "from_date": from_date,
        "n_events": len(events),
        "events": events,
        "by_period": dict(by_period),
        "summary": summary,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

_LABEL_TAG = {
    "raised": "RAISED",
    "lowered": "LOWERED",
    "reaffirmed": "reaffirmed",
    "initiation": "initiated",
    "mixed": "mixed",
    "unclear": "unclear",
}


def _fmt_pct(delta: float | None) -> str:
    if delta is None:
        return ""
    sign = "+" if delta >= 0 else ""
    return f"({sign}{delta*100:.1f}%)"


def render(payload: dict) -> str:
    ticker = payload["ticker"]
    lines: list[str] = []

    if not payload.get("entitled", True):
        lines.append(f"Guidance tracker: {ticker} — ENTITLEMENT REQUIRED")
        lines.append("")
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    n = payload.get("n_events", 0)
    lookback = payload["lookback_days"]
    lines.append(f"Guidance tracker: {ticker} · {lookback}d lookback · {n} guidance event(s)")

    summary = payload.get("summary")
    if summary and n > 0:
        counts = summary["counts_by_label"]
        pieces = []
        for label in ("raised", "lowered", "reaffirmed", "initiation", "mixed"):
            if counts.get(label):
                pieces.append(f"{label}: {counts[label]}")
        if pieces:
            lines.append("By action: " + " · ".join(pieces))
    lines.append("")

    if not payload["events"]:
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    lines.append("Timeline (most recent first)")
    for e in payload["events"][:20]:
        period = f"FY{e.get('fiscal_year')} {e.get('fiscal_period')}"
        tag = _LABEL_TAG.get(e["label"], e["label"])
        line = f"  {e['date']} · {period} · {tag}"
        parts: list[str] = []
        if e.get("eps_current_midpoint") is not None:
            eps_delta = _fmt_pct(e.get("eps_delta_pct"))
            eps_direction = e.get("eps_direction") or ""
            parts.append(f"EPS {e['eps_current_midpoint']:.2f} {eps_direction} {eps_delta}".strip())
        if e.get("revenue_current_midpoint") is not None:
            rev_delta = _fmt_pct(e.get("revenue_delta_pct"))
            rev_direction = e.get("revenue_direction") or ""
            rev_val = e["revenue_current_midpoint"]
            if abs(rev_val) >= 1_000_000_000:
                rev_str = f"${rev_val / 1_000_000_000:.2f}B"
            elif abs(rev_val) >= 1_000_000:
                rev_str = f"${rev_val / 1_000_000:.1f}M"
            else:
                rev_str = f"${rev_val:,.0f}"
            parts.append(f"Rev {rev_str} {rev_direction} {rev_delta}".strip())
        if parts:
            lines.append(line)
            lines.append("    " + " · ".join(parts))
        else:
            lines.append(line)
        if e.get("notes"):
            note = " ".join(e["notes"].split())[:140]
            lines.append(f"    note: {note}")
    if len(payload["events"]) > 20:
        lines.append(f"  ... and {len(payload['events']) - 20} more")
    lines.append("")

    take_parts: list[str] = []
    if summary:
        raised = summary["counts_by_label"].get("raised", 0)
        lowered = summary["counts_by_label"].get("lowered", 0)
        if raised > 0 and lowered == 0:
            take_parts.append(
                f"Management has been raising guidance consistently ({raised} raise(s), 0 cuts)."
            )
        elif lowered > 0 and raised == 0:
            take_parts.append(
                f"Management has been cutting guidance ({lowered} cut(s), 0 raises)."
            )
        elif raised > 0 and lowered > 0:
            take_parts.append(
                f"Mixed guidance trajectory: {raised} raise(s) vs {lowered} cut(s)."
            )
        elif summary["counts_by_label"].get("reaffirmed"):
            take_parts.append("Management has been reaffirming; no directional change.")
        else:
            take_parts.append("Only initiations on record; no comparable prior figures.")
        if summary.get("last_event_label"):
            take_parts.append(
                f"Most recent event ({summary['last_event_date']}) was a {summary['last_event_label']}."
            )
    if take_parts:
        lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
