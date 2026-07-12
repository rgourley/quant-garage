"""
filing-sentiment as an importable library function.

Scores 10-K narrative sections (Business, Risk Factors) for a ticker
using the Loughran-McDonald finance sentiment dictionary and reports
year-over-year tone shifts. Answers "did management's language get
more defensive this year?"

    from quant_garage.skills.filing_sentiment import run, render
    payload = run("AAPL")

Reads MASSIVE_API_KEY from env. Stocks Basic minimum.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .. import MassiveClient, FetchError, today, utcnow_iso
from ..data.loughran_mcdonald import CATEGORY_SETS


# Sections in the 10-K sections endpoint we care about. Others are
# skipped (e.g., cover pages, exhibits).
SECTIONS_OF_INTEREST = ("business", "risk_factors")


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


# ----- HTTP -----

def _fetch_sections(client: MassiveClient, ticker: str, sources: _Sources) -> list[dict]:
    # Note: this endpoint only allows sort=period_end.
    path = (
        f"/stocks/filings/10-K/vX/sections?ticker={ticker}"
        f"&limit=100&sort=period_end.desc"
    )
    rows: list[dict] = []
    try:
        for page, fetched_at in client.paginate(path):
            rows.extend(page)
            sources.record(
                f"/stocks/filings/10-K/vX/sections?ticker={ticker}",
                fetched_at,
                f"10-K sections for {ticker}",
            )
            if len(rows) >= 60:
                break
    except FetchError:
        return []
    return rows


# ----- Scoring -----

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-']+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def _score_text(text: str) -> dict[str, Any]:
    tokens = _tokenize(text)
    n = len(tokens)
    counts: dict[str, int] = {cat: 0 for cat in CATEGORY_SETS}
    if n == 0:
        return {"n_tokens": 0, "counts": counts, "rates_per_10k": {cat: 0.0 for cat in counts}}
    for tok in tokens:
        for cat, wordset in CATEGORY_SETS.items():
            if tok in wordset:
                counts[cat] += 1
    rates = {cat: round(counts[cat] / n * 10_000, 2) for cat in counts}
    return {"n_tokens": n, "counts": counts, "rates_per_10k": rates}


def _tone_shift_label(delta_rate: float, absolute_rate_current: float) -> str:
    """
    Classify a per-10k-word rate shift as noticeable / material / dramatic.
    Absolute rate acts as a floor: a 20% relative shift on a rate near zero
    is noise.
    """
    if absolute_rate_current < 10:
        return "n/a"
    ratio = abs(delta_rate) / max(absolute_rate_current, 1)
    if ratio < 0.10:
        return "flat"
    if ratio < 0.25:
        return "noticeable"
    if ratio < 0.50:
        return "material"
    return "dramatic"


# ----- Public API -----

def run(
    ticker: str,
    current_filing_date: str | None = None,
    prior_filing_date: str | None = None,
    client: MassiveClient | None = None,
) -> dict:
    """Score 10-K narrative sections for `ticker` and report YoY tone shifts.

    Args:
        ticker: single stock ticker.
        current_filing_date: pin a specific "current" filing (YYYY-MM-DD).
            Default: most recent 10-K on record.
        prior_filing_date: pin a specific "prior" filing. Default: second
            most recent.
        client: reuse an existing MassiveClient.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker required")

    client = client or MassiveClient()
    sources = _Sources()
    rows = _fetch_sections(client, ticker, sources)

    tier_caveats: list[str] = []
    if not rows:
        tier_caveats.append(
            f"No 10-K sections returned for {ticker}. Massive coverage may be incomplete for this issuer."
        )
        return {
            "skill": "filing-sentiment",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "filings": None,
            "sections": None,
            "yoy_deltas": None,
            "tier_caveats": tier_caveats,
            "sources": sources.to_list(),
        }

    # Group by filing_date (falls back to period_end) so we can pick
    # current vs prior. The API returns both fields per row.
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        d = r.get("filing_date") or r.get("period_end")
        if d:
            by_date[d].append(r)
    sorted_dates = sorted(by_date.keys(), reverse=True)

    if current_filing_date is None:
        current_filing_date = sorted_dates[0] if sorted_dates else None
    if prior_filing_date is None:
        prior_filing_date = sorted_dates[1] if len(sorted_dates) >= 2 else None

    if current_filing_date is None:
        raise ValueError(f"no 10-K sections on record for {ticker}")

    def _score_filing(date: str) -> dict[str, Any]:
        section_rows = by_date.get(date, [])
        by_section: dict[str, dict] = {}
        for row in section_rows:
            sec = row.get("section")
            if sec not in SECTIONS_OF_INTEREST:
                continue
            text = row.get("content") or row.get("text") or ""
            by_section[sec] = _score_text(text)
        return {"filing_date": date, "sections": by_section}

    current = _score_filing(current_filing_date)
    prior = _score_filing(prior_filing_date) if prior_filing_date else None

    yoy_deltas: dict[str, Any] | None = None
    if prior is not None:
        yoy_deltas = {}
        for sec in SECTIONS_OF_INTEREST:
            cur_sec = current["sections"].get(sec)
            prior_sec = prior["sections"].get(sec)
            if not cur_sec or not prior_sec:
                continue
            deltas: dict[str, dict] = {}
            for cat in CATEGORY_SETS:
                cur_rate = cur_sec["rates_per_10k"][cat]
                prior_rate = prior_sec["rates_per_10k"][cat]
                delta = round(cur_rate - prior_rate, 2)
                label = _tone_shift_label(delta, cur_rate)
                deltas[cat] = {
                    "prior_rate_per_10k": prior_rate,
                    "current_rate_per_10k": cur_rate,
                    "delta_per_10k": delta,
                    "delta_pct": (round(delta / prior_rate * 100, 1)
                                  if prior_rate > 0 else None),
                    "label": label,
                }
            yoy_deltas[sec] = {
                "prior_n_tokens": prior_sec["n_tokens"],
                "current_n_tokens": cur_sec["n_tokens"],
                "length_delta_pct": (round((cur_sec["n_tokens"] - prior_sec["n_tokens"])
                                           / max(prior_sec["n_tokens"], 1) * 100, 1)
                                     if prior_sec["n_tokens"] > 0 else None),
                "categories": deltas,
            }

    tier_caveats.append(
        "Loughran-McDonald finance sentiment dictionary applied. Bag-of-words: "
        "captures aggregate tone shifts, not clause-level meaning. Read the "
        "sections themselves to confirm any 'dramatic' shift the score flags."
    )
    if prior is None:
        tier_caveats.append(
            f"Only one 10-K on record for {ticker} ({current_filing_date}). "
            "YoY comparison not available; showing current scores only."
        )

    return {
        "skill": "filing-sentiment",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "filings": {
            "current": current["filing_date"],
            "prior": prior["filing_date"] if prior else None,
        },
        "sections": {
            "current": current["sections"],
            "prior": prior["sections"] if prior else None,
        },
        "yoy_deltas": yoy_deltas,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

_CATEGORY_ORDER = ("negative", "uncertain", "litigious", "constraining", "modal_weak", "modal_strong")


def _fmt_rate(rate: float) -> str:
    return f"{rate:>5.1f}"


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "  n/a"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:>4.0f}%"


def _tag(label: str, delta: float) -> str:
    if label == "n/a":
        return "n/a"
    if label == "flat":
        return "flat"
    direction = "up" if delta > 0 else "down"
    return f"{label} {direction}"


def render(payload: dict) -> str:
    ticker = payload["ticker"]
    lines: list[str] = []

    filings = payload.get("filings")
    if not filings or not filings.get("current"):
        lines.append(f"Filing sentiment: {ticker}")
        lines.append("")
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    cur_date = filings["current"]
    prior_date = filings.get("prior")
    sections_current = payload["sections"]["current"]

    if not prior_date:
        lines.append(f"Filing sentiment: {ticker} · {cur_date} (only filing on record)")
        lines.append("")
        for sec in SECTIONS_OF_INTEREST:
            data = sections_current.get(sec)
            if not data:
                continue
            lines.append(f"[{sec.replace('_', ' ')}] n_tokens={data['n_tokens']:,}")
            for cat in _CATEGORY_ORDER:
                lines.append(f"  {cat:<14} {_fmt_rate(data['rates_per_10k'][cat])} per 10k words")
            lines.append("")
    else:
        lines.append(f"Filing sentiment: {ticker} · {prior_date} → {cur_date}")
        lines.append("")
        yoy = payload.get("yoy_deltas") or {}

        for sec in SECTIONS_OF_INTEREST:
            sec_yoy = yoy.get(sec)
            if not sec_yoy:
                continue
            length_pct = sec_yoy.get("length_delta_pct")
            len_str = f"length {length_pct:+.0f}%" if length_pct is not None else "length n/a"
            lines.append(
                f"[{sec.replace('_', ' ')}] "
                f"prior {sec_yoy['prior_n_tokens']:,} → current {sec_yoy['current_n_tokens']:,} tokens ({len_str})"
            )
            lines.append(
                f"  {'Category':<14} {'Prior':>7}  {'Current':>7}  {'Δ':>6}  {'Δ%':>6}  {'Shift':<20}"
            )
            for cat in _CATEGORY_ORDER:
                d = sec_yoy["categories"].get(cat)
                if not d:
                    continue
                lines.append(
                    f"  {cat:<14} "
                    f"{d['prior_rate_per_10k']:>7.1f}  "
                    f"{d['current_rate_per_10k']:>7.1f}  "
                    f"{d['delta_per_10k']:>+6.1f}  "
                    f"{_fmt_pct(d['delta_pct'])}  "
                    f"{_tag(d['label'], d['delta_per_10k']):<20}"
                )
            lines.append("")

        # Take: focus on the most notable shifts
        take_parts: list[str] = []
        material_shifts: list[str] = []
        for sec in SECTIONS_OF_INTEREST:
            sec_yoy = yoy.get(sec) or {}
            for cat, d in (sec_yoy.get("categories") or {}).items():
                if d["label"] in ("material", "dramatic"):
                    direction = "up" if d["delta_per_10k"] > 0 else "down"
                    material_shifts.append(
                        f"{sec.replace('_', ' ')} {cat} {direction} "
                        f"{abs(d['delta_pct']):.0f}%"
                    )
        if material_shifts:
            take_parts.append(
                f"Material tone shifts detected: {'; '.join(material_shifts[:6])}."
            )
            if len(material_shifts) > 6:
                take_parts.append(f"+{len(material_shifts) - 6} more in the JSON.")
        else:
            take_parts.append(
                "No material tone shifts across sections. Management's language "
                "held steady year-over-year."
            )
        lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
