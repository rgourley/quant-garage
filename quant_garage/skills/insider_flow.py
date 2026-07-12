"""
insider-flow as an importable library function.

Pulls SEC Form 4 filings for a ticker over a caller-supplied window,
classifies each transaction by SEC transaction code and Rule 10b5-1
status, separates the signal (conviction buys, discretionary sales)
from the noise (grants, exercises, tax withholding, gifts), detects
cluster buys, and emits a sentiment label backed by the underlying
dollar flow.

    from quant_garage.skills.insider_flow import run, render
    payload = run("NVDA", lookback_days=180)

Reads MASSIVE_API_KEY from env. Stocks Basic minimum (filing endpoints
included on every stocks plan).

Signal taxonomy (SEC transaction codes 16A + Massive's aff_10b5_one flag):

  Conviction buy: code P (open-market purchase, non-derivative).
    The clean bullish signal. Insiders rarely open-market buy
    unless they see something the market doesn't.

  Discretionary sale: code S with aff_10b5_one = false.
    A real, on-the-fly decision to sell. Noisier than a P buy
    (diversification, taxes, personal liquidity) but still
    informative when clustered or large.

  Scheduled sale: code S with aff_10b5_one = true.
    Sales under a pre-committed Rule 10b5-1 trading plan. Announced
    months in advance. Near-neutral signal.

  Routine comp: A (grant), M (derivative exercise), F (tax withholding).
    Non-informative. Reported for transparency, not for signal.

  Non-informative: G (gift), I/J (other), X/C (derivative exercise
    mechanics), D (return to issuer), K (equity swap), Z (trust
    deposit/withdrawal), W (by will), V (voluntary), L (small), H/E
    (derivative expiry), O (exercise of out-of-the-money derivative).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Iterable

from .. import MassiveClient, FetchError, today, utcnow_iso


CLUSTER_WINDOW_DAYS = 14
CLUSTER_MIN_INSIDERS = 2
CLUSTER_MIN_DOLLAR = 100_000.0

# Sentiment score thresholds keyed on net conviction dollars
# (buys minus discretionary sales). Round numbers, not
# statistical thresholds. Tunable at module level.
_SENTIMENT_STRONG_BULL_USD = 250_000.0
_SENTIMENT_BULL_USD = 50_000.0
_SENTIMENT_BEAR_USD = -250_000.0
_SENTIMENT_STRONG_BEAR_USD = -1_000_000.0


TRANSACTION_LABELS = {
    "P": "Open-market purchase",
    "S": "Open-market sale",
    "A": "Grant/award",
    "M": "Derivative exercise",
    "F": "Tax withholding",
    "G": "Gift",
    "D": "Return to issuer",
    "I": "Discretionary (other)",
    "J": "Other",
    "V": "Voluntarily reported",
    "X": "Exercise of derivative",
    "Z": "Trust deposit/withdrawal",
    "L": "Small acquisition",
    "W": "By will/laws of descent",
    "C": "Conversion of derivative",
    "E": "Expiration of short derivative",
    "H": "Expiration of long derivative",
    "O": "Exercise of out-of-money derivative",
    "K": "Equity swap",
}


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


# ----- HTTP -----

def _fetch_form4(
    client: MassiveClient, ticker: str, from_date: str, sources: _Sources,
) -> list[dict]:
    """Pull every Form 4 row for `ticker` from `from_date` forward."""
    path = (
        f"/stocks/filings/vX/form-4?tickers={ticker}"
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
                    f"/stocks/filings/vX/form-4?tickers={ticker}&filing_date.gte={from_date}",
                    fetched_at,
                    f"Form 4 filings for {ticker} since {from_date}",
                )
            # Guardrail: even the busiest names rarely file more than a
            # few hundred rows in six months.
            if seen_pages > 20:
                break
    except FetchError:
        return []
    return rows


# ----- Classification -----

def _role_label(row: dict) -> str:
    if row.get("is_ten_percent_owner"):
        return "10% owner"
    if row.get("is_officer"):
        title = (row.get("officer_title") or "").strip()
        return f"Officer ({title})" if title else "Officer"
    if row.get("is_director"):
        return "Director"
    if row.get("is_other"):
        return "Other"
    return "Unknown"


def _classify(row: dict) -> str:
    """
    Returns one of: 'conviction_buy', 'discretionary_sale',
    'scheduled_sale', 'routine_comp', 'non_informative'.
    """
    if row.get("record_type") != "transaction":
        return "non_informative"
    code = (row.get("transaction_code") or "").strip().upper()
    security_type = (row.get("security_type") or "").strip().lower()

    if code == "P" and security_type == "non_derivative":
        return "conviction_buy"
    if code == "S" and security_type == "non_derivative":
        return "scheduled_sale" if row.get("aff_10b5_one") else "discretionary_sale"
    if code in ("A", "M", "F"):
        return "routine_comp"
    return "non_informative"


def _summarize_transactions(rows: Iterable[dict]) -> dict[str, Any]:
    by_category: dict[str, list[dict]] = {
        "conviction_buy": [],
        "discretionary_sale": [],
        "scheduled_sale": [],
        "routine_comp": [],
        "non_informative": [],
    }
    for r in rows:
        cat = _classify(r)
        by_category[cat].append(r)
    return by_category


def _detect_cluster_buys(conviction_buys: list[dict]) -> list[dict]:
    """
    Find 14-day windows where >= 2 distinct insiders made open-market
    purchases summing to at least $100k. Returns one entry per detected
    cluster, sorted by total dollar volume descending.
    """
    if len(conviction_buys) < CLUSTER_MIN_INSIDERS:
        return []
    sorted_buys = sorted(conviction_buys, key=lambda r: r.get("transaction_date") or "")
    from datetime import date

    def _parse(d: str) -> date:
        return date.fromisoformat(d)

    clusters: list[dict] = []
    i = 0
    while i < len(sorted_buys):
        window_start_dt = _parse(sorted_buys[i]["transaction_date"])
        window_end_dt = window_start_dt + timedelta(days=CLUSTER_WINDOW_DAYS)
        window_rows = []
        for r in sorted_buys[i:]:
            d = _parse(r["transaction_date"])
            if d <= window_end_dt:
                window_rows.append(r)
            else:
                break
        insiders = {r.get("owner_cik") or r.get("owner_name") for r in window_rows}
        total_usd = sum((r.get("transaction_value") or 0.0) for r in window_rows)
        if len(insiders) >= CLUSTER_MIN_INSIDERS and total_usd >= CLUSTER_MIN_DOLLAR:
            insider_names = sorted({r.get("owner_name") or "?" for r in window_rows})
            clusters.append({
                "window_start": sorted_buys[i]["transaction_date"],
                "window_end": max(r["transaction_date"] for r in window_rows),
                "n_insiders": len(insiders),
                "n_transactions": len(window_rows),
                "total_dollars": round(total_usd, 2),
                "insider_names": insider_names,
            })
            i += len(window_rows)
        else:
            i += 1
    clusters.sort(key=lambda c: c["total_dollars"], reverse=True)
    return clusters


def _sentiment_label(net_conviction_usd: float, cluster_count: int) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if cluster_count > 0 and net_conviction_usd > 0:
        reasons.append(f"{cluster_count} cluster buy(s) detected")
        reasons.append(f"net conviction flow +${net_conviction_usd:,.0f}")
        return ("strong_bullish", reasons)
    if net_conviction_usd >= _SENTIMENT_STRONG_BULL_USD:
        reasons.append(f"net conviction flow +${net_conviction_usd:,.0f}")
        return ("strong_bullish", reasons)
    if net_conviction_usd >= _SENTIMENT_BULL_USD:
        reasons.append(f"net conviction flow +${net_conviction_usd:,.0f}")
        return ("bullish", reasons)
    if net_conviction_usd <= _SENTIMENT_STRONG_BEAR_USD:
        reasons.append(f"net conviction flow -${abs(net_conviction_usd):,.0f}")
        return ("strong_bearish", reasons)
    if net_conviction_usd <= _SENTIMENT_BEAR_USD:
        reasons.append(f"net conviction flow -${abs(net_conviction_usd):,.0f}")
        return ("bearish", reasons)
    reasons.append("no meaningful discretionary buys or sales")
    return ("neutral", reasons)


# ----- Public API -----

def _is_pure_director(row: dict) -> bool:
    """
    True when the reporter is a director with no operator hat.
    Excludes CEOs/executives who also sit on the board (they carry
    operator signal).
    """
    return bool(
        row.get("is_director")
        and not row.get("is_officer")
        and not row.get("is_ten_percent_owner")
    )


def run(
    ticker: str,
    lookback_days: int = 180,
    exclude_directors: bool = False,
    client: MassiveClient | None = None,
) -> dict:
    """Aggregate insider Form 4 activity for `ticker` over the lookback window.

    Args:
        ticker: single stock ticker.
        lookback_days: calendar-day window back from today. Default 180.
        exclude_directors: drop pure-director rows (is_director AND NOT
            is_officer AND NOT is_ten_percent_owner). Useful for names
            with large VC/PE board reps unwinding a fund position,
            which structurally look bearish but carry no operator
            signal. Default False.
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
    raw_rows = _fetch_form4(client, ticker, from_date, sources)

    if exclude_directors:
        n_pure_director_rows = sum(1 for r in raw_rows if _is_pure_director(r))
        rows = [r for r in raw_rows if not _is_pure_director(r)]
    else:
        n_pure_director_rows = 0
        rows = raw_rows

    tier_caveats: list[str] = []

    if not rows:
        if exclude_directors and raw_rows:
            tier_caveats.append(
                f"All {len(raw_rows)} Form 4 rows for {ticker} were pure-director filings and were excluded by --exclude-directors."
            )
        else:
            tier_caveats.append(
                f"No Form 4 filings returned for {ticker} in the last {lookback_days} days."
            )
        return {
            "skill": "insider-flow",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "ticker": ticker,
            "lookback_days": lookback_days,
            "from_date": from_date,
            "exclude_directors": bool(exclude_directors),
            "n_pure_director_rows_excluded": n_pure_director_rows,
            "n_rows": 0,
            "summary": None,
            "clusters": [],
            "notable_buys": [],
            "notable_sales": [],
            "by_role": {},
            "tier_caveats": tier_caveats,
            "sources": sources.to_list(),
        }

    by_cat = _summarize_transactions(rows)

    def _dollars(rows_in: list[dict]) -> float:
        return sum((r.get("transaction_value") or 0.0) for r in rows_in)

    buy_usd = _dollars(by_cat["conviction_buy"])
    disc_sale_usd = _dollars(by_cat["discretionary_sale"])
    sched_sale_usd = _dollars(by_cat["scheduled_sale"])
    net_conviction = buy_usd - disc_sale_usd

    clusters = _detect_cluster_buys(by_cat["conviction_buy"])

    def _fmt_txn(r: dict) -> dict:
        return {
            "owner_name": r.get("owner_name"),
            "role": _role_label(r),
            "transaction_date": r.get("transaction_date"),
            "transaction_code": r.get("transaction_code"),
            "shares": r.get("transaction_shares"),
            "price_per_share": r.get("transaction_price_per_share"),
            "dollar_value": r.get("transaction_value"),
            "aff_10b5_one": r.get("aff_10b5_one"),
            "filing_url": r.get("filing_url"),
        }

    notable_buys = sorted(
        by_cat["conviction_buy"],
        key=lambda r: (r.get("transaction_value") or 0.0),
        reverse=True,
    )[:5]
    notable_sales = sorted(
        by_cat["discretionary_sale"],
        key=lambda r: (r.get("transaction_value") or 0.0),
        reverse=True,
    )[:5]

    by_role: dict[str, dict[str, float]] = defaultdict(lambda: {
        "conviction_buy_usd": 0.0, "discretionary_sale_usd": 0.0,
        "scheduled_sale_usd": 0.0, "n_transactions": 0,
    })
    for r in by_cat["conviction_buy"] + by_cat["discretionary_sale"] + by_cat["scheduled_sale"]:
        role = _role_label(r)
        by_role[role]["n_transactions"] += 1
        cat = _classify(r)
        by_role[role][f"{cat}_usd"] += (r.get("transaction_value") or 0.0)
    by_role_out = {role: {k: round(v, 2) if isinstance(v, float) else v
                          for k, v in vals.items()}
                   for role, vals in by_role.items()}

    sentiment, reasons = _sentiment_label(net_conviction, len(clusters))

    summary = {
        "sentiment": sentiment,
        "sentiment_reasons": reasons,
        "conviction_buy_dollars": round(buy_usd, 2),
        "discretionary_sale_dollars": round(disc_sale_usd, 2),
        "scheduled_sale_dollars": round(sched_sale_usd, 2),
        "net_conviction_dollars": round(net_conviction, 2),
        "n_conviction_buys": len(by_cat["conviction_buy"]),
        "n_discretionary_sales": len(by_cat["discretionary_sale"]),
        "n_scheduled_sales": len(by_cat["scheduled_sale"]),
        "n_routine_comp": len(by_cat["routine_comp"]),
        "n_non_informative": len(by_cat["non_informative"]),
        "n_cluster_buys": len(clusters),
    }

    tier_caveats.append(
        "Form 4 is filed within 2 business days of the transaction; insider decisions are days-to-weeks fresh, not real-time."
    )
    tier_caveats.append(
        "Discretionary sales (non-10b5-1) are noisier than open-market buys: insiders sell for diversification and taxes, not always because they see downside. Buys are the cleaner signal."
    )
    if summary["n_scheduled_sales"] > 0:
        tier_caveats.append(
            f"{summary['n_scheduled_sales']} scheduled sale(s) under Rule 10b5-1 filtered out of sentiment (pre-committed, near-neutral)."
        )
    if exclude_directors and n_pure_director_rows > 0:
        tier_caveats.append(
            f"{n_pure_director_rows} pure-director row(s) excluded via --exclude-directors "
            "(directors with no operator role; useful when a VC/PE board rep is unwinding)."
        )

    return {
        "skill": "insider-flow",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "lookback_days": lookback_days,
        "from_date": from_date,
        "exclude_directors": bool(exclude_directors),
        "n_pure_director_rows_excluded": n_pure_director_rows,
        "n_rows": len(rows),
        "summary": summary,
        "clusters": clusters,
        "notable_buys": [_fmt_txn(r) for r in notable_buys],
        "notable_sales": [_fmt_txn(r) for r in notable_sales],
        "by_role": by_role_out,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _fmt_usd(x: float) -> str:
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    absx = abs(x)
    if absx >= 1_000_000:
        return f"{sign}${absx / 1_000_000:.2f}M"
    if absx >= 1_000:
        return f"{sign}${absx / 1_000:.1f}k"
    return f"{sign}${absx:,.0f}"


def _fmt_sentiment(label: str) -> str:
    return {
        "strong_bullish": "STRONG BULLISH",
        "bullish": "Bullish",
        "neutral": "Neutral",
        "bearish": "Bearish",
        "strong_bearish": "STRONG BEARISH",
    }.get(label, label)


def render(payload: dict) -> str:
    lines: list[str] = []
    ticker = payload["ticker"]
    lookback = payload["lookback_days"]
    n_rows = payload["n_rows"]

    summary = payload.get("summary")
    if summary is None or n_rows == 0:
        lines.append(f"Insider flow: {ticker} · {lookback}-day lookback · {n_rows} Form 4 rows")
        lines.append("")
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    tail = ""
    if payload.get("exclude_directors"):
        excluded = payload.get("n_pure_director_rows_excluded") or 0
        tail = f" · directors excluded ({excluded} row{'s' if excluded != 1 else ''})"
    lines.append(
        f"Insider flow: {ticker} · {lookback}-day lookback · {n_rows} Form 4 rows{tail}"
    )
    lines.append(
        f"Sentiment: {_fmt_sentiment(summary['sentiment'])} "
        f"(net conviction {_fmt_usd(summary['net_conviction_dollars'])})"
    )
    lines.append("")

    lines.append("Transaction flow")
    lines.append(
        f"  Conviction buys (P):        {summary['n_conviction_buys']:>3} txns  "
        f"{_fmt_usd(summary['conviction_buy_dollars']):>10}"
    )
    lines.append(
        f"  Discretionary sales (S):    {summary['n_discretionary_sales']:>3} txns  "
        f"{_fmt_usd(summary['discretionary_sale_dollars']):>10}"
    )
    lines.append(
        f"  Scheduled sales (10b5-1):   {summary['n_scheduled_sales']:>3} txns  "
        f"{_fmt_usd(summary['scheduled_sale_dollars']):>10}  (filtered out)"
    )
    lines.append(
        f"  Routine comp (A/M/F):       {summary['n_routine_comp']:>3} txns  "
        f"(grants + exercises)"
    )
    lines.append("")

    clusters = payload.get("clusters") or []
    if clusters:
        lines.append(f"CLUSTER BUYS ({len(clusters)})")
        for c in clusters[:3]:
            names = ", ".join(c["insider_names"][:4])
            more = f" +{len(c['insider_names']) - 4}" if len(c["insider_names"]) > 4 else ""
            lines.append(
                f"  · {c['window_start']} → {c['window_end']}: "
                f"{c['n_insiders']} insiders, "
                f"{c['n_transactions']} txns, {_fmt_usd(c['total_dollars'])}"
            )
            lines.append(f"    {names}{more}")
        lines.append("")

    notable_buys = payload.get("notable_buys") or []
    if notable_buys:
        lines.append("Notable open-market buys")
        for b in notable_buys:
            lines.append(
                f"  · {b['transaction_date']} · {b['owner_name']} ({b['role']}) · "
                f"{int(b['shares']):>7,} sh @ ${b['price_per_share']:.2f} = "
                f"{_fmt_usd(b['dollar_value'])}"
            )
        lines.append("")

    notable_sales = payload.get("notable_sales") or []
    if notable_sales:
        lines.append("Notable discretionary sales")
        for s in notable_sales:
            lines.append(
                f"  · {s['transaction_date']} · {s['owner_name']} ({s['role']}) · "
                f"{int(s['shares']):>7,} sh @ ${s['price_per_share']:.2f} = "
                f"{_fmt_usd(s['dollar_value'])}"
            )
        lines.append("")

    by_role = payload.get("by_role") or {}
    if by_role:
        lines.append("By role")
        for role, vals in sorted(by_role.items(), key=lambda kv: kv[0]):
            net = vals.get("conviction_buy_usd", 0.0) - vals.get("discretionary_sale_usd", 0.0)
            lines.append(
                f"  · {role:<40} buys {_fmt_usd(vals.get('conviction_buy_usd', 0.0)):>9}  "
                f"sales {_fmt_usd(vals.get('discretionary_sale_usd', 0.0)):>9}  "
                f"net {_fmt_usd(net):>9}"
            )
        lines.append("")

    take_parts: list[str] = []
    if summary["sentiment"].endswith("bullish"):
        take_parts.append(
            f"Insider read is {_fmt_sentiment(summary['sentiment'])}: "
            f"{summary['n_conviction_buys']} open-market buy(s) worth "
            f"{_fmt_usd(summary['conviction_buy_dollars'])} vs "
            f"{_fmt_usd(summary['discretionary_sale_dollars'])} discretionary sales."
        )
    elif summary["sentiment"].endswith("bearish"):
        take_parts.append(
            f"Insider read is {_fmt_sentiment(summary['sentiment'])}: "
            f"discretionary sales of {_fmt_usd(summary['discretionary_sale_dollars'])} "
            f"vs {_fmt_usd(summary['conviction_buy_dollars'])} in open-market buys."
        )
    else:
        take_parts.append(
            f"No conviction signal from insiders. "
            f"{summary['n_routine_comp']} routine comp txns and "
            f"{summary['n_scheduled_sales']} scheduled 10b5-1 sales dominate the flow."
        )
    if clusters:
        take_parts.append(
            f"Cluster buy caught: {clusters[0]['n_insiders']} insiders "
            f"buying {_fmt_usd(clusters[0]['total_dollars'])} between "
            f"{clusters[0]['window_start']} and {clusters[0]['window_end']}."
        )
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
