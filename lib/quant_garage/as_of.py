"""
Single source of 'as-of' for a run.

Fixes H2 from the 2026-06-26 audit: scripts hardcoded TODAY at two
different dates (2026-06-23, 2026-06-24) while fetched_at used real
now. Anyone hand-coding from the docs would copy a stale date.

Behavior:
- `today()` returns date.today() by default
- Set QUANT_GARAGE_AS_OF=YYYY-MM-DD to freeze for reproducible runs
  (regression tests, snapshot comparisons, replaying a historical run)
- `utcnow_iso()` always returns the real wall-clock UTC timestamp;
  per-call provenance should not be frozen even when the universe date is
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone


def today() -> date:
    """The 'today' for the current run.

    Defaults to system date. Override with QUANT_GARAGE_AS_OF=YYYY-MM-DD
    for reproducible runs.
    """
    frozen = os.environ.get("QUANT_GARAGE_AS_OF")
    if frozen:
        return date.fromisoformat(frozen)
    return date.today()


def utcnow_iso() -> str:
    """Real-wall-clock UTC timestamp, ISO 8601 with `Z` suffix.

    Always returns the actual current time, regardless of any
    QUANT_GARAGE_AS_OF override. Per-call provenance should not be
    frozen even when the run's reference date is.

    The trailing `Z` (vs `+00:00`) makes UTC interpretation unambiguous
    and matches the common `Z$` regex JSON consumers use to detect UTC
    timestamps in serialized payloads.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
