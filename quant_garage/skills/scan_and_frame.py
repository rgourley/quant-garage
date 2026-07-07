"""
scan-and-frame: research-tier idea generation with regime framing.

Chains market-regime (context) + universe-builder (candidates) +
relative-strength (rank the candidates vs SPY). Optionally adds
factor-research for a broader factor context (heavy, opt-in).

Different from portfolio-review: this is discovery-mode. "What
should I look at right now, framed by the current regime?"

    from quant_garage.skills.scan_and_frame import run, render
    payload = run(candidate_source="curated", min_mcap=10e9, top_n_rank=15)
"""
from __future__ import annotations

import sys
from typing import Iterable

from .. import MassiveClient, today, utcnow_iso
from . import (
    market_regime,
    universe_builder,
    relative_strength,
    factor_research,
)


def run(
    candidate_source: str = "curated",
    min_mcap: float = 10e9,
    max_mcap: float | None = None,
    include_sectors: str | Iterable[str] | None = None,
    exclude_sectors: str | Iterable[str] | None = None,
    top_n_rank: int = 15,
    include_factor_research: bool = False,
    factor_universe_size: int = 200,
    client: MassiveClient | None = None,
) -> dict:
    """Regime-framed idea generation.

    Args:
        candidate_source: 'curated' (fast, top ~200 US names) or
            'reference' (full universe, slower). Passed to universe-builder.
        min_mcap: minimum market cap. Default $10B.
        max_mcap: optional cap ceiling.
        include_sectors: optional sector filter.
        exclude_sectors: optional sector exclusion.
        top_n_rank: how many universe-builder candidates to rank by RS.
            Default 15.
        include_factor_research: also run factor-research for broader
            factor context. Heavy — off by default.
        factor_universe_size: universe size for factor-research. Default 200.
        client: reuse an existing MassiveClient.
    """
    client = client or MassiveClient()
    sections: dict = {}
    errors: list[dict] = []

    def _try(name, fn):
        try:
            sections[name] = fn()
        except Exception as exc:
            errors.append({"section": name, "error": str(exc)})

    # 1) Regime for framing
    print("[1/4] market-regime...", file=sys.stderr)
    _try("market_regime", lambda: market_regime.run(client=client))

    # 2) Universe candidates
    print(f"[2/4] universe-builder ({candidate_source})...", file=sys.stderr)
    _try("universe_builder", lambda: universe_builder.run(
        candidate_source=candidate_source,
        min_mcap=min_mcap, max_mcap=max_mcap,
        include_sectors=include_sectors, exclude_sectors=exclude_sectors,
        client_=client,
    ))

    # 3) Pull top-N candidates + rank by RS vs SPY
    top_tickers: list[str] = []
    ub = sections.get("universe_builder")
    if ub:
        survivors = ub.get("survivors") or []
        top_tickers = [s.get("ticker") for s in survivors[:top_n_rank]
                        if s.get("ticker")]
    if top_tickers:
        print(f"[3/4] relative-strength ({len(top_tickers)} candidates)...",
              file=sys.stderr)
        _try("relative_strength", lambda: relative_strength.run(
            watchlist=",".join(top_tickers), client=client,
        ))
    else:
        sections["relative_strength"] = None

    # 4) Optional factor context
    if include_factor_research:
        print("[4/4] factor-research (heavy)...", file=sys.stderr)
        _try("factor_research", lambda: factor_research.run(
            universe_size=factor_universe_size, client_=client,
        ))
    else:
        sections["factor_research"] = None

    headline = _build_headline(sections, top_tickers)

    return {
        "scan_params": {
            "candidate_source": candidate_source,
            "min_mcap": min_mcap,
            "max_mcap": max_mcap,
            "include_sectors": include_sectors,
            "exclude_sectors": exclude_sectors,
            "top_n_rank": top_n_rank,
            "include_factor_research": include_factor_research,
            "as_of": today().isoformat(),
        },
        "headline": headline,
        "sections": sections,
        "errors": errors,
        "generated_at": utcnow_iso(),
    }


def _build_headline(sections: dict, top_tickers: list[str]) -> dict:
    hl: dict = {
        "regime": None,
        "n_candidates": None,
        "top_by_composite": [],
        "top_factor": None,
    }

    mr = sections.get("market_regime")
    if mr:
        hl["regime"] = (mr.get("composite_regime") or {}).get("label")

    ub = sections.get("universe_builder")
    if ub:
        hl["n_candidates"] = len(ub.get("survivors") or [])

    rs = sections.get("relative_strength")
    if rs:
        rankings = rs.get("rankings") or []
        rankings_sorted = sorted(
            rankings,
            key=lambda r: -(r.get("composite_percentile") or 0),
        )
        for r in rankings_sorted[:5]:
            hl["top_by_composite"].append({
                "ticker": r.get("ticker"),
                "composite_percentile": r.get("composite_percentile"),
                "trend_label": r.get("trend_label"),
            })

    fr = sections.get("factor_research")
    if fr:
        # Prefer significant factor with highest |t-stat|
        factors = fr.get("factors") or []
        significant = [
            f for f in factors
            if (f.get("t_stat") or 0) and abs(f.get("t_stat") or 0) >= 2
        ]
        if significant:
            top = max(significant, key=lambda f: abs(f.get("t_stat") or 0))
            hl["top_factor"] = {
                "name": top.get("name"),
                "t_stat": top.get("t_stat"),
                "ic_avg": top.get("ic_avg"),
            }

    return hl


def render(payload: dict) -> str:
    params = payload["scan_params"]
    hl = payload["headline"]
    sections = payload["sections"]
    lines: list[str] = []

    lines.append(
        f"Scan and Frame — {params['as_of']}\n"
        f"Universe: {params['candidate_source']} · "
        f"Min mcap: ${params['min_mcap'] / 1e9:.0f}B"
        + (f" · Sectors: {params['include_sectors']}"
           if params.get("include_sectors") else "")
    )
    lines.append("")

    lines.append("HEADLINE")
    lines.append("─" * 60)
    if hl.get("regime"):
        lines.append(f"Regime:        {hl['regime'].upper()}")
    if hl.get("n_candidates") is not None:
        lines.append(
            f"Universe:      {hl['n_candidates']} names survived filters"
        )
    if hl.get("top_by_composite"):
        top_str = ", ".join(
            f"{r['ticker']} ({r.get('composite_percentile', 0):.0f}%ile)"
            for r in hl["top_by_composite"]
        )
        lines.append(f"Top RS:        {top_str}")
    if hl.get("top_factor"):
        tf = hl["top_factor"]
        t = tf.get("t_stat")
        ic = tf.get("ic_avg")
        t_str = f"{t:+.1f}" if t is not None else "n/a"
        ic_str = f", IC {ic:+.3f}" if ic is not None else ""
        lines.append(f"Top factor:    {tf['name']} (t={t_str}{ic_str})")
    lines.append("")

    render_map = [
        ("market_regime", "MACRO REGIME", market_regime.render),
        ("universe_builder", "UNIVERSE CANDIDATES", universe_builder.render),
        ("relative_strength", "RS RANKING", relative_strength.render),
        ("factor_research", "FACTOR CONTEXT", factor_research.render),
    ]
    for key, title, render_fn in render_map:
        sub = sections.get(key)
        if sub is None:
            continue
        lines.append(title)
        lines.append("═" * 60)
        try:
            lines.append(render_fn(sub))
        except Exception as exc:
            lines.append(f"(render error: {exc})")
        lines.append("")

    errors = payload.get("errors") or []
    if errors:
        lines.append("ERRORS")
        lines.append("─" * 60)
        for e in errors:
            lines.append(f"  {e['section']}: {e['error']}")

    return "\n".join(lines)
