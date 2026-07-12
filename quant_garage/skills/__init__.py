"""
Skill implementations as importable Python functions.

Each module exposes:
  - run(...) -> dict  — computes the canonical JSON payload.
  - render(payload: dict) -> str  — formats the payload as human-facing text.

Agents call run() directly. CLI wrappers under examples/run-*.py just
arg-parse and forward. Composed skills (stock-one-pager, scan-timerange)
import run() from here and stitch results together — no shelling out.

The dual-layer contract (Layer 1 canonical JSON + Layer 2 rendered text)
is what keeps token usage low: pipelines consume JSON cheaply, humans get
the rendered layer only when asked.
"""
from . import technical_briefing  # noqa: F401
from . import earnings_blackout  # noqa: F401
from . import market_regime  # noqa: F401
from . import stock_one_pager  # noqa: F401
from . import relative_strength  # noqa: F401
from . import pairs_scanner  # noqa: F401
from . import risk_factor_delta  # noqa: F401
from . import insider_flow  # noqa: F401
from . import eight_k_scanner  # noqa: F401
from . import news_scanner  # noqa: F401
from . import event_study  # noqa: F401
from . import pitch_comps  # noqa: F401
from . import valuation_sanity_check  # noqa: F401
try:
    from . import factor_research  # noqa: F401  (heavy: pandas + scipy required)
except ImportError:
    factor_research = None  # optional heavy skill; skip if pandas/scipy absent
from . import position_sizer  # noqa: F401
from . import risk_report  # noqa: F401
from . import corp_actions  # noqa: F401
from . import corporate_actions_scanner  # noqa: F401
from . import macro_event_calendar  # noqa: F401
from . import portfolio_rebalancer  # noqa: F401
from . import sector_rotation_signal  # noqa: F401
from . import historical_analog_finder  # noqa: F401
from . import options_structure_analyzer  # noqa: F401
from . import portfolio_review  # noqa: F401
from . import weekly_brief  # noqa: F401
from . import morning_brief  # noqa: F401
from . import preflight_trade  # noqa: F401
from . import earnings_week_prep  # noqa: F401
from . import historical_comparison  # noqa: F401
from . import scan_and_frame  # noqa: F401
from . import fixed_income_context  # noqa: F401
from . import slippage_cost  # noqa: F401
from . import t1_settlement_prep  # noqa: F401
try:
    from . import portfolio_mark  # noqa: F401  (optional: needs websocket-client for live mode)
except ImportError:
    portfolio_mark = None
try:
    from . import backtest_data_prep  # noqa: F401  (heavy: pandas + numpy required)
except ImportError:
    backtest_data_prep = None
from . import universe_builder  # noqa: F401
from . import options_flow  # noqa: F401
from . import crypto_vol_scanner  # noqa: F401
from . import earnings_drilldown  # noqa: F401
