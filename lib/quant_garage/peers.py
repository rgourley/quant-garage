"""
Peer selection for valuation comparison.

Massive's /v3/reference/tickers endpoint silently ignores the
sic_code query param and returns alphabetical results, so the
previous SIC-filter approach was broken: AACI, AACO, AAL, etc.
came back as 'biotech peers' for ALLO. This module uses the
/v1/related-companies endpoint as the primary path with
optional SIC cross-validation.

Fixes the C13 follow-up to the 2026-06-26 audit (peer-selection
correctness).
"""
from __future__ import annotations

from typing import Optional

from .client import MassiveClient, FetchError


def select_peers(
    client: MassiveClient,
    ticker: str,
    n: int = 8,
    validate_sic: bool = True,
) -> dict:
    """Return a peer set for `ticker` via /v1/related-companies.

    Optionally cross-checks each candidate's SIC code against the
    subject's SIC (validate_sic=True) and only keeps peers within
    the same 4-digit SIC. Tradeoff: stricter peers, more API calls.

    Args:
        client: MassiveClient instance for API calls.
        ticker: Subject ticker (will be uppercased).
        n: Maximum peer count to return.
        validate_sic: If True, fetch subject + each candidate's SIC
            and drop candidates whose SIC doesn't match the subject.

    Returns:
        {
          "peers": ["TICKER1", "TICKER2", ...],            # up to n
          "method": "related_companies" |
                    "related_companies_sic_validated",
          "subject_sic": "2836" | None,                    # None when
                                                            # validate_sic=False
                                                            # or subject lookup fails
          "n_candidates_pre_filter": int,                  # related-companies returned
          "n_dropped_sic_mismatch": int,                   # 0 when validate_sic=False
        }

    Raises:
        ValueError: No peers found (empty related list, or every
            candidate was filtered out by SIC validation). Caller
            should surface a clear error message and an override hint.
        FetchError: Underlying HTTP error from MassiveClient that
            wasn't a 404 on the related-companies endpoint.
    """
    subject = ticker.upper()
    candidates = _fetch_related(client, subject)
    # Drop self-reference if the API ever echoes the subject back
    candidates = [t for t in candidates if t and t != subject]
    n_candidates_pre_filter = len(candidates)

    if n_candidates_pre_filter == 0:
        raise ValueError(
            f"no related companies found for {subject} via "
            f"/v1/related-companies; pass --peers TICKER1,TICKER2,... to override"
        )

    if not validate_sic:
        return {
            "peers": candidates[:n],
            "method": "related_companies",
            "subject_sic": None,
            "n_candidates_pre_filter": n_candidates_pre_filter,
            "n_dropped_sic_mismatch": 0,
        }

    subject_sic = _fetch_sic(client, subject)
    if not subject_sic:
        # Subject has no SIC code on file; can't validate. Fall back to
        # the unvalidated method rather than raising, since the
        # related-companies set is still better than alphabetical garbage.
        return {
            "peers": candidates[:n],
            "method": "related_companies",
            "subject_sic": None,
            "n_candidates_pre_filter": n_candidates_pre_filter,
            "n_dropped_sic_mismatch": 0,
        }

    kept: list[str] = []
    dropped = 0
    for cand in candidates:
        cand_sic = _fetch_sic(client, cand)
        if cand_sic == subject_sic:
            kept.append(cand)
            if len(kept) >= n:
                # Stop fetching once we have enough; remaining candidates
                # are uncounted in n_dropped_sic_mismatch (the metric is
                # "of the ones we checked, how many didn't match").
                break
        else:
            dropped += 1

    if not kept:
        raise ValueError(
            f"no related companies for {subject} matched subject SIC "
            f"{subject_sic} (checked {n_candidates_pre_filter} candidates, "
            f"dropped {dropped}); pass --peers TICKER1,TICKER2,... to override"
        )

    return {
        "peers": kept,
        "method": "related_companies_sic_validated",
        "subject_sic": subject_sic,
        "n_candidates_pre_filter": n_candidates_pre_filter,
        "n_dropped_sic_mismatch": dropped,
    }


def _fetch_related(client: MassiveClient, ticker: str) -> list[str]:
    """GET /v1/related-companies/{ticker} -> list of ticker strings.

    Returns [] on 404 or when results is empty/missing. Raises
    FetchError on other HTTP errors.
    """
    try:
        doc, _ = client.get(f"/v1/related-companies/{ticker}")
    except FetchError as exc:
        if exc.status_code == 404:
            return []
        raise
    rows = doc.get("results") or []
    out: list[str] = []
    for r in rows:
        if isinstance(r, dict):
            t = r.get("ticker")
            if isinstance(t, str) and t:
                out.append(t)
    return out


def _fetch_sic(client: MassiveClient, ticker: str) -> Optional[str]:
    """GET /v3/reference/tickers/{ticker} -> sic_code or None.

    Returns None on 404, missing sic_code field, or any FetchError
    that isn't worth raising for a single-peer lookup. Logging the
    miss is the caller's call.
    """
    try:
        doc, _ = client.get(f"/v3/reference/tickers/{ticker}")
    except FetchError:
        return None
    results = doc.get("results") or {}
    sic = results.get("sic_code")
    if isinstance(sic, str) and sic:
        return sic
    return None
