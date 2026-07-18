"""
commodity-cycle as an importable library function.

Answers one question: is this commodity in a winning or losing macro
setup right now, and which macro driver dominates. Where fixed-income-context
paints the whole cross-asset tape, commodity-cycle zooms in on a single
commodity ETF and reads the specific drivers: the dollar, real yields,
miner divergence (for gold), and momentum.

    from quant_garage.skills.commodity_cycle import run, render
    payload = run(ticker="GLD", window=60)
    print(render(payload))

Signals:
  - DXY correlation      rolling corr of the commodity vs UUP
  - Real-yield corr      rolling corr vs the TIP-minus-IEF spread
  - Miner divergence     GLD vs GDX relative return (GLD only)
  - Silver co-movement   rolling corr GLD vs SLV (GLD only)
  - Momentum quintile    window return ranked vs trailing-year windows

Take: constructive / neutral / headwind + dominant driver.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta

import numpy as np

from .. import (
    MassiveClient,
    FetchError,
    RateLimited,
    today,
    utcnow_iso,
    percentile_rank,
)


MACRO_CONTEXT: dict[str, str] = {
    "UUP": "US Dollar Index (bullish)",
    "TIP": "TIPS (inflation-protected)",
    "IEF": "7-10 Year Treasuries (belly)",
}

COMMODITY_LABELS: dict[str, str] = {
    "GLD": "Gold",
    "SLV": "Silver",
    "USO": "Crude Oil",
    "DBC": "Broad Commodities",
    "GDX": "Gold Miners",
    "SLX": "Steel",
    "UNG": "Natural Gas",
}

_RATE_LIMIT_COOLDOWN_SECONDS = 13


class _State:
    def __init__(self, sleep_between: float) -> None:
        self.client = MassiveClient()
        self.today = today()
        self.aggs_cache: dict[str, list[dict]] = {}
        self.rate_limited: set[str] = set()
        self.sleep_between = sleep_between


# ----- HTTP -----

def _fetch_daily_aggs(state: _State, ticker: str, calendar_days: int) -> list[dict]:
    if ticker in state.aggs_cache:
        return state.aggs_cache[ticker]

    end = state.today
    start = end - timedelta(days=calendar_days)
    path = (
        f"/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    try:
        doc, _ = state.client.get(path, params)
    except RateLimited:
        print(
            f"  WARN: rate limited on {ticker}; cooling down "
            f"{_RATE_LIMIT_COOLDOWN_SECONDS}s and retrying once...",
            file=sys.stderr,
        )
        time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
        try:
            doc, _ = state.client.get(path, params)
        except FetchError as exc:
            print(f"  WARN: still failing for {ticker} after cooldown: {exc}",
                  file=sys.stderr)
            state.rate_limited.add(ticker)
            state.aggs_cache[ticker] = []
            return []
    except FetchError as exc:
        print(f"  WARN: aggs for {ticker}: {exc}", file=sys.stderr)
        state.aggs_cache[ticker] = []
        return []
    finally:
        if state.sleep_between > 0:
            time.sleep(state.sleep_between)

    rows: list[dict] = []
    for r in doc.get("results") or []:
        ts_ms, close = r.get("t"), r.get("c")
        if ts_ms is None or close is None:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": d, "close": float(close)})
    rows.sort(key=lambda x: x["date"])
    state.aggs_cache[ticker] = rows
    return rows


# ----- Math -----

def _window_return(rows: list[dict], window_days: int) -> tuple[float | None, int]:
    if len(rows) < window_days + 1:
        return (None, len(rows))
    last = rows[-1]["close"]
    prior = rows[-(window_days + 1)]["close"]
    if prior <= 0 or last <= 0:
        return (None, len(rows))
    return ((last / prior) - 1.0, window_days)


def _aligned_returns(a: list[dict], b: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    amap = {r["date"]: r["close"] for r in a}
    bmap = {r["date"]: r["close"] for r in b}
    common = sorted(set(amap) & set(bmap))
    if len(common) < 3:
        return np.array([]), np.array([])
    av = np.array([amap[d] for d in common], dtype=float)
    bv = np.array([bmap[d] for d in common], dtype=float)
    ra = np.diff(av) / av[:-1]
    rb = np.diff(bv) / bv[:-1]
    return ra, rb


def _safe_corr(x: np.ndarray, y: np.ndarray) -> float | None:
    if x.size < 3 or y.size < 3 or x.size != y.size:
        return None
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return None
    c = float(np.corrcoef(x, y)[0, 1])
    if not np.isfinite(c):
        return None
    return c


def _rolling_corr(a: list[dict], b: list[dict], window: int) -> tuple[float | None, int]:
    ra, rb = _aligned_returns(a, b)
    n = int(min(ra.size, rb.size))
    if n < 3:
        return (None, n)
    use = min(window, n)
    return (_safe_corr(ra[-use:], rb[-use:]), use)


def _rolling_corr_vs_real_yield(
    commodity: list[dict], tip: list[dict], ief: list[dict], window: int
) -> tuple[float | None, int]:
    cmap = {r["date"]: r["close"] for r in commodity}
    tmap = {r["date"]: r["close"] for r in tip}
    imap = {r["date"]: r["close"] for r in ief}
    common = sorted(set(cmap) & set(tmap) & set(imap))
    if len(common) < 4:
        return (None, 0)
    cv = np.array([cmap[d] for d in common], dtype=float)
    tv = np.array([tmap[d] for d in common], dtype=float)
    iv = np.array([imap[d] for d in common], dtype=float)
    cr = np.diff(cv) / cv[:-1]
    tr = np.diff(tv) / tv[:-1]
    ir = np.diff(iv) / iv[:-1]
    spread = tr - ir
    n = int(cr.size)
    use = min(window, n)
    return (_safe_corr(cr[-use:], spread[-use:]), use)


def _real_yield_spread_return(tip: list[dict], ief: list[dict], window: int) -> float | None:
    tr, _ = _window_return(tip, window)
    ir, _ = _window_return(ief, window)
    if tr is None or ir is None:
        return None
    return tr - ir


def _momentum_quintile(rows: list[dict], window: int) -> tuple[int | None, float | None]:
    if len(rows) < window + 2:
        return (None, None)
    closes = [r["close"] for r in rows]
    series: list[float] = []
    for i in range(window, len(closes)):
        prior = closes[i - window]
        cur = closes[i]
        if prior > 0 and cur > 0:
            series.append((cur / prior) - 1.0)
    if len(series) < 5:
        return (None, None)
    trailing = series[-252:]
    current = series[-1]
    pct = percentile_rank(current, trailing)
    if pct is None:
        return (None, None)
    quintile = min(5, int(pct // 20) + 1)
    return (quintile, pct)


# ----- Signal composition -----

def _ordinal_quintile(q: int | None) -> str:
    return {1: "bottom", 2: "second", 3: "middle", 4: "fourth", 5: "top"}.get(
        q, "unknown"
    )


def _build_signals(ticker: str, rows: dict[str, list[dict]], window: int) -> dict:
    target = rows.get(ticker, [])

    dxy_corr, dxy_n = _rolling_corr(target, rows.get("UUP", []), window)
    ry_corr, ry_n = _rolling_corr_vs_real_yield(
        target, rows.get("TIP", []), rows.get("IEF", []), window
    )
    uup_ret, _ = _window_return(rows.get("UUP", []), window)
    ry_spread = _real_yield_spread_return(rows.get("TIP", []), rows.get("IEF", []), window)
    own_ret, _ = _window_return(target, window)
    quintile, pct = _momentum_quintile(target, window)

    dollar_effect = (dxy_corr * uup_ret) if (dxy_corr is not None and uup_ret is not None) else None
    real_yield_effect = (ry_corr * ry_spread) if (ry_corr is not None and ry_spread is not None) else None

    signals: dict = {
        "dxy_correlation": {
            "value": round(dxy_corr, 3) if dxy_corr is not None else None,
            "window_days": window,
            "n_obs": dxy_n,
            "uup_return": round(uup_ret, 4) if uup_ret is not None else None,
            "effect": round(dollar_effect, 4) if dollar_effect is not None else None,
            "label": (
                "unknown" if dxy_corr is None else
                "inverse to the dollar" if dxy_corr <= -0.3 else
                "moves with the dollar" if dxy_corr >= 0.3 else
                "dollar-neutral"
            ),
        },
        "real_yield_correlation": {
            "value": round(ry_corr, 3) if ry_corr is not None else None,
            "window_days": window,
            "n_obs": ry_n,
            "tip_minus_ief_return": round(ry_spread, 4) if ry_spread is not None else None,
            "real_yield_direction": (
                "unknown" if ry_spread is None else
                "falling" if ry_spread > 0.0 else
                "rising" if ry_spread < 0.0 else
                "flat"
            ),
            "effect": round(real_yield_effect, 4) if real_yield_effect is not None else None,
            "label": (
                "unknown" if ry_corr is None else
                "real-yield sensitive" if abs(ry_corr) >= 0.3 else
                "real-yield insensitive"
            ),
        },
        "momentum_quintile": {
            "quintile": quintile,
            "percentile": round(pct, 1) if pct is not None else None,
            "window_return": round(own_ret, 4) if own_ret is not None else None,
            "window_days": window,
            "label": (
                "unknown" if quintile is None else
                f"{_ordinal_quintile(quintile)} quintile"
            ),
        },
    }

    if ticker == "GLD":
        gdx = rows.get("GDX", [])
        slv = rows.get("SLV", [])
        gld_ret, _ = _window_return(target, window)
        gdx_ret, _ = _window_return(gdx, window)
        if gld_ret is not None and gdx_ret is not None:
            miner_rel = gdx_ret - gld_ret
            miner_label = (
                "miners leading (constructive)" if miner_rel > 0.01 else
                "miners lagging (warning)" if miner_rel < -0.01 else
                "miners in line"
            )
        else:
            miner_rel = None
            miner_label = "unknown"
        signals["miner_divergence"] = {
            "gdx_minus_gld_return": round(miner_rel, 4) if miner_rel is not None else None,
            "window_days": window,
            "label": miner_label,
        }
        slv_corr, slv_n = _rolling_corr(target, slv, window)
        signals["silver_comovement"] = {
            "value": round(slv_corr, 3) if slv_corr is not None else None,
            "window_days": window,
            "n_obs": slv_n,
            "label": (
                "unknown" if slv_corr is None else
                "broad metals move (high co-movement)" if slv_corr >= 0.6 else
                "gold-specific move (low co-movement)" if slv_corr < 0.3 else
                "moderate co-movement"
            ),
        }
    else:
        dbc = rows.get("DBC", [])
        dbc_corr, dbc_n = _rolling_corr(target, dbc, window)
        signals["broad_commodity_comovement"] = {
            "value": round(dbc_corr, 3) if dbc_corr is not None else None,
            "window_days": window,
            "n_obs": dbc_n,
            "reference": "DBC",
            "label": (
                "unknown" if dbc_corr is None else
                "moves with broad commodities" if dbc_corr >= 0.5 else
                "idiosyncratic vs broad commodities" if dbc_corr < 0.3 else
                "moderate co-movement with DBC"
            ),
        }
        signals["miner_divergence"] = None
        signals["silver_comovement"] = None

    return signals


def _compose_take(ticker: str, signals: dict) -> tuple[str, str]:
    name = COMMODITY_LABELS.get(ticker, ticker)

    dollar_effect = signals["dxy_correlation"]["effect"]
    real_yield_effect = signals["real_yield_correlation"]["effect"]
    quintile = signals["momentum_quintile"]["quintile"]

    votes = 0
    if dollar_effect is not None:
        if dollar_effect > 0.002:
            votes += 1
        elif dollar_effect < -0.002:
            votes -= 1
    if real_yield_effect is not None:
        if real_yield_effect > 0.002:
            votes += 1
        elif real_yield_effect < -0.002:
            votes -= 1
    if quintile is not None:
        if quintile >= 4:
            votes += 1
        elif quintile <= 2:
            votes -= 1
    miner = signals.get("miner_divergence")
    if miner and miner.get("gdx_minus_gld_return") is not None:
        mr = miner["gdx_minus_gld_return"]
        if mr > 0.01:
            votes += 1
        elif mr < -0.01:
            votes -= 1

    if votes > 0:
        label = "constructive"
    elif votes < 0:
        label = "headwind"
    else:
        label = "neutral"

    def _driver_phrase() -> str:
        d = signals["dxy_correlation"]
        r = signals["real_yield_correlation"]
        parts: list[str] = []
        ranked = []
        if dollar_effect is not None:
            ranked.append(("dollar", abs(dollar_effect)))
        if real_yield_effect is not None:
            ranked.append(("real_yield", abs(real_yield_effect)))
        ranked.sort(key=lambda x: x[1], reverse=True)
        if not ranked:
            return "no clean macro driver (insufficient data)"
        for kind, _mag in ranked:
            if kind == "dollar":
                direction = "strong" if (d["uup_return"] or 0) > 0 else "weak"
                corr = d["value"]
                corr_s = f"{corr:+.2f}" if corr is not None else "n/a"
                parts.append(f"a {direction} dollar ({d['window_days']}d corr {corr_s})")
            else:
                direction = r["real_yield_direction"]
                ry_phrase = (
                    "rising real yields" if direction == "rising" else
                    "falling real yields" if direction == "falling" else
                    "flat real yields"
                )
                corr = r["value"]
                corr_s = f"{corr:+.2f}" if corr is not None else "n/a"
                parts.append(f"{ry_phrase} ({r['window_days']}d corr {corr_s})")
        if len(parts) >= 2:
            return f"{parts[0]} plus {parts[1]}"
        return parts[0]

    driver = _driver_phrase()

    miner_clause = ""
    if miner and miner.get("label") not in (None, "unknown"):
        ml = miner["label"]
        if "leading" in ml:
            miner_clause = "; miners are leading, confirming strength"
        elif "lagging" in ml:
            miner_clause = "; miners are lagging, confirming weakness"
        else:
            miner_clause = "; miners are roughly in line"

    q_label = signals["momentum_quintile"]["label"]
    momentum_clause = (
        f" Momentum in the {q_label}." if q_label != "unknown" else
        " Momentum read unavailable."
    )

    take = (
        f"{name}: {label}. The dominant driver is {driver}{miner_clause}."
        f"{momentum_clause} "
        f"Cross-reference with fixed-income-context for the full cross-asset picture."
    )
    return label, take


# ----- Public API -----

def run(ticker: str = "GLD", window: int = 60, sleep: float = 0.0) -> dict:
    """Read the macro cycle for one commodity ETF."""
    if sleep < 0:
        raise ValueError("sleep cannot be negative")
    window = int(window)
    if window <= 0:
        raise ValueError("window must be > 0")

    state = _State(sleep)
    ticker = ticker.strip().upper()

    calendar_days = int(max(window, 252) * 1.6) + 14

    to_pull: list[str] = [ticker]
    for t in MACRO_CONTEXT:
        if t not in to_pull:
            to_pull.append(t)
    if ticker == "GLD":
        for t in ("GDX", "SLV"):
            if t not in to_pull:
                to_pull.append(t)
    else:
        if "DBC" not in to_pull:
            to_pull.append("DBC")

    rows: dict[str, list[dict]] = {}
    sources: list[dict] = []
    for t in to_pull:
        print(f"  Pulling daily aggs for {t}...", file=sys.stderr)
        rows[t] = _fetch_daily_aggs(state, t, calendar_days)
        sources.append({
            "endpoint": f"/v2/aggs/ticker/{t}/range/1/day/{{from}}/{{to}}",
            "fetched_at": utcnow_iso(),
            "context": f"daily closes for {t}",
        })

    signals = _build_signals(ticker, rows, window)
    label, take = _compose_take(ticker, signals)

    tier_caveats: list[str] = []
    if state.rate_limited:
        tier_caveats.append(
            f"RATE LIMIT: {len(state.rate_limited)} series "
            f"({', '.join(sorted(state.rate_limited))}) returned no data because the "
            f"API rate limit was hit, not because history is missing. Any signal "
            f"that uses them is UNKNOWN, not zero. Free Basic tier caps at 5 "
            f"calls/min: rerun with sleep=13 or upgrade to Stocks Starter."
        )
    if not rows.get(ticker):
        tier_caveats.append(
            f"No history returned for the target {ticker}. The take is not "
            f"reliable; check the ticker symbol and the tier."
        )
    tier_caveats.append(
        "Every driver is an ETF-return proxy. UUP proxies the dollar index, "
        "TIP minus IEF proxies real yields, GDX proxies gold miners. "
        "Directionally right, not the cash rate or the underlying curve."
    )
    tier_caveats.append(
        "Correlations are unstable: a 60-day window can flip sign around a "
        "macro regime change. Read the correlation alongside its driver's own "
        "move, not in isolation."
    )

    return {
        "skill": "commodity-cycle",
        "as_of": state.today.isoformat(),
        "fetched_at": utcnow_iso(),
        "ticker": ticker,
        "name": COMMODITY_LABELS.get(ticker, ticker),
        "window_days": window,
        "signals": signals,
        "setup": label,
        "take": take,
        "tier_caveats": tier_caveats,
        "sources": sources,
    }


# ----- Renderer -----

def render(payload: dict) -> str:
    s = payload["signals"]
    lines: list[str] = []
    lines.append(
        f"Commodity Cycle: {payload['name']} ({payload['ticker']}) "
        f"as of {payload['as_of']}"
    )
    lines.append("")
    lines.append(f"Macro setup: {payload['setup']}")
    lines.append("")
    lines.append(f"Drivers ({payload['window_days']}d):")

    dxy = s["dxy_correlation"]
    dxy_v = f"{dxy['value']:+.2f}" if dxy["value"] is not None else "n/a"
    lines.append(f"  Dollar (UUP) corr:     {dxy_v}  ({dxy['label']})")

    ry = s["real_yield_correlation"]
    ry_v = f"{ry['value']:+.2f}" if ry["value"] is not None else "n/a"
    lines.append(
        f"  Real-yield corr:       {ry_v}  ({ry['label']}, "
        f"real yields {ry['real_yield_direction']})"
    )

    miner = s.get("miner_divergence")
    if miner:
        mr = miner["gdx_minus_gld_return"]
        mr_s = f"{mr:+.2%}" if mr is not None else "n/a"
        lines.append(f"  Miner divergence:      {mr_s}  ({miner['label']})")

    slv = s.get("silver_comovement")
    if slv:
        slv_v = f"{slv['value']:+.2f}" if slv["value"] is not None else "n/a"
        lines.append(f"  Silver co-movement:    {slv_v}  ({slv['label']})")

    bcc = s.get("broad_commodity_comovement")
    if bcc:
        bcc_v = f"{bcc['value']:+.2f}" if bcc["value"] is not None else "n/a"
        lines.append(f"  Broad-commodity corr:  {bcc_v}  ({bcc['label']})")

    mom = s["momentum_quintile"]
    q = mom["quintile"]
    q_s = f"Q{q}" if q is not None else "n/a"
    ret = mom["window_return"]
    ret_s = f"{ret:+.2%}" if ret is not None else "n/a"
    lines.append(f"  Momentum quintile:     {q_s}  ({mom['label']}, return {ret_s})")

    lines.append("")
    lines.append("Take: " + payload["take"])
    if payload.get("tier_caveats"):
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"]:
            lines.append(f"  - {c}")
    return "\n".join(lines)
