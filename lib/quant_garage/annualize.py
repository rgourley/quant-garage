"""
Annualization helpers for fundamentals.

Both pitch-comps and valuation-sanity-check pull quarterly or LTM
financials and need to land at an annualized D&A, operating income,
EBITDA, etc. Doing the math in one place keeps EV/EBITDA, P/E, and
EBITDA-margin numbers consistent across surfaces.

Source tags returned by these helpers ('LTM' vs 'Q4' vs 'unavailable')
should land in the per-ticker JSON output (`da_source`,
`op_income_source`) so consumers can audit which fallback was used.

Fixes H5 from the 2026-06-26 audit.
"""
from __future__ import annotations

import math
from typing import Sequence


def _is_finite_number(v: object) -> bool:
    if v is None:
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def ltm_sum(quarterly_values: Sequence[float | None]) -> float | None:
    """Sum the four most recent quarterly values for an LTM estimate.

    `quarterly_values` is expected to be reverse-chronological (most
    recent first). Returns None if fewer than 4 valid (non-None, finite)
    values are present in the first four slots. Callers should surface
    'insufficient_quarters' in tier_caveats rather than emitting a
    partial-LTM estimate.
    """
    if quarterly_values is None:
        return None
    head = list(quarterly_values)[:4]
    if len(head) < 4:
        return None
    if not all(_is_finite_number(v) for v in head):
        return None
    return float(sum(float(v) for v in head))


def annualize_quarter(quarterly_value: float | None) -> float | None:
    """Annualize a single quarterly value via x4.

    Less accurate than LTM (assumes flat seasonality) and should only be
    used when LTM is unavailable. Callers should flag the annualized
    estimate with source='Q4' in audit-trail fields.
    """
    if not _is_finite_number(quarterly_value):
        return None
    return float(quarterly_value) * 4.0


def _val(node: dict | None, key: str) -> float | None:
    """Pull `.value` from a Massive financials field, safe against null."""
    sub = (node or {}).get(key)
    if not sub:
        return None
    v = sub.get("value") if isinstance(sub, dict) else None
    return float(v) if _is_finite_number(v) else None


def operating_income(financials: dict) -> float | None:
    """Pull operating income from a single fundamentals record.

    Tries the canonical `operating_income_loss` field on the income
    statement first; falls back to `revenues - cost_of_revenue -
    operating_expenses` if the components are present. Returns None if
    neither path resolves. Source is documented at the call site (this
    helper just returns the numeric value).
    """
    if not isinstance(financials, dict):
        return None
    inc = financials.get("income_statement") or {}
    direct = _val(inc, "operating_income_loss")
    if direct is not None:
        return direct
    revenues = _val(inc, "revenues")
    cogs = _val(inc, "cost_of_revenue")
    if cogs is None:
        cogs = _val(inc, "costs_and_expenses")
    opex = _val(inc, "operating_expenses")
    if revenues is None or cogs is None or opex is None:
        return None
    return revenues - cogs - opex


def _extract_da_quarters(financials_history: list[dict]) -> list[float | None]:
    """Return per-quarter D&A values in the order given.

    Prefers cash_flow_statement.depreciation_and_amortization (the canonical
    location); falls back to the income_statement equivalent if cash flow
    is missing for a given quarter. None marks a quarter with no D&A
    reported anywhere.
    """
    out: list[float | None] = []
    for r in financials_history or []:
        fin = (r or {}).get("financials") or {}
        cfs = fin.get("cash_flow_statement") or {}
        inc = fin.get("income_statement") or {}
        v = _val(cfs, "depreciation_and_amortization")
        if v is None:
            v = _val(inc, "depreciation_and_amortization")
        out.append(v)
    return out


def da_annualized(financials_history: list[dict]) -> tuple[float | None, str]:
    """Return (annualized D&A, source_tag).

    source_tag is one of:
      'LTM'         - sum of 4 most recent quarters of D&A
      'Q4'          - most recent quarter * 4 (flat-seasonality estimate)
      'unavailable' - no D&A reported on any of the supplied quarters

    Prefers LTM. Falls back to most-recent-quarter * 4 with a caveat so
    the caller can surface the lower-confidence source. Callers should
    write `da_source` into the per-ticker output for auditability.
    """
    quarters = _extract_da_quarters(financials_history)
    if not quarters:
        return None, "unavailable"
    ltm = ltm_sum(quarters)
    if ltm is not None:
        return ltm, "LTM"
    # No 4-quarter LTM available; try most-recent-quarter x 4.
    for v in quarters:
        if _is_finite_number(v):
            return annualize_quarter(v), "Q4"
    return None, "unavailable"


def operating_income_annualized(
    financials_history: list[dict],
) -> tuple[float | None, str]:
    """Return (annualized operating income, source_tag).

    source_tag is one of:
      'LTM'         - sum of 4 most recent quarters of operating income
      'Q4'          - most recent quarter * 4 (flat-seasonality estimate)
      'unavailable' - no operating income resolvable on any quarter

    Uses `operating_income()` per quarter (canonical field first,
    revenue-cogs-opex fallback second). Mirrors `da_annualized` so the
    two scripts that share this lib produce identical numbers for the
    same input financials.
    """
    if not financials_history:
        return None, "unavailable"
    per_quarter: list[float | None] = []
    for r in financials_history:
        fin = (r or {}).get("financials") or {}
        per_quarter.append(operating_income(fin))
    ltm = ltm_sum(per_quarter)
    if ltm is not None:
        return ltm, "LTM"
    for v in per_quarter:
        if _is_finite_number(v):
            return annualize_quarter(v), "Q4"
    return None, "unavailable"
