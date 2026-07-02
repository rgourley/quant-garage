"""
Eastern timezone helpers using stdlib zoneinfo.

Fixes H1 from the 2026-06-26 audit: scripts hardcoded EASTERN = UTC-4
and ET_OFFSET_HOURS = -4 across slippage-cost, news-scanner,
portfolio-mark, and earnings. That's wrong from early November to
mid-March (EST is UTC-5). Bug shifts NBBO bars by an hour and
mis-buckets BMO/AMC sessions during the winter half of the year.

Use these helpers instead. zoneinfo does the DST math for free.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

# Standard US equity market hours (regular session).
_MARKET_OPEN_ET = time(9, 30)
_MARKET_CLOSE_ET = time(16, 0)


def utc_to_et(dt: datetime) -> datetime:
    """Convert a UTC datetime to ET. Handles both naive and aware inputs.

    Naive inputs are assumed to be UTC, matching the convention used by
    Massive's API timestamps (`t` fields are ns or ms since UTC epoch).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ET)


def et_now() -> datetime:
    """Current wall-clock time in ET."""
    return datetime.now(ET)


def is_market_hours_et(dt: datetime) -> bool:
    """True if dt falls within regular US equity market hours.

    Naive datetimes are assumed UTC and converted. Weekend dates always
    return False. Holiday calendar is NOT applied here; if a skill needs
    holiday-aware session checks, layer that on top.
    """
    et = utc_to_et(dt)
    if et.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    return _MARKET_OPEN_ET <= et.time() < _MARKET_CLOSE_ET
