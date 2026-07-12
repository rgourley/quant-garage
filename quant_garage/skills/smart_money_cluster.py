"""
smart-money-cluster as an importable library function.

Iterates manager-portfolio-diff across a curated cohort of well-known
funds (Berkshire, Baupost, Renaissance, Bridgewater, Third Point,
Pershing Square, Tiger Global, Scion, Appaloosa) and aggregates the
initiations and adds. Reports issuers that appeared in >= N funds'
new positions this quarter as a "cross-fund conviction" signal.

    from quant_garage.skills.smart_money_cluster import run, render
    payload = run(min_funds=2)
"""
from __future__ import annotations

from collections import defaultdict

from .. import MassiveClient, today, utcnow_iso
from . import manager_portfolio_diff


DEFAULT_COHORT = (
    "berkshire",
    "baupost",
    "renaissance",
    "bridgewater",
    "third point",
    "pershing",
    "tiger global",
    "scion",
    "appaloosa",
)


def _safe_run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def run(
    aliases: list[str] | None = None,
    min_funds: int = 2,
    client: MassiveClient | None = None,
) -> dict:
    """Run manager-portfolio-diff across a cohort and aggregate crowded positions.

    Args:
        aliases: list of filer aliases. Default: DEFAULT_COHORT.
        min_funds: minimum number of funds a name must appear in to
            surface in the cluster report.
        client: reuse an existing MassiveClient.
    """
    if aliases is None:
        aliases = list(DEFAULT_COHORT)
    else:
        aliases = [a.strip().lower() for a in aliases if a and a.strip()]
    if not aliases:
        raise ValueError("provide at least one alias")
    if min_funds < 1:
        raise ValueError("min_funds must be >= 1")

    client = client or MassiveClient()

    per_fund: list[dict] = []
    errors: dict[str, str] = {}
    initiations_by_issuer: dict[str, list[dict]] = defaultdict(list)
    adds_by_issuer: dict[str, list[dict]] = defaultdict(list)
    exits_by_issuer: dict[str, list[dict]] = defaultdict(list)

    for alias in aliases:
        out, err = _safe_run(
            manager_portfolio_diff.run,
            filer=alias,
            client=client,
        )
        if err:
            errors[alias] = err
            continue
        if not out or not out.get("changes"):
            per_fund.append({
                "alias": alias,
                "display_name": (out or {}).get("filer_display_name"),
                "current_period": None,
                "prior_period": None,
                "n_initiations": 0,
                "n_adds": 0,
                "n_exits": 0,
            })
            continue

        periods = out.get("periods") or {}
        display = out.get("filer_display_name") or alias
        per_fund.append({
            "alias": alias,
            "display_name": display,
            "current_period": periods.get("current"),
            "prior_period": periods.get("prior"),
            "n_initiations": (out.get("summary") or {}).get("n_initiations", 0),
            "n_adds": (out.get("summary") or {}).get("n_adds", 0),
            "n_exits": (out.get("summary") or {}).get("n_exits", 0),
        })

        for e in out["changes"].get("initiation", []):
            initiations_by_issuer[e["issuer_name"]].append({
                "fund": display,
                "cusip": e.get("cusip"),
                "current_market_value": e.get("current_market_value"),
                "current_shares": e.get("current_shares"),
            })
        for e in out["changes"].get("add", []):
            adds_by_issuer[e["issuer_name"]].append({
                "fund": display,
                "cusip": e.get("cusip"),
                "delta_shares_pct": e.get("delta_shares_pct"),
                "current_market_value": e.get("current_market_value"),
            })
        for e in out["changes"].get("exit", []):
            exits_by_issuer[e["issuer_name"]].append({
                "fund": display,
                "cusip": e.get("cusip"),
                "prior_market_value": e.get("prior_market_value"),
            })

    # Aggregate. Dedupe fund count per issuer: a fund holding multiple
    # CUSIPs of the same issuer (share classes, ETF variants) counts
    # once. Sum dollars across the underlying CUSIPs.
    def _rank(d: dict, key: str = "current_market_value") -> list[dict]:
        rows: list[dict] = []
        for name, entries in d.items():
            funds_set: set[str] = set()
            fund_dollars: dict[str, float] = defaultdict(float)
            for e in entries:
                funds_set.add(e["fund"])
                fund_dollars[e["fund"]] += e.get(key) or 0
            if len(funds_set) < min_funds:
                continue
            total = sum(fund_dollars.values())
            rows.append({
                "issuer_name": name,
                "n_funds": len(funds_set),
                "total_dollars": round(total, 2),
                "funds": sorted(funds_set),
                "n_cusips": len(entries),
            })
        rows.sort(key=lambda x: (-x["n_funds"], -x["total_dollars"]))
        return rows

    clustered_initiations = _rank(initiations_by_issuer, key="current_market_value")
    clustered_adds = _rank(adds_by_issuer, key="current_market_value")
    clustered_exits = _rank(exits_by_issuer, key="prior_market_value")

    tier_caveats: list[str] = [
        "13-F is filed quarterly and lagged ~45 days. Cluster reads what "
        "funds held at the reporting date; positions may have changed.",
        "Longs and options only. Shorts and derivatives do not appear.",
        "Fund cohorts are curated. Different cohort = different signal. "
        "Custom aliases pass in via the CLI --aliases flag.",
    ]
    if errors:
        for alias, err in errors.items():
            tier_caveats.append(f"Sub-run failed for {alias}: {err}")

    return {
        "skill": "smart-money-cluster",
        "as_of": today().isoformat(),
        "fetched_at": utcnow_iso(),
        "aliases": aliases,
        "min_funds": int(min_funds),
        "n_funds_queried": len(aliases),
        "n_funds_success": len(per_fund),
        "per_fund": per_fund,
        "clustered_initiations": clustered_initiations,
        "clustered_adds": clustered_adds,
        "clustered_exits": clustered_exits,
        "n_clustered_initiations": len(clustered_initiations),
        "n_clustered_adds": len(clustered_adds),
        "n_clustered_exits": len(clustered_exits),
        "errors": errors,
        "tier_caveats": tier_caveats,
    }


# ----- Renderer -----

def _fmt_usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    absx = abs(x)
    if absx >= 1_000_000_000:
        return f"${absx / 1_000_000_000:.2f}B"
    if absx >= 1_000_000:
        return f"${absx / 1_000_000:.1f}M"
    if absx >= 1_000:
        return f"${absx / 1_000:.1f}k"
    return f"${absx:,.0f}"


def _render_cluster(name: str, entries: list[dict], cap: int = 15) -> list[str]:
    lines = [f"[{name}] ({len(entries)})"]
    if not entries:
        lines.append("  · none")
        return lines
    for e in entries[:cap]:
        funds_str = ", ".join(e["funds"][:5])
        more = f" +{len(e['funds']) - 5}" if len(e["funds"]) > 5 else ""
        issuer = (e["issuer_name"] or "?")[:38]
        lines.append(
            f"  {e['n_funds']}× {issuer:<38} {_fmt_usd(e['total_dollars']):>10}   {funds_str}{more}"
        )
    if len(entries) > cap:
        lines.append(f"  ... and {len(entries) - cap} more")
    return lines


def render(payload: dict) -> str:
    lines: list[str] = []

    lines.append(
        f"Smart-money cluster: {payload['n_funds_success']}/{payload['n_funds_queried']} funds "
        f"· min_funds={payload['min_funds']}"
    )
    lines.append("")

    # Per-fund summary
    lines.append("Per-fund")
    for f in payload["per_fund"]:
        period = f"{f['prior_period']} → {f['current_period']}" if f["prior_period"] else "(one period only)"
        lines.append(
            f"  · {f['display_name'] or f['alias']}: {period} · "
            f"+{f.get('n_initiations', 0)} init  ^{f.get('n_adds', 0)} add  -{f.get('n_exits', 0)} exit"
        )
    lines.append("")

    lines.extend(_render_cluster("CLUSTERED NEW POSITIONS", payload["clustered_initiations"]))
    lines.append("")
    lines.extend(_render_cluster("CLUSTERED ADDS (>= 25%)", payload["clustered_adds"]))
    lines.append("")
    lines.extend(_render_cluster("CLUSTERED EXITS", payload["clustered_exits"]))
    lines.append("")

    # Take
    take_parts: list[str] = []
    if payload["clustered_initiations"]:
        top = payload["clustered_initiations"][0]
        take_parts.append(
            f"Top clustered new position: {top['issuer_name']} "
            f"({top['n_funds']} funds, {_fmt_usd(top['total_dollars'])} combined)."
        )
    if payload["clustered_exits"]:
        top_exit = payload["clustered_exits"][0]
        take_parts.append(
            f"Top clustered exit: {top_exit['issuer_name']} "
            f"({top_exit['n_funds']} funds)."
        )
    if not take_parts:
        take_parts.append("No cross-fund clustering at the min_funds threshold.")
    lines.append("Take: " + " ".join(take_parts))

    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines).rstrip()
