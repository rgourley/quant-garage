#!/usr/bin/env python3
"""
Reference implementation of the position-sizer skill.

Takes a basket of tickers (the PM has already decided what goes IN
the book) and emits position sizes under four canonical methods side
by side: vol-target (inverse-vol scaled to a target portfolio vol),
fractional Kelly (matrix form, user-supplied edges), risk parity
(Equal Risk Contribution), and equal weight. The PM picks the method
whose worldview matches their conviction.

This script does NOT pick names or predict returns. It does
descriptive math on a basket the user already chose.

Two output layers:
  Layer 1: canonical JSON matching skills/position-sizer/output-schema.json
  Layer 2: rendered side-by-side table to
           examples/position-sizer-output.md

Usage:
    python3 examples/run-position-sizer.py \\
      --tickers NVDA,AMZN,GOOGL,META \\
      --target-vol 0.12 \\
      --kelly-edges NVDA=0.15,AMZN=0.10,GOOGL=0.08,META=0.12

Reads MASSIVE_API_KEY from env.
"""
from __future__ import annotations

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone, timedelta

import numpy as np

# Make `lib.quant_garage` importable when running from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    FetchError,
    RateLimited,
    today,
    utcnow_iso,
    resolve_output_format,
    emit_to_stdout,
    annualized_vol,
    correlation_matrix,
    covariance_matrix,
    shrink_correlation,
    vol_target_weights,
    fractional_kelly_weights,
    risk_parity_weights,
    equal_weights,
)


client = MassiveClient()
NOW_UTC = datetime.now(timezone.utc)
TODAY = today()

# Per-run cache: one daily-aggs pull per ticker.
_AGGS_CACHE: dict[str, list[dict]] = {}

# Tickers whose pull hit a rate limit even after the client's retries plus one
# cooldown. They come back empty because the tier throttled us, not because
# there is no data, so the output must say so loudly rather than sizing a book
# on a silently truncated ticker set.
_RATE_LIMITED: set[str] = set()
_SLEEP_BETWEEN: float = 0.05
_RATE_LIMIT_COOLDOWN_SECONDS = 13

VALID_METHODS = ("vol_target", "kelly", "risk_parity", "equal_weight")


# ----- HTTP -----

def fetch_daily_aggs(ticker: str, lookback_days: int) -> list[dict]:
    """Pull daily aggregates for `ticker` covering lookback_days trading days.

    Overshoots by 1.6x in calendar days to handle weekends/holidays. Returns
    a list of {date, close} records sorted ascending by date.
    """
    if ticker in _AGGS_CACHE:
        return _AGGS_CACHE[ticker]

    end = TODAY
    start = end - timedelta(days=int(lookback_days * 1.6) + 10)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true"
    )
    try:
        doc, _ = client.get(path)
    except RateLimited:
        print(
            f"  WARN: rate limited on {ticker}; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            doc, _ = client.get(path)
        except FetchError as exc:
            print(f"  WARN: still failing for {ticker} after cooldown: {exc}",
                  file=sys.stderr)
            _RATE_LIMITED.add(ticker)
            _AGGS_CACHE[ticker] = []
            return []
    except FetchError as exc:
        print(f"  WARN: aggs for {ticker}: {exc}", file=sys.stderr)
        _AGGS_CACHE[ticker] = []
        return []

    results = doc.get("results") or []
    rows: list[dict] = []
    for r in results:
        ts_ms = r.get("t")
        close = r.get("c")
        if ts_ms is None or close is None:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    _AGGS_CACHE[ticker] = rows
    return rows


# ----- Returns + alignment -----

def daily_log_returns(rows: list[dict]) -> dict[str, float]:
    """Compute close-to-close daily log returns. Returns {date: return}."""
    out: dict[str, float] = {}
    prev_close = None
    for r in rows:
        c = r["close"]
        if prev_close is not None and prev_close > 0 and c > 0:
            out[r["date"]] = float(np.log(c / prev_close))
        prev_close = c
    return out


def align_returns(per_ticker: dict[str, dict[str, float]]) -> tuple[list[str], dict[str, list[float]]]:
    """Align per-ticker {date: return} dicts to the intersection of dates.

    Returns (sorted_dates, {ticker: [returns aligned to those dates]}).
    """
    if not per_ticker:
        return [], {}
    common_dates: set[str] | None = None
    for t, r in per_ticker.items():
        s = set(r.keys())
        common_dates = s if common_dates is None else common_dates & s
    common_dates = sorted(common_dates or [])
    aligned = {t: [per_ticker[t][d] for d in common_dates] for t in per_ticker}
    return common_dates, aligned


# ----- Formatting -----

def fmt_pct(x, decimals=1, signed=False):
    if x is None:
        return "n/a"
    val = x * 100.0
    if abs(val) < 0.5 * (10 ** -decimals):
        val = 0.0
    if signed:
        sign = "+" if val >= 0 else ""
        return f"{sign}{val:.{decimals}f}%"
    return f"{val:.{decimals}f}%"


def fmt_mult(x, decimals=1):
    if x is None:
        return "n/a"
    return f"{x:.{decimals}f}x"


# ----- CLI -----

def parse_edges(raw: str | None) -> dict[str, float] | None:
    if not raw:
        return None
    out: dict[str, float] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise SystemExit(f"--kelly-edges entry missing '=': {chunk!r}")
        t, v = chunk.split("=", 1)
        try:
            out[t.strip().upper()] = float(v.strip())
        except ValueError as exc:
            raise SystemExit(f"--kelly-edges value not a number: {chunk!r}") from exc
    return out


def parse_methods(raw: str) -> list[str]:
    methods = [m.strip() for m in raw.split(",") if m.strip()]
    for m in methods:
        if m not in VALID_METHODS:
            raise SystemExit(f"--methods unknown method {m!r}; valid: {VALID_METHODS}")
    return methods


ap = argparse.ArgumentParser(description="position-sizer reference")
ap.add_argument("--tickers", type=str, required=True,
                help="Comma-separated list of tickers in the book")
ap.add_argument("--target-vol", type=float, default=0.12,
                help="Annualized portfolio vol target (decimal). Default 0.12.")
ap.add_argument("--leverage-cap", type=float, default=1.0,
                help="Max sum of |w| across all methods. Default 1.0.")
ap.add_argument("--max-weight", type=float, default=None,
                help="Max single-position weight (decimal). Default no cap.")
ap.add_argument("--lookback-days", type=int, default=252,
                help="Trading days of daily returns to pull. Default 252.")
ap.add_argument("--kelly-edges", type=str, default=None,
                help="Comma-separated TICKER=edge pairs (decimals). Required for Kelly.")
ap.add_argument("--kelly-scale", type=float, default=0.25,
                help="Fractional Kelly scale. Default 0.25 (quarter Kelly).")
ap.add_argument("--methods", type=str,
                default="vol_target,kelly,risk_parity,equal_weight",
                help="Comma-separated subset of methods to run.")
ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT. Default: render.")
ap.add_argument("--shrinkage", type=float, default=0.05,
                help="Correlation shrinkage toward identity. Default 0.05.")
ap.add_argument("--sleep", type=float, default=None,
                help="Seconds to sleep between aggregate calls. Free Basic "
                     "tier caps at 5 calls/min; use --sleep 13 to stay under "
                     "it. Default: minimal spacing (assumes an unmetered tier).")
args = ap.parse_args()
fmt = resolve_output_format(args.format)

if args.sleep is not None:
    if args.sleep < 0:
        print("ERROR: --sleep cannot be negative", file=sys.stderr)
        sys.exit(1)
    _SLEEP_BETWEEN = args.sleep


tickers_req = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
if len(tickers_req) < 2:
    print("ERROR: need at least 2 tickers", file=sys.stderr)
    sys.exit(1)

methods = parse_methods(args.methods)
edges = parse_edges(args.kelly_edges)
if args.leverage_cap <= 0:
    print("ERROR: --leverage-cap must be > 0", file=sys.stderr)
    sys.exit(1)
if args.max_weight is not None and not (0 < args.max_weight <= 1.0):
    print("ERROR: --max-weight must be in (0, 1.0]", file=sys.stderr)
    sys.exit(1)
if args.target_vol <= 0:
    print("ERROR: --target-vol must be > 0", file=sys.stderr)
    sys.exit(1)


# ----- Data pull -----

print(
    f"Sizing {len(tickers_req)} tickers: {','.join(tickers_req)} ; "
    f"target_vol={args.target_vol:.0%}, leverage_cap={args.leverage_cap}, "
    f"max_weight={args.max_weight}, lookback={args.lookback_days}d",
    file=sys.stderr,
)
sources: list[dict] = []
per_ticker_returns: dict[str, dict[str, float]] = {}
n_trading_min = 60
tier_caveats: list[str] = []
excluded: list[dict] = []

for t in tickers_req:
    print(f"  Pulling daily aggs for {t}...", file=sys.stderr)
    rows = fetch_daily_aggs(t, args.lookback_days)
    sources.append({
        "endpoint": f"/v2/aggs/ticker/{t}/range/1/day/{{from}}/{{to}}",
        "fetched_at": utcnow_iso(),
        "context": f"daily aggs for {t}",
    })
    returns = daily_log_returns(rows)
    if len(returns) < n_trading_min:
        excluded.append({
            "ticker": t,
            "reason": "insufficient_history",
            "n_obs": len(returns),
            "min_required": n_trading_min,
        })
        tier_caveats.append(
            f"{t}: insufficient history ({len(returns)} days < {n_trading_min} min) — excluded from sizing"
        )
        continue
    per_ticker_returns[t] = returns
    time.sleep(_SLEEP_BETWEEN)

tickers_used_input = sorted(per_ticker_returns.keys())
if len(tickers_used_input) < 2:
    print(
        f"ERROR: only {len(tickers_used_input)} tickers passed the history filter; "
        f"need at least 2",
        file=sys.stderr,
    )
    sys.exit(1)

# Trim each ticker's series to the last `lookback_days` observations
for t in tickers_used_input:
    series = per_ticker_returns[t]
    if len(series) > args.lookback_days:
        sorted_keys = sorted(series.keys())
        keep = set(sorted_keys[-args.lookback_days:])
        per_ticker_returns[t] = {d: v for d, v in series.items() if d in keep}

aligned_dates, aligned_returns = align_returns(per_ticker_returns)
if len(aligned_dates) < n_trading_min:
    # Possibly the intersection is short even if individual series were long
    print(
        f"ERROR: after aligning, only {len(aligned_dates)} overlapping trading "
        f"days remain; need at least {n_trading_min}",
        file=sys.stderr,
    )
    sys.exit(1)


# ----- Per-name stats -----

name_stats: dict[str, dict] = {}
for t in tickers_used_input:
    r = np.asarray(aligned_returns[t], dtype=float)
    sigma_ann = annualized_vol(r)
    # Annualized mean: daily mean * 252. Display-only (Kelly takes edges from user).
    mean_ann = float(np.mean(r)) * 252.0
    name_stats[t] = {
        "vol_annualized": round(float(sigma_ann), 4),
        "n_obs": int(r.size),
        "mean_return_annualized": round(mean_ann, 4),
    }

vols = {t: name_stats[t]["vol_annualized"] for t in tickers_used_input}


# ----- Correlation + cov -----

ordered_tickers, raw_corr = correlation_matrix(aligned_returns)
shrunk = shrink_correlation(raw_corr, args.shrinkage)
cov = covariance_matrix(vols, shrunk, ordered_tickers)


# ----- Sizings -----

sizings: dict[str, dict] = {}

if "vol_target" in methods:
    sizings["vol_target"] = vol_target_weights(
        vols=vols,
        cov=cov,
        tickers=ordered_tickers,
        target_vol=args.target_vol,
        leverage_cap=args.leverage_cap,
        max_weight=args.max_weight,
    )

if "kelly" in methods:
    if edges is None:
        tier_caveats.append(
            "Kelly skipped: --kelly-edges not supplied. Kelly requires user-"
            "supplied edges per name; the script never predicts edge."
        )
    else:
        # Check every used ticker has an edge
        missing = [t for t in ordered_tickers if t not in edges]
        if missing:
            tier_caveats.append(
                f"Kelly skipped: edges missing for {', '.join(missing)}. "
                f"Provide edges for every ticker in the book."
            )
        else:
            kelly_result = fractional_kelly_weights(
                edges=edges,
                cov=cov,
                tickers=ordered_tickers,
                scale=args.kelly_scale,
                leverage_cap=args.leverage_cap,
                max_weight=args.max_weight,
            )
            if kelly_result is None:
                tier_caveats.append("Kelly skipped: insufficient edges supplied.")
            else:
                sizings["kelly_quarter"] = kelly_result

if "risk_parity" in methods:
    sizings["risk_parity"] = risk_parity_weights(
        cov=cov,
        tickers=ordered_tickers,
        leverage_cap=args.leverage_cap,
        max_weight=args.max_weight,
    )
    if not sizings["risk_parity"].get("converged", False):
        tier_caveats.append(
            f"Risk-parity did not converge in "
            f"{sizings['risk_parity'].get('iterations')} iterations; "
            f"fell back to inverse-vol weights."
        )

if "equal_weight" in methods:
    sizings["equal_weight"] = equal_weights(
        tickers=ordered_tickers,
        cov=cov,
        leverage_cap=args.leverage_cap,
    )


# ----- Rate-limit caveat (loud, goes first) -----

# A ticker throttled out of the pull is excluded from sizing silently unless
# we say so: the weights would then be computed on a smaller book than the
# user asked for, which looks like a valid answer but is not the one requested.
if _RATE_LIMITED:
    tier_caveats.insert(
        0,
        f"RATE LIMIT: {len(_RATE_LIMITED)} ticker(s) "
        f"({', '.join(sorted(_RATE_LIMITED))}) returned no data because the API "
        f"rate limit was hit, not because history is missing. They were dropped "
        f"from the sizing, so the weights cover a smaller book than requested. "
        f"Free Basic tier caps at 5 calls/min: rerun with --sleep 13 or upgrade "
        f"to Stocks Starter."
    )


# ----- Always-on caveats -----

tier_caveats.append(
    f"Vol estimates use {len(aligned_dates)}-day realized; future vol may differ."
)
tier_caveats.append(
    f"Correlation matrix shrunk {args.shrinkage*100:.0f}% toward identity for "
    f"numerical safety."
)
if edges is not None and "kelly_quarter" in sizings:
    tier_caveats.append(
        "Kelly assumes user-supplied edges are correct; the script doesn't "
        "predict returns."
    )


# ----- Payload -----

corr_block = {
    "tickers": ordered_tickers,
    "raw": [[round(float(v), 4) for v in row] for row in raw_corr.tolist()],
    "shrunk": [[round(float(v), 4) for v in row] for row in shrunk.tolist()],
    "shrinkage": float(args.shrinkage),
}

payload = {
    "skill": "position-sizer",
    "as_of": TODAY.isoformat(),
    "fetched_at": NOW_UTC.isoformat(),
    "tickers_requested": tickers_req,
    "tickers_used": ordered_tickers,
    "tickers_excluded": excluded,
    "lookback_days": int(args.lookback_days),
    "n_obs_aligned": len(aligned_dates),
    "target_vol": float(args.target_vol),
    "leverage_cap": float(args.leverage_cap),
    "max_weight": args.max_weight,
    "kelly_scale": float(args.kelly_scale),
    "methods_requested": methods,
    "methods_emitted": sorted(sizings.keys()),
    "name_stats": name_stats,
    "correlation_matrix": corr_block,
    "sizings": sizings,
    "tier_caveats": tier_caveats,
    "sources": sources,
}


# ----- Renderer -----

def render(payload: dict) -> str:
    used = payload["tickers_used"]
    sizings = payload["sizings"]
    name_stats = payload["name_stats"]
    lines: list[str] = []

    method_order = [m for m in ("vol_target", "kelly_quarter", "risk_parity", "equal_weight")
                    if m in sizings]
    method_labels = {
        "vol_target": "Vol-Target",
        "kelly_quarter": f"Kelly({payload['kelly_scale']:.2f})",
        "risk_parity": "Risk-Parity",
        "equal_weight": "Equal-Wt",
    }

    # Header
    lines.append(f"Position sizes — {', '.join(used)}")
    cap_txt = (f"Max weight {payload['max_weight']*100:.0f}%"
               if payload["max_weight"] is not None else "Max weight none")
    lines.append(
        f"Target vol {payload['target_vol']*100:.0f}% · "
        f"Lookback {payload['lookback_days']}d · "
        f"{cap_txt} · "
        f"Leverage cap {payload['leverage_cap']:.1f}x"
    )
    lines.append("")

    # Table header
    col_w = 12
    header_cells = [f"{method_labels[m]:>{col_w}}" for m in method_order]
    lines.append(
        f"{'Ticker':<8}  {'σ(annual)':>10}  " + "  ".join(header_cells)
    )
    rule_width = 8 + 2 + 10 + 2 + sum(col_w + 2 for _ in method_order) - 2
    lines.append("-" * rule_width)

    # Body rows
    for t in used:
        sigma_pct = name_stats[t]["vol_annualized"] * 100
        cells = []
        for m in method_order:
            w = sizings[m]["weights"].get(t, 0.0)
            cells.append(f"{w*100:>{col_w-1}.1f}%")
        lines.append(
            f"{t:<8}  {sigma_pct:>9.1f}%  " + "  ".join(cells)
        )
    lines.append("-" * rule_width)

    # Sum of |w|
    cells = []
    for m in method_order:
        cells.append(f"{sizings[m]['gross_exposure']*100:>{col_w-1}.1f}%")
    lines.append(
        f"{'Σ |w|':<8}  {'':>10}  " + "  ".join(cells)
    )

    # Portfolio vol
    cells = []
    for m in method_order:
        cells.append(f"{sizings[m]['portfolio_vol_annualized']*100:>{col_w-1}.1f}%")
    lines.append(
        f"{'Port vol':<8}  {'':>10}  " + "  ".join(cells)
    )

    # Binding constraint
    cells = []
    for m in method_order:
        b = sizings[m].get("binding_constraint")
        label = "none" if b is None else b
        cells.append(f"{label:>{col_w}}")
    lines.append(
        f"{'Binding':<8}  {'':>10}  " + "  ".join(cells)
    )
    lines.append("")

    # Take — adaptive based on observed weights and edges
    lines.append(generate_take(payload))

    # Caveats footer
    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"- {c}")
    return "\n".join(lines)


def _max_weight_ticker(sizing: dict) -> tuple[str, float]:
    weights = sizing.get("weights") or {}
    if not weights:
        return ("", 0.0)
    t = max(weights, key=lambda k: weights[k])
    return (t, weights[t])


def generate_take(payload: dict) -> str:
    sizings = payload["sizings"]
    name_stats = payload["name_stats"]
    parts: list[str] = []

    if "vol_target" in sizings:
        # Highest-vol vs lowest-vol name in the basket
        vol_sorted = sorted(name_stats.items(), key=lambda kv: kv[1]["vol_annualized"], reverse=True)
        if vol_sorted:
            high_t, high_s = vol_sorted[0]
            low_t, low_s = vol_sorted[-1]
            parts.append(
                f"Take: Vol-target tilts away from {high_t} "
                f"(σ {high_s['vol_annualized']*100:.0f}%) and toward {low_t} "
                f"(σ {low_s['vol_annualized']*100:.0f}%); keeps high-vol names "
                f"from dominating the book."
            )

    if "kelly_quarter" in sizings:
        # Pick name with highest edge / vol²
        edges = sizings["kelly_quarter"].get("edges_used") or {}
        if edges:
            ranked = sorted(
                edges.items(),
                key=lambda kv: (kv[1] / (name_stats[kv[0]]["vol_annualized"] ** 2)),
                reverse=True,
            )
            top_t, top_e = ranked[0]
            bot_t, bot_e = ranked[-1]
            parts.append(
                f"Kelly({payload['kelly_scale']:.2f}) shifts toward names with "
                f"the highest edge per variance — {top_t} (edge {top_e*100:.0f}%, "
                f"σ {name_stats[top_t]['vol_annualized']*100:.0f}%) over {bot_t} "
                f"(edge {bot_e*100:.0f}%, σ {name_stats[bot_t]['vol_annualized']*100:.0f}%)."
            )

    if "risk_parity" in sizings:
        # ERC picks the lowest-vol name as the largest weight in a uniform-corr case
        rp_top_t, rp_top_w = _max_weight_ticker(sizings["risk_parity"])
        parts.append(
            f"Risk-parity equalizes each name's contribution to portfolio "
            f"variance — largest weight goes to {rp_top_t} ({rp_top_w*100:.0f}%), "
            f"the name whose vol + correlation profile carries the smallest "
            f"share at equal weight."
        )

    if "equal_weight" in sizings:
        parts.append("Equal-weight ignores risk entirely.")

    parts.append("Pick the method that matches your conviction model.")
    return " ".join(parts)


rendered = render(payload)


# ----- Write output -----

out_name = "position-sizer-output.md"
out_path = os.path.join(os.path.dirname(__file__), out_name)
with open(out_path, "w") as fout:
    fout.write("# position-sizer run\n\n")
    fout.write(f"Generated: {NOW_UTC.isoformat()}\n")
    fout.write(f"Tickers: {','.join(payload['tickers_used'])}\n")
    fout.write(f"Target vol: {args.target_vol:.0%}  ")
    fout.write(f"Leverage cap: {args.leverage_cap}  ")
    fout.write(f"Max weight: {args.max_weight}\n\n")
    fout.write("## Layer 1: canonical JSON (live data)\n\n")
    fout.write("```json\n")
    fout.write(json.dumps(payload, indent=2, default=str))
    fout.write("\n```\n\n")
    fout.write("## Layer 2: rendered table (live data)\n\n")
    fout.write("```\n")
    fout.write(rendered)
    fout.write("\n```\n")

print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
emit_to_stdout(rendered, payload, fmt)
