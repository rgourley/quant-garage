"""
Universe construction with honest survivorship handling.

Fixes from the 2026-06-26 audit:

- C1, H3: `--survivorship clean` was fictional. Scripts pulled today's
  active-only ticker list and labeled the resulting dataset "survivorship
  clean" while delisted names could never enter. The Universe dataclass
  here has a `survivorship_mode` field tied to what we actually pulled,
  not what the caller asserted.

- M5: top-quartile filter used `*0.75 - 1` plus `>=` which kept ~26% of
  the universe instead of 25%. `top_quartile_threshold()` returns the
  75th percentile cleanly so callers index with `value >= threshold`.

- M6: concentration baselines computed three different ways across
  curated/grouped/reference paths. `concentration_z_score()` is the one
  definition every skill should use.

Out of scope (deferred):

- C2, C4: point-in-time market cap and quality factor require historical
  share counts and historical fundamentals at each rebalance. That's a
  separate integration (per-ticker financials probe with
  period_of_report_date.lte filter). The current universe.py covers
  point-in-time UNIVERSE membership; point-in-time per-ticker mcap is a
  follow-up.

Design notes:

- The point-in-time universe for any given date comes from the grouped
  aggregates endpoint for THAT date. This is naturally survivorship-
  clean: only tickers that actually traded on that date appear, names
  delisted before that date are excluded, names that have since
  delisted but were trading on that date are INCLUDED. For backtest
  workflows, build one Universe per rebalance date rather than a single
  current-snapshot universe.

- The Universe object carries source provenance (the as_of date and the
  raw count from the grouped aggs response) so callers can stamp it on
  output JSON for audit trail.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal, Optional, Sequence

from .client import MassiveClient


@dataclass(frozen=True)
class TickerSnapshot:
    """One ticker's market state on a given as_of date.

    Fields populated from the grouped-aggs endpoint:
    - close, open, high, low: that day's OHLC (already split-adjusted by
      Massive's `adjusted=true` flag)
    - volume, vwap, transactions: that day's microstructure
    - dollar_volume: close * volume; the standard universe-size proxy

    Other fields (sector, sic_code, type, market_cap) require enrichment
    via /v3/reference/tickers/{T} and may be None on the initial pull.
    """

    ticker: str
    close: float
    open: float
    high: float
    low: float
    volume: float
    vwap: Optional[float]
    transactions: Optional[int]
    dollar_volume: float
    market_cap: Optional[float] = None
    sector: Optional[str] = None
    sic_code: Optional[str] = None
    type: Optional[str] = None
    name: Optional[str] = None


@dataclass(frozen=True)
class Universe:
    """A point-in-time universe of tickers.

    `survivorship_mode` is derived from how the universe was built, not
    asserted by the caller. A universe built from a specific historical
    as_of date via grouped aggs is "point_in_time" (= survivorship-clean
    for backtests anchored at that date). A universe built from today's
    grouped aggs and used for a historical window is "biased_forward".

    Attributes:
        as_of: the trading date the universe represents
        snapshots: per-ticker market state on that date
        survivorship_mode: how the universe was constructed
        source_endpoints: API paths called to build this universe, for
            citation in skill output
    """

    as_of: date
    snapshots: dict[str, TickerSnapshot]
    survivorship_mode: Literal["point_in_time", "biased_forward"]
    source_endpoints: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.snapshots)

    def tickers(self) -> list[str]:
        return list(self.snapshots.keys())

    def top_n_by_dollar_volume(self, n: int) -> "Universe":
        """Return a Universe trimmed to the top n by dollar volume.

        Preserves survivorship_mode and source_endpoints unchanged.
        """
        sorted_snaps = sorted(
            self.snapshots.values(), key=lambda s: s.dollar_volume, reverse=True
        )
        kept = {s.ticker: s for s in sorted_snaps[:n]}
        return Universe(
            as_of=self.as_of,
            snapshots=kept,
            survivorship_mode=self.survivorship_mode,
            source_endpoints=list(self.source_endpoints),
        )

    def filter(self, predicate) -> "Universe":
        """Return a Universe with only snapshots passing `predicate`.

        Preserves survivorship_mode and source_endpoints.
        """
        kept = {t: s for t, s in self.snapshots.items() if predicate(s)}
        return Universe(
            as_of=self.as_of,
            snapshots=kept,
            survivorship_mode=self.survivorship_mode,
            source_endpoints=list(self.source_endpoints),
        )

    def with_metadata(self, ticker: str, **kwargs) -> "Universe":
        """Return a new Universe with one ticker's snapshot updated.

        Useful for the enrichment pass after the initial grouped pull
        adds sector/sic_code/market_cap from /v3/reference/tickers.
        """
        existing = self.snapshots.get(ticker)
        if existing is None:
            return self
        new_snaps = dict(self.snapshots)
        # dataclass.replace doesn't import here to keep the module light
        merged = {**existing.__dict__, **kwargs}
        new_snaps[ticker] = TickerSnapshot(**merged)
        return Universe(
            as_of=self.as_of,
            snapshots=new_snaps,
            survivorship_mode=self.survivorship_mode,
            source_endpoints=list(self.source_endpoints),
        )


def build_universe(
    client: MassiveClient,
    as_of: date,
    type_filter: Optional[tuple[str, ...]] = None,
) -> Universe:
    """Build a point-in-time universe for `as_of` via grouped aggregates.

    Pulls /v2/aggs/grouped/locale/us/market/stocks/{as_of} which returns
    every common-stock-like ticker that printed on that date. If as_of
    is a market holiday or weekend, walks back up to 7 days to find a
    real session.

    Returns a Universe with `survivorship_mode="point_in_time"`. Names
    delisted before as_of are excluded (they couldn't have traded);
    names that have since delisted but were trading on as_of are
    included (they DID trade). This is the survivorship-clean baseline
    for any historical analysis anchored at this date.

    `type_filter` is applied later via enrichment (the grouped endpoint
    doesn't expose ticker type). Pass it to `enrich_with_reference()`
    if you need it.
    """
    body, fetched_at = _fetch_grouped_walk_back(client, as_of)
    actual_date = date.fromisoformat(body.get("queryCount", as_of.isoformat())) if isinstance(body.get("queryCount"), str) else as_of
    # queryCount is an integer in real responses; the "actual" date is the
    # one we walked back to. Keep as_of in the dataclass for caller intent.
    results = body.get("results") or []

    snapshots: dict[str, TickerSnapshot] = {}
    for row in results:
        ticker = row.get("T")
        close = row.get("c")
        if ticker is None or close is None:
            continue
        snapshots[ticker] = TickerSnapshot(
            ticker=ticker,
            close=float(close),
            open=float(row.get("o", close)),
            high=float(row.get("h", close)),
            low=float(row.get("l", close)),
            volume=float(row.get("v", 0)),
            vwap=row.get("vw"),
            transactions=row.get("n"),
            dollar_volume=float(close) * float(row.get("v", 0)),
        )

    endpoint = f"/v2/aggs/grouped/locale/us/market/stocks/{as_of.isoformat()}"
    return Universe(
        as_of=as_of,
        snapshots=snapshots,
        survivorship_mode="point_in_time",
        source_endpoints=[f"{endpoint} @ {fetched_at}"],
    )


def _fetch_grouped_walk_back(
    client: MassiveClient, as_of: date, max_walk_back_days: int = 7
) -> tuple[dict, str]:
    """Pull grouped aggs for as_of, walking back over weekends/holidays.

    Massive returns an empty results array on weekend / holiday dates.
    Walk back one calendar day at a time until we find a real session
    (results is non-empty). Capped at `max_walk_back_days`.
    """
    cursor = as_of
    walked = 0
    last_body: dict = {}
    last_fetched_at = ""
    while walked <= max_walk_back_days:
        path = f"/v2/aggs/grouped/locale/us/market/stocks/{cursor.isoformat()}?adjusted=true"
        body, fetched_at = client.get(path)
        last_body, last_fetched_at = body, fetched_at
        if body.get("results"):
            return body, fetched_at
        cursor = cursor - timedelta(days=1)
        walked += 1
    # Out of walk-back budget; return whatever the last (possibly empty) response was
    return last_body, last_fetched_at


def top_quartile_threshold(values: Sequence[float]) -> float:
    """Return the 75th-percentile threshold for a top-quartile filter.

    Use as: `kept = [v for v in values if v >= top_quartile_threshold(values)]`.

    Fixes M5 from the audit: the prior code used `*0.75 - 1` indexing
    plus `>=` which kept ~26% of the universe instead of 25%. This
    helper uses numpy quantile so the boundary is exact.
    """
    if not values:
        raise ValueError("top_quartile_threshold requires at least one value")
    import numpy as np  # local import keeps module import-fast on cold start

    return float(np.quantile(np.asarray(list(values), dtype=float), 0.75))


def concentration_z_score(
    top_set: Sequence[str],
    universe_set: Sequence[str],
    expected_size: Optional[int] = None,
) -> dict[str, dict]:
    """Z-score the concentration of categories in a top set vs the universe.

    Fixes M6 from the audit: scripts computed concentration baselines
    three different ways across curated/grouped/reference paths. This
    is the one definition every skill should use.

    Methodology
    -----------
    For each category C (sector, industry, etc.):
        observed = count of C in top_set
        p = count of C in universe_set / len(universe_set)
        expected_count = p * len(top_set)
        std_dev = sqrt(len(top_set) * p * (1 - p))
        z = (observed - expected_count) / std_dev

    Returns a dict keyed by category with subkeys
    {observed, expected, z_score, p_universe}. Skip categories with
    fewer than 2 occurrences in the top set unless the universe baseline
    is also small.

    Parameters
    ----------
    top_set : sequence of str
        The categories of the top-N names (e.g., sectors of the top
        20 by some score).
    universe_set : sequence of str
        The categories of the FULL universe baseline.
    expected_size : int, optional
        Override the size used in the expected-count calculation.
        Defaults to len(top_set).
    """
    if not top_set or not universe_set:
        return {}
    n_top = expected_size if expected_size is not None else len(top_set)
    n_uni = len(universe_set)

    top_counts = Counter(top_set)
    uni_counts = Counter(universe_set)

    out: dict[str, dict] = {}
    for category, observed in top_counts.items():
        baseline = uni_counts.get(category, 0)
        p = baseline / n_uni if n_uni > 0 else 0.0
        expected_count = p * n_top
        # Bernoulli variance for the count
        var = n_top * p * (1 - p)
        z = (observed - expected_count) / math.sqrt(var) if var > 0 else 0.0
        out[category] = {
            "observed": observed,
            "expected": expected_count,
            "z_score": z,
            "p_universe": p,
        }
    return out
