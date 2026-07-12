"""
8-k-scanner as an importable library function.

Pulls SEC 8-K disclosures for a single ticker or a watchlist over a
lookback window using Massive's pre-parsed disclosure taxonomy. Groups
rows by accession_number (one 8-K filing carries N disclosure rows,
one per tagged item), sorts most-recent-first, and surfaces high-signal
filings (M&A, going concern, restatements, executive departures) at
the top.

    from quant_garage.skills.eight_k_scanner import run, render
    payload = run("NVDA,RKLB,AAPL", lookback_days=30)

Reads MASSIVE_API_KEY from env. Stocks Basic minimum (filing endpoints
included on every stocks plan).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Iterable

from .. import MassiveClient, FetchError, today, utcnow_iso


# Primary-category buckets ranked by trader/analyst importance. Order
# is used as a stable priority: the FIRST bucket a filing hits by
# primary_category wins the filing's headline label.
SIGNAL_BUCKETS: list[tuple[str, tuple[str, ...]]] = [
    ("M&A / Strategic", ("strategic_transactions",)),
    ("Restatement / Restructuring", ("accounting_and_restatement", "restructuring_and_bankruptcy")),
    ("Material agreement", ("material_agreements",)),
    ("Regulatory / Legal", ("regulatory_actions", "legal_and_regulatory", "litigation")),
    ("Leadership change", ("leadership_and_governance",)),
    ("Capital / Debt", ("capital_and_financing", "debt_and_credit_agreements")),
    ("Earnings / Guidance", ("financial_results",)),
    ("Corporate housekeeping", ("corporate_governance", "shareholder_matters", "auditor_matters")),
    ("Other", ("other",)),
]


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


# ----- HTTP -----

def _fetch_disclosures(
    client: MassiveClient,
    tickers: list[str],
    from_date: str,
    sources: _Sources,
) -> list[dict]:
    tickers_arg = ",".join(tickers)
    path = (
        f"/stocks/filings/8-K/vX/disclosures?tickers.any_of={tickers_arg}"
        f"&filing_date.gte={from_date}"
        f"&limit=1000&sort=filing_date.desc"
    )
    rows: list[dict] = []
    seen_pages = 0
    try:
        for page, fetched_at in client.paginate(path):
            rows.extend(page)
            seen_pages += 1
            if seen_pages == 1:
                sources.record(
                    f"/stocks/filings/8-K/vX/disclosures?tickers.any_of={tickers_arg}&filing_date.gte={from_date}",
                    fetched_at,
                    f"8-K disclosures for {len(tickers)} tickers since {from_date}",
                )
            if seen_pages > 30:
                break
    except FetchError:
        return []
    return rows


# ----- Grouping / signal logic -----

def _humanize(cat: str) -> str:
    return cat.replace("_", " ")


def _headline_bucket(primary_categories: set[str]) -> str:
    for bucket_name, primaries in SIGNAL_BUCKETS:
        for p in primaries:
            if p in primary_categories:
                return bucket_name
    return "Uncategorized"


def _bucket_rank(bucket_name: str) -> int:
    for i, (name, _) in enumerate(SIGNAL_BUCKETS):
        if name == bucket_name:
            return i
    return len(SIGNAL_BUCKETS) + 1


def _group_filings(rows: list[dict]) -> list[dict]:
    """
    Collapse rows into per-accession filings. Each filing carries the
    union of tagged (primary, secondary, tertiary) categories and one
    supporting_text per unique tuple.
    """
    by_accession: dict[str, dict] = {}
    for r in rows:
        acc = r.get("accession_number")
        if not acc:
            continue
        f = by_accession.setdefault(acc, {
            "accession_number": acc,
            "cik": r.get("cik"),
            "tickers": tuple(r.get("tickers") or []),
            "filing_date": r.get("filing_date"),
            "filing_url": r.get("filing_url"),
            "categories": [],
            "_seen_tuples": set(),
        })
        key = (
            r.get("primary_category") or "unclassified",
            r.get("secondary_category") or "unclassified",
            r.get("tertiary_category") or "unclassified",
        )
        if key in f["_seen_tuples"]:
            continue
        f["_seen_tuples"].add(key)
        f["categories"].append({
            "primary_category": key[0],
            "secondary_category": key[1],
            "tertiary_category": key[2],
            "supporting_text": r.get("supporting_text", ""),
        })
    filings = []
    for f in by_accession.values():
        primary_set = {c["primary_category"] for c in f["categories"]}
        bucket = _headline_bucket(primary_set)
        filings.append({
            "accession_number": f["accession_number"],
            "cik": f["cik"],
            "tickers": list(f["tickers"]),
            "filing_date": f["filing_date"],
            "filing_url": f["filing_url"],
            "categories": f["categories"],
            "primary_categories": sorted(primary_set),
            "headline_bucket": bucket,
        })
    return filings


# ----- Public API -----

def run(
    tickers: str | Iterable[str],
    lookback_days: int = 30,
    categories: str | Iterable[str] | None = None,
    client: MassiveClient | None = None,
) -> dict:
    """Scan 8-K filings across a ticker or watchlist.

    Args:
        tickers: comma-separated string or iterable of tickers.
        lookback_days: calendar-day window back from today. Default 30.
        categories: optional filter on primary_category (comma-separated
            string or iterable). Skips filings that don't touch any of
            the given primaries. Default None (no filter).
        client: reuse an existing MassiveClient.
    """
    if isinstance(tickers, str):
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    else:
        ticker_list = [t.strip().upper() for t in tickers if t and t.strip()]
    ticker_list = list(dict.fromkeys(ticker_list))
    if not ticker_list:
        raise ValueError("at least one ticker required")
    if lookback_days < 1:
        raise ValueError("lookback_days must be positive")

    if categories is None:
        category_filter: set[str] | None = None
    elif isinstance(categories, str):
        category_filter = {c.strip() for c in categories.split(",") if c.strip()}
    else:
        category_filter = {c.strip() for c in categories if c and c.strip()}

    client = client or MassiveClient()
    sources = _Sources()

    from_date = (today() - timedelta(days=lookback_days)).isoformat()
    rows = _fetch_disclosures(client, ticker_list, from_date, sources)

    tier_caveats: list[str] = []
    filings = _group_filings(rows)

    if category_filter:
        filings = [
            f for f in filings
            if set(f["primary_categories"]) & category_filter
        ]

    filings.sort(
        key=lambda f: (_bucket_rank(f["headline_bucket"]), f["filing_date"] or ""),
    )
    # Second pass: within a bucket, most-recent first
    filings.sort(
        key=lambda f: (
            _bucket_rank(f["headline_bucket"]),
            -(int(f["filing_date"].replace("-", "")) if f["filing_date"] else 0),
        ),
    )

    by_ticker: dict[str, dict] = defaultdict(lambda: {"n_filings": 0, "buckets": defaultdict(int)})
    by_primary_category: dict[str, int] = defaultdict(int)
    by_bucket: dict[str, int] = defaultdict(int)
    for f in filings:
        for t in f["tickers"]:
            if t not in ticker_list:
                continue
            by_ticker[t]["n_filings"] += 1
            by_ticker[t]["buckets"][f["headline_bucket"]] += 1
        for p in f["primary_categories"]:
            by_primary_category[p] += 1
        by_bucket[f["headline_bucket"]] += 1

    by_ticker_out = {
        t: {"n_filings": v["n_filings"], "buckets": dict(v["buckets"])}
        for t, v in by_ticker.items()
    }

    tier_caveats.append(
        "8-K disclosures come from Massive's pre-parsed taxonomy (primary/secondary/tertiary). "
        "Category shifts reflect real edits and reclassification; read the supporting_text to confirm."
    )
    if not rows:
        tier_caveats.append(
            f"No 8-K disclosures returned for {', '.join(ticker_list)} in the last {lookback_days} days."
        )
    silent_tickers = [t for t in ticker_list if t not in by_ticker_out]
    if silent_tickers and rows:
        tier_caveats.append(
            f"No 8-K activity for {', '.join(silent_tickers)} in the window."
        )

    return {
        "skill": "8-k-scanner",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "tickers": ticker_list,
        "lookback_days": lookback_days,
        "from_date": from_date,
        "category_filter": sorted(category_filter) if category_filter else None,
        "n_rows": len(rows),
        "n_filings": len(filings),
        "filings": filings,
        "by_ticker": by_ticker_out,
        "by_primary_category": dict(by_primary_category),
        "by_bucket": dict(by_bucket),
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _truncate(text: str, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render(payload: dict) -> str:
    lines: list[str] = []
    tickers = payload["tickers"]
    n_filings = payload["n_filings"]
    n_rows = payload["n_rows"]
    lookback = payload["lookback_days"]

    tickers_str = ",".join(tickers) if len(tickers) <= 8 else f"{','.join(tickers[:8])}...+{len(tickers) - 8}"
    lines.append(
        f"8-K scan: {tickers_str} · {lookback}d lookback · "
        f"{n_filings} filing{'s' if n_filings != 1 else ''} ({n_rows} disclosure rows)"
    )
    if payload.get("category_filter"):
        lines.append(f"Filter: primary_category in [{', '.join(payload['category_filter'])}]")

    by_bucket = payload.get("by_bucket") or {}
    if by_bucket:
        order = [name for name, _ in SIGNAL_BUCKETS if name in by_bucket]
        summary = " · ".join(f"{name}: {by_bucket[name]}" for name in order)
        lines.append(f"By signal: {summary}")
    lines.append("")

    if not payload["filings"]:
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    current_bucket = None
    for f in payload["filings"]:
        bucket = f["headline_bucket"]
        if bucket != current_bucket:
            lines.append(f"[{bucket}]")
            current_bucket = bucket
        tkr = ",".join(f["tickers"][:3]) or f.get("cik", "?")
        lines.append(f"  {f['filing_date']} · {tkr} · accession {f['accession_number']}")
        for c in f["categories"]:
            lines.append(
                f"    · {_humanize(c['primary_category'])} > "
                f"{_humanize(c['secondary_category'])} > "
                f"{_humanize(c['tertiary_category'])}"
            )
            if c.get("supporting_text"):
                lines.append(f"      \"{_truncate(c['supporting_text'])}\"")
        if f.get("filing_url"):
            lines.append(f"      link: {f['filing_url']}")
        lines.append("")

    take_parts: list[str] = []
    if by_bucket.get("M&A / Strategic"):
        n = by_bucket["M&A / Strategic"]
        take_parts.append(
            f"{n} strategic-transaction filing{'s' if n != 1 else ''} in the window "
            f"(top of the report)."
        )
    if by_bucket.get("Restatement / Restructuring"):
        take_parts.append("Restatement/restructuring activity present; read the top block first.")
    if by_bucket.get("Leadership change"):
        n = by_bucket["Leadership change"]
        take_parts.append(f"{n} leadership-change filing{'s' if n != 1 else ''}.")
    if not take_parts:
        take_parts.append(
            "No M&A, restatement, or leadership signal in the window. "
            "The scan surfaced routine 8-K items only."
        )
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
