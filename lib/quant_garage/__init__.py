"""
quant_garage shared client and helpers.

Each script in examples/ should import from here instead of rolling
its own auth, retry, timezone, or snapshot-resolution logic. The
goal is to fix infrastructure bugs in one place and have every
skill inherit the fix.

What lives here:
- `client.MassiveClient`        one auth scheme, one host, next_url
                                pagination, retry on transient
                                errors, fetched_at populated per call.
- `timezones.ET`                zoneinfo America/New_York, DST-correct.
                                Use this instead of hardcoded UTC-4.
- `as_of.today()`               single source of "today" for the whole
                                run. Overridable via QUANT_GARAGE_AS_OF
                                env var for reproducible runs.
- `snapshot.resolve_price`      canonical price-fallback chain over the
                                v2/snapshot response shape. Correct paths
                                (snapshot.ticker.lastTrade.p etc.) per
                                the 2026-06-26 audit (D4, D5).

Importing from these modules instead of copy-pasting prevents the
drift the v0.1 scripts accumulated.
"""

from .client import MassiveClient, FetchError, RateLimited
from .timezones import ET, utc_to_et, et_now, is_market_hours_et
from .as_of import today, utcnow_iso
from .snapshot import resolve_price, PriceResolution
from .stats import critical_t, is_significant, newey_west_se, spearman_ic, winsorize
from .universe import (
    Universe,
    TickerSnapshot,
    build_universe,
    top_quartile_threshold,
    concentration_z_score,
)
from .output import OutputFormat, resolve_output_format, emit_to_stdout
from .annualize import (
    ltm_sum,
    annualize_quarter,
    operating_income,
    operating_income_annualized,
    da_annualized,
)
from .percentile import percentile_rank, format_rank_label, base_rate
from .monte_carlo import (
    sample_empirical,
    sample_normal,
    spearman_sensitivity,
    percentile_summary,
    DistributionKind,
)
from .sizing import (
    annualized_vol,
    correlation_matrix,
    covariance_matrix,
    shrink_correlation,
    vol_target_weights,
    fractional_kelly_weights,
    risk_parity_weights,
    equal_weights,
)

__all__ = [
    "MassiveClient",
    "FetchError",
    "RateLimited",
    "ET",
    "utc_to_et",
    "et_now",
    "is_market_hours_et",
    "today",
    "utcnow_iso",
    "resolve_price",
    "PriceResolution",
    "critical_t",
    "is_significant",
    "newey_west_se",
    "spearman_ic",
    "winsorize",
    "Universe",
    "TickerSnapshot",
    "build_universe",
    "top_quartile_threshold",
    "concentration_z_score",
    "OutputFormat",
    "resolve_output_format",
    "emit_to_stdout",
    "ltm_sum",
    "annualize_quarter",
    "operating_income",
    "operating_income_annualized",
    "da_annualized",
    "percentile_rank",
    "format_rank_label",
    "base_rate",
    "sample_empirical",
    "sample_normal",
    "spearman_sensitivity",
    "percentile_summary",
    "DistributionKind",
    "annualized_vol",
    "correlation_matrix",
    "covariance_matrix",
    "shrink_correlation",
    "vol_target_weights",
    "fractional_kelly_weights",
    "risk_parity_weights",
    "equal_weights",
]
