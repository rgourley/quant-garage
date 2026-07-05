"""
Canonical price-fallback chain for Massive's v2 snapshot response.

Fixes D4 and D5 from the 2026-06-26 audit:

- D4: api-patterns docs referenced `snapshot.last.price` and
  `snapshot.min.c` as top-level fields. The real v2 response nests
  everything under `ticker.{lastTrade, min, day, prevDay}`. Anyone
  hand-coding from the docs would get None on every lookup.

- D5: the documented 5-step chain had `snapshot.last.price` as step 1
  and `snapshot.lastTrade.p` as step 2. They were the same field;
  step 1 always returned None. Chain is 4 steps, not 5.

The chain below uses real paths and is what portfolio-mark already
implements correctly. Centralizing it here so other skills (notably
`earnings-drilldown` and any new tool that needs a current price)
inherit the fix.

Resolution order:
    1. lastTrade.p  most recent trade across exchanges
    2. min.c        current minute bar close (intraday only)
    3. day.c        today's session close
    4. prevDay.c    previous session close (off-hours / quiet names)

Zero is treated as an unpopulated field, not a valid price. The v2
snapshot endpoint returns 0 for intraday sections (lastTrade.p, min.c,
day.c) outside market hours or when the tape hasn't yet ticked;
accepting 0 as a valid resolution silently gave every downstream skill
a bogus $0.00 price. The chain now walks past zeros to the next
populated field (typically prevDay.c during holidays / pre-market).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class PriceResolution:
    """Result of walking the price-fallback chain.

    Attributes
    ----------
    price : float | None
        The resolved price. None if every chain step returned null
        (delisted, never-traded, or response missing the ticker key).
    source : str
        Which chain step won. One of: "lastTrade", "min.c", "day.c",
        "prevDay.c", "no_price".
    timestamp_ns : int | None
        Nanosecond epoch when the source field was last updated, if
        the response carries it. Daily fields (day.c, prevDay.c) don't
        always carry a ts.
    """

    price: Optional[float]
    source: str
    timestamp_ns: Optional[int]


def resolve_price(snapshot_response: dict[str, Any]) -> PriceResolution:
    """Walk the snapshot fallback chain on a v2/snapshot/locale/.../tickers/{T} response.

    Expects the parsed JSON body. The response is shaped like::

        {
          "status": "OK",
          "ticker": {
            "lastTrade": {"p": 297.39, "t": 1751148723000000000, ...},
            "min":       {"c": 297.40, "t": ..., ...},
            "day":       {"c": 297.50, ...},
            "prevDay":   {"c": 296.10, ...}
          }
        }

    Returns a `PriceResolution` capturing which step won. Callers should
    inspect `.source` to surface confidence (lastTrade in market hours =
    high; prevDay = low).

    Zero is rejected as unpopulated. See the module docstring for why.
    """
    ticker = snapshot_response.get("ticker") or {}

    last_trade = ticker.get("lastTrade") or {}
    price = last_trade.get("p")
    if price is not None and float(price) > 0:
        return PriceResolution(price=float(price), source="lastTrade", timestamp_ns=last_trade.get("t"))

    minute = ticker.get("min") or {}
    price = minute.get("c")
    if price is not None and float(price) > 0:
        return PriceResolution(price=float(price), source="min.c", timestamp_ns=minute.get("t"))

    day = ticker.get("day") or {}
    price = day.get("c")
    if price is not None and float(price) > 0:
        return PriceResolution(price=float(price), source="day.c", timestamp_ns=day.get("t"))

    prev_day = ticker.get("prevDay") or {}
    price = prev_day.get("c")
    if price is not None and float(price) > 0:
        return PriceResolution(price=float(price), source="prevDay.c", timestamp_ns=prev_day.get("t"))

    return PriceResolution(price=None, source="no_price", timestamp_ns=None)
