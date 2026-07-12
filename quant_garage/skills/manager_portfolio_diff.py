"""
manager-portfolio-diff as an importable library function.

Diffs the two most recent quarterly 13-F filings for a specified filer
(institutional investment manager). Reports initiations, exits, adds
(>= 25% share change), and trims (<= -25%), sorted by market_value
descending. Answers "what did Berkshire do last quarter?"

    from quant_garage.skills.manager_portfolio_diff import run, render
    payload = run(filer="berkshire")  # alias resolution
    payload = run(filer_cik="0001067983")  # direct CIK

Reads MASSIVE_API_KEY from env. Stocks Basic minimum.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .. import MassiveClient, FetchError, today, utcnow_iso


# Convenience aliases for well-known filers so callers don't have to
# hunt for CIKs. Aliases match lowercase, hyphen-stripped input.
FILER_ALIASES: dict[str, tuple[str, str]] = {
    "berkshire": ("0001067983", "Berkshire Hathaway (Warren Buffett)"),
    "berkshire hathaway": ("0001067983", "Berkshire Hathaway (Warren Buffett)"),
    "buffett": ("0001067983", "Berkshire Hathaway (Warren Buffett)"),
    "baupost": ("0001061768", "Baupost Group (Seth Klarman)"),
    "klarman": ("0001061768", "Baupost Group (Seth Klarman)"),
    "renaissance": ("0001037389", "Renaissance Technologies"),
    "rentech": ("0001037389", "Renaissance Technologies"),
    "bridgewater": ("0001350694", "Bridgewater Associates"),
    "third point": ("0001040273", "Third Point (Dan Loeb)"),
    "loeb": ("0001040273", "Third Point (Dan Loeb)"),
    "pershing": ("0001336528", "Pershing Square (Bill Ackman)"),
    "pershing square": ("0001336528", "Pershing Square (Bill Ackman)"),
    "ackman": ("0001336528", "Pershing Square (Bill Ackman)"),
    "tiger global": ("0001167483", "Tiger Global (Chase Coleman)"),
    "coleman": ("0001167483", "Tiger Global (Chase Coleman)"),
    "scion": ("0001649339", "Scion Asset Management (Michael Burry)"),
    "burry": ("0001649339", "Scion Asset Management (Michael Burry)"),
    "appaloosa": ("0001112264", "Appaloosa Management (David Tepper)"),
    "tepper": ("0001112264", "Appaloosa Management (David Tepper)"),
}

MATERIAL_CHANGE_PCT = 0.25


class _Sources:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def record(self, endpoint: str, fetched_at: str, context: str) -> None:
        self._items.append({"endpoint": endpoint, "fetched_at": fetched_at, "context": context})

    def to_list(self) -> list[dict]:
        return list(self._items)


def _fetch_13f(client: MassiveClient, filer_cik: str, sources: _Sources) -> list[dict]:
    path = f"/stocks/filings/vX/13-F?filer_cik={filer_cik}&limit=1000&sort=filing_date.desc"
    rows: list[dict] = []
    try:
        for page, fetched_at in client.paginate(path):
            rows.extend(page)
            sources.record(
                f"/stocks/filings/vX/13-F?filer_cik={filer_cik}",
                fetched_at,
                f"13-F filings for CIK {filer_cik}",
            )
            if len(rows) >= 20_000:
                break
    except FetchError:
        return []
    return rows


def _resolve_filer(filer: str | None, filer_cik: str | None) -> tuple[str, str | None]:
    if filer_cik:
        return filer_cik.strip().zfill(10), None
    if not filer:
        raise ValueError("Provide either filer (name/alias) or filer_cik.")
    key = filer.strip().lower()
    if key in FILER_ALIASES:
        cik, display = FILER_ALIASES[key]
        return cik, display
    raise ValueError(
        f"Unknown filer alias {filer!r}. Known aliases: "
        f"{', '.join(sorted(FILER_ALIASES.keys()))}. "
        "Or pass filer_cik directly (10-digit zero-padded)."
    )


def _classify(prior_shares: int | None, current_shares: int | None) -> str:
    if prior_shares is None and current_shares is not None:
        return "initiation"
    if prior_shares is not None and current_shares is None:
        return "exit"
    if prior_shares is None or current_shares is None:
        return "unchanged"
    if prior_shares == 0 and current_shares > 0:
        return "initiation"
    if current_shares == 0 and prior_shares > 0:
        return "exit"
    if prior_shares == 0:
        return "unchanged"
    delta_pct = (current_shares - prior_shares) / prior_shares
    if delta_pct >= MATERIAL_CHANGE_PCT:
        return "add"
    if delta_pct <= -MATERIAL_CHANGE_PCT:
        return "trim"
    return "unchanged"


def run(
    filer: str | None = None,
    filer_cik: str | None = None,
    client: MassiveClient | None = None,
) -> dict:
    """Diff the two most recent quarterly 13-F snapshots for a filer.

    Args:
        filer: alias for a well-known filer (e.g. "berkshire", "burry").
        filer_cik: 10-digit zero-padded SEC CIK. Takes precedence over
            filer.
        client: reuse an existing MassiveClient.
    """
    cik, display_name = _resolve_filer(filer, filer_cik)
    client = client or MassiveClient()
    sources = _Sources()

    rows = _fetch_13f(client, cik, sources)

    tier_caveats: list[str] = []
    if not rows:
        tier_caveats.append(
            f"No 13-F filings returned for CIK {cik}. "
            "Verify the CIK or check that Massive covers this filer."
        )
        return {
            "skill": "manager-portfolio-diff",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "filer_cik": cik,
            "filer_display_name": display_name,
            "periods": None,
            "summary": None,
            "changes": None,
            "tier_caveats": tier_caveats,
            "sources": sources.to_list(),
        }

    # Group by period (quarter-end)
    by_period: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        p = r.get("period")
        if p:
            by_period[p].append(r)
    sorted_periods = sorted(by_period.keys(), reverse=True)

    if len(sorted_periods) < 2:
        tier_caveats.append(
            f"Only one 13-F period on record for CIK {cik} ({sorted_periods[0] if sorted_periods else 'n/a'}). "
            "Quarter-over-quarter diff not available."
        )
        return {
            "skill": "manager-portfolio-diff",
            "as_of": today().isoformat(),
            "fetched_at": utcnow_iso(),
            "filer_cik": cik,
            "filer_display_name": display_name,
            "periods": {
                "current": sorted_periods[0] if sorted_periods else None,
                "prior": None,
            },
            "summary": None,
            "changes": None,
            "tier_caveats": tier_caveats,
            "sources": sources.to_list(),
        }

    current_period, prior_period = sorted_periods[0], sorted_periods[1]

    # Aggregate holdings by CUSIP within each period (multiple rows per
    # CUSIP happen when multiple managers file jointly with different
    # investment discretion).
    def _agg_by_cusip(period_rows: list[dict]) -> dict[str, dict]:
        agg: dict[str, dict] = {}
        for r in period_rows:
            cusip = r.get("cusip")
            if not cusip:
                continue
            entry = agg.setdefault(cusip, {
                "cusip": cusip,
                "issuer_name": r.get("issuer_name"),
                "title_of_class": r.get("title_of_class"),
                "shares": 0,
                "market_value": 0.0,
            })
            entry["shares"] += int(r.get("shares_or_principal_amount") or 0)
            entry["market_value"] += float(r.get("market_value") or 0)
        return agg

    current_holdings = _agg_by_cusip(by_period[current_period])
    prior_holdings = _agg_by_cusip(by_period[prior_period])

    all_cusips = set(current_holdings) | set(prior_holdings)
    changes = {
        "initiation": [],
        "exit": [],
        "add": [],
        "trim": [],
        "unchanged": [],
    }
    for cusip in all_cusips:
        cur = current_holdings.get(cusip)
        prior = prior_holdings.get(cusip)
        cur_shares = cur["shares"] if cur else None
        prior_shares = prior["shares"] if prior else None
        label = _classify(prior_shares, cur_shares)
        entry = {
            "cusip": cusip,
            "issuer_name": (cur or prior).get("issuer_name"),
            "prior_shares": prior_shares,
            "current_shares": cur_shares,
            "prior_market_value": prior["market_value"] if prior else None,
            "current_market_value": cur["market_value"] if cur else None,
            "delta_shares": (cur_shares or 0) - (prior_shares or 0),
        }
        if prior_shares and cur_shares:
            entry["delta_shares_pct"] = round((cur_shares - prior_shares) / prior_shares, 4)
        else:
            entry["delta_shares_pct"] = None
        changes[label].append(entry)

    # Sort each bucket by market value (current for adds/initiations,
    # prior for exits/trims).
    changes["initiation"].sort(key=lambda e: -(e["current_market_value"] or 0))
    changes["exit"].sort(key=lambda e: -(e["prior_market_value"] or 0))
    changes["add"].sort(key=lambda e: -(e["current_market_value"] or 0))
    changes["trim"].sort(key=lambda e: -(e["prior_market_value"] or 0))
    changes["unchanged"].sort(key=lambda e: -(e["current_market_value"] or 0))

    total_current_value = sum(h["market_value"] for h in current_holdings.values())
    total_prior_value = sum(h["market_value"] for h in prior_holdings.values())

    summary = {
        "n_holdings_current": len(current_holdings),
        "n_holdings_prior": len(prior_holdings),
        "total_market_value_current": round(total_current_value, 2),
        "total_market_value_prior": round(total_prior_value, 2),
        "portfolio_value_delta_pct": (
            round((total_current_value - total_prior_value) / total_prior_value, 4)
            if total_prior_value > 0 else None
        ),
        "n_initiations": len(changes["initiation"]),
        "n_exits": len(changes["exit"]),
        "n_adds": len(changes["add"]),
        "n_trims": len(changes["trim"]),
        "n_unchanged": len(changes["unchanged"]),
    }

    tier_caveats.append(
        "13-F is quarterly and lagged (typically 45 days after quarter-end). "
        "Shows what the filer held at the reporting date, not their current book."
    )
    tier_caveats.append(
        "Long positions and options only. Shorts and other derivatives may not appear. "
        "'Market value' is what the filer reported, not marked to a current price."
    )

    return {
        "skill": "manager-portfolio-diff",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "filer_cik": cik,
        "filer_display_name": display_name,
        "periods": {
            "current": current_period,
            "prior": prior_period,
        },
        "summary": summary,
        "changes": changes,
        "tier_caveats": tier_caveats,
        "sources": sources.to_list(),
    }


# ----- Renderer -----

def _fmt_usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    absx = abs(x)
    sign = "-" if x < 0 else ""
    if absx >= 1_000_000_000:
        return f"{sign}${absx / 1_000_000_000:.2f}B"
    if absx >= 1_000_000:
        return f"{sign}${absx / 1_000_000:.1f}M"
    if absx >= 1_000:
        return f"{sign}${absx / 1_000:.1f}k"
    return f"{sign}${absx:,.0f}"


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "n/a"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct * 100:.1f}%"


def _render_bucket(name: str, tag: str, entries: list[dict], value_key: str, cap: int = 10) -> list[str]:
    if not entries:
        return []
    lines = [f"[{tag}] ({len(entries)})"]
    for e in entries[:cap]:
        mv = e.get(value_key)
        shares = e.get("current_shares" if value_key == "current_market_value" else "prior_shares")
        pct = e.get("delta_shares_pct")
        issuer = (e.get("issuer_name") or "?")[:40]
        if name == "add" or name == "trim":
            lines.append(
                f"  · {issuer:<40} {_fmt_usd(mv):>10} "
                f"({int(shares or 0):>10,} sh, {_fmt_pct(pct)})"
            )
        else:
            lines.append(
                f"  · {issuer:<40} {_fmt_usd(mv):>10} "
                f"({int(shares or 0):>10,} sh)"
            )
    if len(entries) > cap:
        lines.append(f"  ... and {len(entries) - cap} more")
    return lines


def render(payload: dict) -> str:
    lines: list[str] = []
    cik = payload["filer_cik"]
    name = payload.get("filer_display_name") or f"CIK {cik}"

    periods = payload.get("periods")
    if not periods or not periods.get("current"):
        lines.append(f"Manager portfolio diff: {name}")
        lines.append("")
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    prior = periods.get("prior")
    if not prior:
        lines.append(f"Manager portfolio diff: {name} · {periods['current']} (only period on record)")
        lines.append("")
        for c in payload.get("tier_caveats", []):
            lines.append(f"- {c}")
        return "\n".join(lines).rstrip()

    summary = payload["summary"]
    lines.append(f"Manager portfolio diff: {name} · {prior} → {periods['current']}")
    lines.append(
        f"Holdings: {summary['n_holdings_prior']} → {summary['n_holdings_current']} "
        f"· Portfolio value: {_fmt_usd(summary['total_market_value_prior'])} → "
        f"{_fmt_usd(summary['total_market_value_current'])} "
        f"({_fmt_pct(summary['portfolio_value_delta_pct'])})"
    )
    lines.append(
        f"Activity: +{summary['n_initiations']} init  -{summary['n_exits']} exit  "
        f"^{summary['n_adds']} add  v{summary['n_trims']} trim  "
        f"~{summary['n_unchanged']} unchanged"
    )
    lines.append("")

    changes = payload["changes"]
    lines.extend(_render_bucket("initiation", "NEW POSITIONS", changes["initiation"], "current_market_value"))
    if changes["initiation"]:
        lines.append("")
    lines.extend(_render_bucket("exit", "EXITED POSITIONS", changes["exit"], "prior_market_value"))
    if changes["exit"]:
        lines.append("")
    lines.extend(_render_bucket("add", "ADDS (>= 25%)", changes["add"], "current_market_value"))
    if changes["add"]:
        lines.append("")
    lines.extend(_render_bucket("trim", "TRIMS (<= -25%)", changes["trim"], "prior_market_value"))
    if changes["trim"]:
        lines.append("")

    take_parts: list[str] = []
    if summary["n_initiations"] > 0:
        top_init = changes["initiation"][0] if changes["initiation"] else None
        if top_init:
            take_parts.append(
                f"Biggest new position: {top_init['issuer_name']} "
                f"at {_fmt_usd(top_init['current_market_value'])}."
            )
    if summary["n_exits"] > 0:
        top_exit = changes["exit"][0] if changes["exit"] else None
        if top_exit:
            take_parts.append(
                f"Biggest exit: {top_exit['issuer_name']} "
                f"({_fmt_usd(top_exit['prior_market_value'])})."
            )
    if not take_parts:
        take_parts.append("No initiations or exits this quarter; only add/trim adjustments.")
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
