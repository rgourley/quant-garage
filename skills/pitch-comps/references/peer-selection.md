# Peer selection

How the comp set is chosen. Get this wrong and the whole table is
noise. This skill uses the same three-layer waterfall as
`earnings-drilldown`'s peer-reaction methodology (see
[`../../earnings-drilldown/references/peer-reaction.md`](../../earnings-drilldown/references/peer-reaction.md)
for the parent reference). Keep the override maps in sync.

## Why the SIC code alone fails

SIC was designed for census reporting in the 1930s. It groups
prepackaged-software firms together (Salesforce, Oracle, Adobe, Intuit,
ServiceNow all map to SIC 7372), which is fine for the software comp
set but breaks for any name where the official classification doesn't
match how traders think:

- AAPL → SIC 3571 (Electronic Computers), peers IBM/HPE/DELL. Traders
  compare AAPL to NVDA/MSFT/GOOGL/AMZN/META.
- PANW → SIC 3577 (Computer Peripheral Equipment) even though it's a
  cybersecurity software company. Peers should be CRWD/FTNT/ZS.
- TSLA → SIC 3711 (Motor Vehicles & Passenger Car Bodies) groups it
  with F and GM. The market trades it against NIO/RIVN/LCID for the
  EV-pure-play take, and against the magnificent seven for the
  growth-multiple take.

A pitch-deck comp page that derives peers from SIC alone produces a
table the banker can't defend in an MD review.

## The three layers

### 1. Curated override map (primary)

For the top ~30 US names where SIC misclassifies or where the trader
consensus is well-defined, ship a hand-curated map. Highest precision,
covers the names users actually ask about.

```python
PEER_OVERRIDES = {
    # Software majors (the test case for this skill)
    "CRM":  ["ORCL", "SAP", "NOW", "WDAY", "ADBE", "INTU", "PANW", "CRWD"],
    "ORCL": ["CRM", "SAP", "MSFT", "ADBE", "NOW", "WDAY", "INTU"],
    "ADBE": ["CRM", "ORCL", "INTU", "NOW", "WDAY", "SAP"],
    "NOW":  ["CRM", "WDAY", "ADBE", "ORCL", "INTU", "PANW"],
    "WDAY": ["CRM", "NOW", "ADBE", "INTU", "ORCL"],
    "INTU": ["CRM", "ADBE", "ORCL", "NOW", "WDAY"],
    "PANW": ["CRWD", "FTNT", "ZS", "S", "CHKP", "OKTA"],
    "CRWD": ["PANW", "FTNT", "ZS", "S", "OKTA"],

    # Mega-cap tech (mirrored from earnings-drilldown's override map)
    "AAPL":  ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSM", "AVGO"],
    "NVDA":  ["AMD", "AVGO", "TSM", "MU", "ARM", "QCOM", "INTC"],
    "MSFT":  ["GOOGL", "AMZN", "META", "ORCL", "CRM", "AAPL"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL", "NFLX", "SNAP"],
    "META":  ["GOOGL", "SNAP", "PINS", "NFLX", "AMZN"],
    "AMZN":  ["GOOGL", "META", "MSFT", "AAPL", "SHOP", "WMT"],
    "TSLA":  ["NIO", "RIVN", "LCID", "F", "GM"],

    # Banks
    "JPM": ["BAC", "WFC", "C", "GS", "MS"],
    "GS":  ["MS", "JPM", "BAC", "C"],

    # Payments
    "V":  ["MA", "PYPL", "AXP"],
    "MA": ["V", "PYPL", "AXP"],

    # Pharma
    "LLY":  ["NVO", "PFE", "MRK", "ABBV", "BMY", "AMGN"],
    "MRK":  ["LLY", "PFE", "ABBV", "BMY", "AMGN", "JNJ"],
    "NVO":  ["LLY", "PFE", "MRK", "ABBV"],

    # Energy
    "XOM": ["CVX", "COP", "EOG", "OXY"],
    "CVX": ["XOM", "COP", "EOG", "OXY"],
}
```

Maintain this list with reference to what sell-side analysts already
group together. Update annually as the trading-comp consensus shifts
(e.g. SHOP joined the MSFT/GOOGL "rule of 40" software cohort circa
2022; PLTR did circa 2024). The override map in this skill should
match earnings-drilldown's override map for the names that appear in
both; cross-reference on every update.

### 2. Correlation-based (uncurated names)

When the subject is not in the override map, fall back to correlation.
Pull daily returns over the trailing 252 trading days for a candidate
pool (top 100 by market cap is the default; sector-ETF holdings work
when available). Compute Pearson correlation of daily returns with the
subject; keep the top 6-8.

```python
def correlation_peers(subject, candidate_universe, returns_by_ticker, n=6):
    target = returns_by_ticker.get(subject)
    if not target or len(target) < 200:
        return None  # not enough history; fall to SIC
    out = []
    for tk in candidate_universe:
        if tk == subject:
            continue
        peer_ret = returns_by_ticker.get(tk)
        if not peer_ret or len(peer_ret) < 200:
            continue
        r = pearson(target, peer_ret)
        out.append((tk, r))
    out.sort(key=lambda x: -x[1])
    return [tk for tk, _ in out[:n]]
```

Cost: roughly 100 extra aggregate-bars calls per run. On Tier A (Starter+)
this is acceptable. On Tier B (Basic) the rate limit makes this
prohibitive; the skill skips the correlation layer on Tier B and falls
straight to SIC.

### 3. SIC fallback (last resort)

When neither the override map nor correlation produces a usable set
(small / new / illiquid name without 200 days of price history):

1. Pull `sic_code` from `/v3/reference/tickers/{subject}`.
2. Paginate `/v3/reference/tickers?market=stocks&active=true&type=CS`
   and keep names with the same `sic_code` and market cap > $1B.
3. Sort by market cap descending; cap at the top 8.
4. Record the fallback in the JSON so the consumer knows the peer set
   is weaker.

For software (SIC 7372) this happens to produce a reasonable result.
For cybersecurity software (SIC 3577) it doesn't, which is why
hand-curation is the primary layer.

## Recording the method

The output JSON includes `peer_selection.method` ∈ `{curated_override,
correlation, sic_fallback}` and `peer_selection.n_peers`. The rendered
header includes a one-line note: `8 peers selected via curated_override`
or `6 peers selected via correlation (top 6 by 1-year daily return ρ)`.
This is how the reader knows whether to trust the peer set without
opening the JSON.

## Edge cases

- **Subject in its own peer list.** Filter out so the override map is
  symmetric without polluting the comp set.
- **Foreign issuer ADRs.** SAP, ASML, NVO often have empty financials
  in Massive's endpoint (the data is filed with home-country regulators
  in different formats). Keep them in the peer set with `null`
  multiples so the framing is honest ("CRM trades vs. these names"),
  but exclude them from summary stats and the regression. Document
  `data_status: "empty"` in the peer's JSON entry.
- **Recent IPOs with no four full quarters yet.** The growth metric
  needs 8 quarters of revenue. Subject or peers with less than 8
  quarters of revenue get `revenue_growth_ttm: null` and drop out of
  the regression (but stay in the table with current-period multiples).
- **Subject = peer.** When pitching `CRM` as comp set for `ORCL` and
  vice versa, the maps are reciprocal. The renderer just shows the
  subject-vs-peers framing; the peer list always excludes the subject.

## Why this matters for the rendered output

The first line of the rendered header reads:

```
CRM: comp set as of 2026-06-23 · 8 peers selected via curated_override
```

That `curated_override` is the trust marker. A banker reading the
table knows immediately whether to defend the peer set in an MD review
or to push back. The other two methods get their own marker:

```
SHOP: comp set as of 2026-06-23 · 6 peers selected via correlation (top 6 by 1Y daily ρ)
SMCI: comp set as of 2026-06-23 · 8 peers selected via sic_fallback (SIC 3674)
```

The renderer reads the `peer_selection` block in the JSON and emits
the right marker without the user needing to know the methodology.
