# single-name-vs-sector methodology

The IP is the split into two relative-strength legs, the ticker-to-sector
map, the divergence score, and the classification rule. Everything is
descriptive math on real ETF and stock prices, chosen so a name-specific
move can be told apart from a sector move.

## The ticker to sector map

Each name maps to one SPDR select-sector ETF. The eleven funds cover the
GICS sectors:

| ETF | Sector |
|------|--------|
| XLK | Technology |
| XLF | Financials |
| XLE | Energy |
| XLV | Health Care |
| XLI | Industrials |
| XLY | Consumer Discretionary |
| XLP | Consumer Staples |
| XLU | Utilities |
| XLB | Materials |
| XLRE | Real Estate |
| XLC | Communication Services |

The built-in map holds roughly seventy large, liquid US names spanning all
eleven sectors. Assignment follows the GICS sector each name sits in inside
the SPDR funds, which has two traps worth stating:

- **AMZN and TSLA are Consumer Discretionary (XLY)**, not Technology. The
  select-sector funds classify them by GICS, and GICS puts them in XLY.
- **GOOGL, GOOG, META, and NFLX are Communication Services (XLC)**, not
  Technology. XLC was carved out of tech and telecom in 2018 and holds the
  internet-media names.

The map is a convenience, not a constraint. Any name not in the map runs
with `--sector`, and `--sector` also lets you re-map a name to a different
proxy (for example testing a conglomerate against a second sector). When a
ticker is unknown and no override is given, the tool errors with the list
of valid sector ETFs rather than guessing a sector.

## The three relative-strength legs

Per window, all in basis points so magnitudes compare across horizons:

- **name vs sector**: `(name_return - sector_return) * 10_000`. The core
  measurement: how far the name is running from its own sector.
- **sector vs benchmark**: `(sector_return - spy_return) * 10_000`. Is the
  sector itself leading or lagging the market.
- **name vs benchmark**: `(name_return - spy_return) * 10_000`. The plain
  relative-strength number, kept for cross-reference. It is the sum of the
  other two legs (subtractions telescope), so it ties the split back to the
  single number a relative-strength run would have shown.

Each leg gets the five-bucket trend label (improving, deteriorating,
stable_leader, stable_laggard, mixed) applied to its RS across windows,
the same scheme relative-strength uses.

## The divergence score

- **score_bps**: the name-vs-sector RS averaged across the windows, signed.
  Positive means the name is leading its sector on average; negative means
  it is lagging. This is "how far the name is running from its own sector"
  reduced to one number.
- **composite_bps**: the mean of the absolute name-vs-sector RS across
  windows. It measures the size of the gap regardless of direction, so a
  name that is far from its sector in either direction scores high. A large
  composite with a small signed score means the name is whipping around its
  sector rather than steadily diverging.

Windows with insufficient history contribute a null and are excluded from
both averages.

## The classification rule

Exactly three labels: `leading its sector`, `lagging its sector`,
`diverging`.

1. **diverging is checked first.** When the sign of the name-vs-sector
   average is the opposite of the sign of the sector-vs-benchmark average,
   and both clear their thresholds (name vs sector >= 25 bps, sector vs
   benchmark >= 10 bps in magnitude), the name is classified as diverging.
   This is the case the tool exists to catch: the name and its sector are
   pointing opposite ways, so the name's move is name-specific rather than
   a sector move. SOFI lagging XLF while XLF leads SPY is the canonical
   example.
2. **Otherwise** the label is the sign of the divergence score: >= 0 is
   `leading its sector`, < 0 is `lagging its sector`. When the name and its
   sector move the same way, the interesting question is just direction and
   magnitude, and the take reports whether the sector agrees (broad-based)
   or is quiet (name-specific).

The take line always names the window driving the name-vs-sector gap (the
largest absolute leg) and, for the sector clause, the window where the
sector most diverges from the benchmark, expressed in percentage points
(basis points / 100) to read like a desk note.

## Honest caveats

- **The sector ETF is a cap-weighted proxy, not a peer basket.** A few
  mega-caps dominate each fund. A name can read as diverging from its
  sector when it is really diverging from the ETF's largest holdings. For a
  true peer comparison, build a custom peer basket.
- **Thresholds are judgment calls**, set to filter noise, not estimated.
  A different set of windows may want different thresholds.
- **RS is past relative return, not predictive.** A name diverging from its
  sector is a description of what happened, not a forecast of mean
  reversion or continuation.
- **A rate-limited pull can drop the sector or benchmark leg and flip the
  classification.** The runtime flags rate-limited series loudly; never
  read the take without checking the caveats.
