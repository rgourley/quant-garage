"""
risk-factor-delta as an importable library function.

Compares Item 1A Risk Factors between two 10-K filings for a name, using
Massive's pre-parsed and taxonomy-classified risk-factor endpoint. Reports
categories added, categories removed, and categories where the supporting
text materially changed year-over-year. Groups by primary category so a
reader sees the shape of what changed, not just a flat diff.

    from quant_garage.skills.risk_factor_delta import run, render
    payload = run("AAPL")
    print(render(payload))

Reads MASSIVE_API_KEY from env. Stocks Basic minimum (filing endpoints are
included on every stocks plan; the endpoint itself is not rate-limited on
free tier because n rows per filing is small).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .. import MassiveClient, FetchError, today, utcnow_iso


# Text-length ratio above/below which a retained category is flagged as
# "materially changed". A 25% delta on the SEC's typical two-sentence
# category snippet is a real edit, not paraphrase drift.
MATERIAL_CHANGE_PCT = 0.25


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


# ----- HTTP -----

def _fetch_all_risk_factors(client: MassiveClient, ticker: str, sources: _Sources) -> list[dict]:
    """Pull every risk-factor row for `ticker` across all filings on record."""
    path = f"/stocks/filings/vX/risk-factors?ticker={ticker}&limit=50000&sort=filing_date.desc"
    rows: list[dict] = []
    seen_pages = 0
    try:
        for page, fetched_at in client.paginate(path):
            rows.extend(page)
            seen_pages += 1
            if seen_pages == 1:
                sources.record(
                    f"/stocks/filings/vX/risk-factors?ticker={ticker}",
                    fetched_at,
                    f"categorized risk factors for {ticker}",
                )
            # Guardrail: SEC 10-K risk-factor rows are small per name; a
            # runaway loop indicates upstream pagination misbehavior.
            if seen_pages > 20:
                break
    except FetchError:
        return []
    return rows


# ----- Diff logic -----

def _category_key(row: dict) -> tuple[str, str, str]:
    return (
        row.get("primary_category") or "unclassified",
        row.get("secondary_category") or "unclassified",
        row.get("tertiary_category") or "unclassified",
    )


def _humanize(cat: str) -> str:
    return cat.replace("_", " ")


def _material_change(prior: str, current: str) -> tuple[bool, float]:
    p_len = len(prior or "")
    c_len = len(current or "")
    if max(p_len, c_len) == 0:
        return (False, 0.0)
    delta = abs(c_len - p_len) / max(p_len, c_len)
    return (delta >= MATERIAL_CHANGE_PCT, round(delta, 3))


def _diff_filings(current_rows: list[dict], prior_rows: list[dict]) -> dict[str, Any]:
    prior_map: dict[tuple[str, str, str], dict] = {_category_key(r): r for r in prior_rows}
    current_map: dict[tuple[str, str, str], dict] = {_category_key(r): r for r in current_rows}

    added: list[dict] = []
    removed: list[dict] = []
    materially_changed: list[dict] = []
    retained_unchanged = 0

    for key, cur in current_map.items():
        if key not in prior_map:
            added.append({
                "primary_category": key[0],
                "secondary_category": key[1],
                "tertiary_category": key[2],
                "supporting_text": cur.get("supporting_text", ""),
            })
            continue
        prior = prior_map[key]
        changed, delta = _material_change(prior.get("supporting_text", ""), cur.get("supporting_text", ""))
        if changed:
            materially_changed.append({
                "primary_category": key[0],
                "secondary_category": key[1],
                "tertiary_category": key[2],
                "prior_text_length": len(prior.get("supporting_text", "")),
                "current_text_length": len(cur.get("supporting_text", "")),
                "length_delta_pct": delta,
                "current_supporting_text": cur.get("supporting_text", ""),
                "prior_supporting_text": prior.get("supporting_text", ""),
            })
        else:
            retained_unchanged += 1

    for key, prior in prior_map.items():
        if key not in current_map:
            removed.append({
                "primary_category": key[0],
                "secondary_category": key[1],
                "tertiary_category": key[2],
                "supporting_text": prior.get("supporting_text", ""),
            })

    # Group counts by primary_category
    by_primary: dict[str, dict] = defaultdict(lambda: {"added": 0, "removed": 0, "materially_changed": 0})
    for entry in added:
        by_primary[entry["primary_category"]]["added"] += 1
    for entry in removed:
        by_primary[entry["primary_category"]]["removed"] += 1
    for entry in materially_changed:
        by_primary[entry["primary_category"]]["materially_changed"] += 1

    return {
        "added": added,
        "removed": removed,
        "materially_changed": materially_changed,
        "retained_unchanged": retained_unchanged,
        "by_primary_category": dict(by_primary),
    }


# ----- Public API -----

def run(
    ticker: str,
    current_filing_date: str | None = None,
    prior_filing_date: str | None = None,
    client: MassiveClient | None = None,
) -> dict:
    """Diff Item 1A Risk Factors between two 10-K filings for `ticker`.

    Args:
        ticker: single stock ticker.
        current_filing_date: force a specific "current" filing (YYYY-MM-DD).
            Defaults to the most recent filing on record.
        prior_filing_date: force a specific "prior" filing (YYYY-MM-DD).
            Defaults to the second-most-recent filing on record.
        client: reuse an existing MassiveClient.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")

    client = client or MassiveClient()
    sources = _Sources()

    rows = _fetch_all_risk_factors(client, ticker, sources)

    tier_caveats: list[str] = []
    if not rows:
        tier_caveats.append(
            f"No categorized risk factors returned for {ticker}. Massive's coverage may not include this issuer, or the ticker/CIK mapping needs verification."
        )
        return {
            "skill": "risk-factor-delta",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "filings": None,
            "summary": None,
            "changes": None,
            "tier_caveats": tier_caveats,
            "sources": sources.to_list(),
        }

    filings_by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        d = r.get("filing_date")
        if d:
            filings_by_date[d].append(r)
    sorted_dates = sorted(filings_by_date.keys(), reverse=True)

    if current_filing_date is None:
        current_filing_date = sorted_dates[0] if sorted_dates else None
    if prior_filing_date is None:
        prior_filing_date = sorted_dates[1] if len(sorted_dates) >= 2 else None

    if current_filing_date is None:
        raise ValueError(f"no filings on record for {ticker}")

    if prior_filing_date is None:
        tier_caveats.append(
            f"Only one 10-K on record for {ticker} ({current_filing_date}); YoY diff not available. Showing current risk-factor catalog."
        )
        current_rows = filings_by_date.get(current_filing_date, [])
        by_primary: dict[str, int] = defaultdict(int)
        for r in current_rows:
            by_primary[r.get("primary_category") or "unclassified"] += 1
        return {
            "skill": "risk-factor-delta",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "filings": {
                "current": {"filing_date": current_filing_date, "n_categories": len(current_rows)},
                "prior": None,
            },
            "summary": {
                "n_added": None,
                "n_removed": None,
                "n_materially_changed": None,
                "n_retained_unchanged": None,
                "n_current_categories": len(current_rows),
                "n_prior_categories": 0,
                "by_primary_category_current": dict(by_primary),
            },
            "changes": None,
            "tier_caveats": tier_caveats,
            "sources": sources.to_list(),
        }

    current_rows = filings_by_date.get(current_filing_date, [])
    prior_rows = filings_by_date.get(prior_filing_date, [])
    if not current_rows or not prior_rows:
        raise ValueError(
            f"one or both requested filing dates have no rows: "
            f"current={current_filing_date} ({len(current_rows)} rows), "
            f"prior={prior_filing_date} ({len(prior_rows)} rows). "
            f"Available dates: {sorted_dates[:6]}"
        )

    diff = _diff_filings(current_rows, prior_rows)

    largest_new_primary = None
    if diff["added"]:
        counts: dict[str, int] = defaultdict(int)
        for a in diff["added"]:
            counts[a["primary_category"]] += 1
        largest_new_primary = max(counts.items(), key=lambda kv: kv[1])[0]

    tier_caveats.append(
        "Risk-factor categorization is Massive's taxonomy applied per-filing (see the referenced methodology paper). Category shifts reflect real edits and reclassification, not just new prose. Read the supporting_text for confirmation."
    )

    return {
        "skill": "risk-factor-delta",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "filings": {
            "current": {"filing_date": current_filing_date, "n_categories": len(current_rows)},
            "prior": {"filing_date": prior_filing_date, "n_categories": len(prior_rows)},
        },
        "summary": {
            "n_added": len(diff["added"]),
            "n_removed": len(diff["removed"]),
            "n_materially_changed": len(diff["materially_changed"]),
            "n_retained_unchanged": diff["retained_unchanged"],
            "n_current_categories": len(current_rows),
            "n_prior_categories": len(prior_rows),
            "largest_new_primary_category": largest_new_primary,
            "by_primary_category": diff["by_primary_category"],
        },
        "changes": {
            "added": diff["added"],
            "removed": diff["removed"],
            "materially_changed": diff["materially_changed"],
        },
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _truncate(text: str, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tag(row: dict) -> str:
    parts = [_humanize(row["primary_category"]),
             _humanize(row["secondary_category"]),
             _humanize(row["tertiary_category"])]
    return " > ".join(parts)


def render(payload: dict) -> str:
    lines: list[str] = []
    ticker = payload["ticker"]
    filings = payload.get("filings")
    summary = payload.get("summary")
    changes = payload.get("changes")

    if not filings or not filings.get("current"):
        lines.append(f"Risk-factor delta: {ticker}")
        lines.append("")
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    cur = filings["current"]
    prior = filings.get("prior")
    if prior is None:
        lines.append(
            f"Risk-factor catalog: {ticker} ({cur['filing_date']}): "
            f"{summary['n_current_categories']} categorized risks (no prior filing on record)"
        )
        lines.append("")
        lines.append("By primary category:")
        for cat, n in sorted(summary["by_primary_category_current"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {_humanize(cat):<40} {n}")
        if payload.get("tier_caveats"):
            lines.append("")
            lines.append("Caveats:")
            for c in payload["tier_caveats"]:
                lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    lines.append(
        f"Risk-factor delta: {ticker} · {prior['filing_date']} → {cur['filing_date']}"
    )
    lines.append(
        f"Categories: prior {summary['n_prior_categories']} → current {summary['n_current_categories']} · "
        f"+{summary['n_added']} added, -{summary['n_removed']} removed, "
        f"{summary['n_materially_changed']} materially changed, "
        f"{summary['n_retained_unchanged']} retained"
    )
    lines.append("")

    if changes["added"]:
        by_primary: dict[str, list[dict]] = defaultdict(list)
        for a in changes["added"]:
            by_primary[a["primary_category"]].append(a)
        lines.append(f"NEW risk categories ({summary['n_added']})")
        for primary, entries in sorted(by_primary.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  {_humanize(primary)} ({len(entries)}):")
            for entry in entries:
                lines.append(f"    · {_humanize(entry['secondary_category'])} > {_humanize(entry['tertiary_category'])}")
                lines.append(f"      \"{_truncate(entry['supporting_text'])}\"")
        lines.append("")

    if changes["removed"]:
        by_primary_r: dict[str, list[dict]] = defaultdict(list)
        for a in changes["removed"]:
            by_primary_r[a["primary_category"]].append(a)
        lines.append(f"DROPPED risk categories ({summary['n_removed']})")
        for primary, entries in sorted(by_primary_r.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  {_humanize(primary)} ({len(entries)}):")
            for entry in entries:
                lines.append(f"    · {_humanize(entry['secondary_category'])} > {_humanize(entry['tertiary_category'])}")
        lines.append("")

    if changes["materially_changed"]:
        lines.append(f"MATERIALLY CHANGED text ({summary['n_materially_changed']})")
        for entry in sorted(changes["materially_changed"], key=lambda e: -e["length_delta_pct"])[:10]:
            delta_pct = entry["length_delta_pct"] * 100
            direction = "expanded" if entry["current_text_length"] > entry["prior_text_length"] else "contracted"
            lines.append(f"  · {_tag(entry)}")
            lines.append(f"    text {direction} {delta_pct:.0f}% "
                         f"({entry['prior_text_length']} → {entry['current_text_length']} chars)")
            lines.append(f"    now: \"{_truncate(entry['current_supporting_text'])}\"")
        if len(changes["materially_changed"]) > 10:
            lines.append(f"  ... and {len(changes['materially_changed']) - 10} more")
        lines.append("")

    take_parts: list[str] = []
    if summary["n_added"]:
        take_parts.append(
            f"{summary['n_added']} new risk categor{'y' if summary['n_added'] == 1 else 'ies'} added YoY"
            + (f" (concentrated in {_humanize(summary['largest_new_primary_category'])})"
               if summary.get('largest_new_primary_category') else "")
            + "."
        )
    if summary["n_removed"]:
        take_parts.append(
            f"{summary['n_removed']} categor{'y' if summary['n_removed'] == 1 else 'ies'} dropped."
        )
    if summary["n_materially_changed"]:
        take_parts.append(
            f"{summary['n_materially_changed']} retained categor{'y' if summary['n_materially_changed'] == 1 else 'ies'} rewritten (>= 25% length change)."
        )
    if not take_parts:
        take_parts.append("No material changes YoY: same risk-factor lineup, same language.")
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
