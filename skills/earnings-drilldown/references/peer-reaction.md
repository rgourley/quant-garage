# Peer reaction

## What we measure

How sector peers traded on the same day as this name's past earnings
prints. Useful because a single-name surprise often moves the basket
(AAPL beats → semis rally; NVDA misses → semis sell off). The peer
reaction reveals whether this name is a leader or a follower, and
which specific peers track it most closely.

## Peer selection: get this right or the analysis is noise

SIC codes alone produce wrong peer baskets for any name where the
official classification doesn't match how traders think. AAPL's SIC
is 3571 (Electronic Computers), which maps to IBM/HPE/DELL/SMCI: the
wrong basket. Traders compare AAPL against NVDA/MSFT/GOOGL/AMZN/META
plus semis like TSM/AVGO. A SIC-only peer reaction analysis would
report movements of a basket the user does not care about.

Three-layer peer selection, in order:

### 1. Hand-curated override map (primary)

For the top ~25 names where SIC is unreliable, ship a curated map.
This is the highest-precision path and covers the names users actually
ask about. Examples:

```python
PEER_OVERRIDES = {
    "AAPL":  ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSM", "AVGO"],
    "NVDA":  ["AMD", "AVGO", "TSM", "MU", "ARM", "QCOM", "INTC"],
    "MSFT":  ["GOOGL", "AMZN", "META", "ORCL", "CRM", "AAPL", "NVDA"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL", "NFLX", "SNAP"],
    "META":  ["GOOGL", "SNAP", "PINS", "NFLX", "AMZN"],
    "AMZN":  ["GOOGL", "META", "MSFT", "AAPL", "SHOP", "WMT"],
    "TSLA":  ["NIO", "RIVN", "LCID", "F", "GM", "BYDDY"],
    # ... extend to top ~25 ticker
}
```

Maintain this list with reference to what sell-side analysts actually
group together. Update annually.

### 2. Correlation-based peers (fallback for uncurated names)

Pull daily returns over the last ~252 trading days for a candidate
universe (top 100 by market cap, or sector ETF holdings), compute
Pearson correlation of daily returns with the target, keep the top 10.

```python
def correlation_peers(ticker, universe, returns_by_ticker, n=10):
    target_returns = returns_by_ticker[ticker]
    correlations = []
    for peer in universe:
        if peer == ticker:
            continue
        peer_returns = returns_by_ticker.get(peer)
        if not peer_returns or len(peer_returns) < 200:
            continue
        r = pearson(target_returns, peer_returns)
        correlations.append((peer, r))
    correlations.sort(key=lambda x: -x[1])
    return [p for p, _ in correlations[:n]]
```

This is robust but requires ~100 extra agg fetches per run. Worth it.

### 3. Sector ETF holdings (nice-to-have)

If a Massive endpoint exposes ETF holdings (probe before relying on it),
use the relevant sector ETF's top holdings as the candidate universe:

- AAPL → XLK top holdings
- NVDA → SOXX or SMH top holdings
- JPM → XLF top holdings
- XOM → XLE top holdings

This is the cleanest universe but depends on endpoint availability.

### 4. SIC + industry filter (last resort)

For very small / niche names where neither override nor correlation
works (insufficient history, no analyst coverage), fall back to:

1. Pull `sic_code` from `/v3/reference/tickers/{ticker}`
2. Filter US-listed tickers with same `sic_code` and market cap > $1B
3. Cap at top 20 by market cap

Document when this fallback fires so the user knows the peer set is
weaker.

## Per-print peer return

Once a peer set is chosen, the rest is straightforward.

```
for each historical print at date d:
    if AMC: peer_return = (close(d+1) - close(d)) / close(d) for each peer
    if BMO: peer_return = (close(d) - close(d-1)) / close(d-1) for each peer

    surprise_sign = sign(eps_surprise_percent at d)  # from Benzinga

    if surprise_sign > 0:
        peer_returns_on_beats[peer].append(peer_return)
    else:
        peer_returns_on_misses[peer].append(peer_return)
```

The aggregate "peers traded X% on beats" is the average across all
peers across all beat-prints.

## Per-peer beta

The print-day beta tells us which peers move most when this name prints.

```
for each peer:
    name_returns = [print_day_return at each print]
    peer_returns = [peer's print_day_return at each print]
    beta = cov(peer_returns, name_returns) / var(name_returns)
```

Report the top 3 peers by absolute beta. A peer with beta 0.7 moves
70% as much as the name does on print day. Beta near 0 means the peer
ignores this name's prints. Beta > 1 happens when a peer is more
reactive than the lead name (often smaller name + larger lead).

## Why this matters

Three trade reads from peer reaction:

1. **Pairs setup**: high-beta peer with options that didn't price the
   move can be a cleaner expression than the lead name
2. **Sector confirmation**: if peer reaction historically aligns with
   the lead name's surprise direction, post-print sector trade is
   more reliable
3. **Outlier flag**: if peers diverged from this name on past prints,
   the read on this name's print is noisy and the take should
   downgrade confidence

## Edge cases

- **New IPO peers**: peer companies that IPO'd mid-window have shorter
  histories. Compute beta on the available overlap, flag if n_pairs < 4
- **Highly correlated prints**: if peers also report on the same day
  (semis often cluster), peer reaction reflects their own prints too.
  Filter to peers that didn't print within ±3 trading days of the
  target's print
- **Mergers / delistings**: peers acquired during the window drop out
  for prints after their delisting date

## Endpoints used

- `GET /v3/reference/tickers/{ticker}`: sector/industry/SIC classification
  for the SIC fallback path
- `GET /v2/aggs/ticker/{ticker}/range/1/day/...`: daily closes for
  correlation-based peer selection AND beta calculation
- Optionally: `GET /v3/reference/etf-holdings/{etf}` if Massive exposes
  this (probe before relying on it)

## What goes in the JSON

```json
{
  "peer_reaction": {
    "peer_selection_method": "curated_override",
    "peers_used": ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSM", "AVGO"],
    "n_peers": 7,
    "n_cycles": 8,
    "avg_peer_return_on_beat_pct": 0.011,
    "avg_peer_return_on_miss_pct": null,
    "top_peers": [
      { "ticker": "TSM", "beta": 0.58 },
      { "ticker": "NVDA", "beta": 0.51 },
      { "ticker": "AVGO", "beta": 0.36 }
    ]
  }
}
```

## Take generation

Peer reaction makes the take only when something unusual: a peer move
into the print wildly inconsistent with the lead name's setup, or a
streak of peer over-reactions suggesting the print is sector-defining.
Otherwise it lives in the cross-asset body section, not the headline.

## Tier-agnostic peer selection

The override map + correlation fallback methodology works identically
on Tier A (with Benzinga consensus) and Tier B (SEC EDGAR + Stocks-only).
The only tier-specific behavior is the bucketing: Tier A buckets by
surprise sign (beat vs miss), Tier B buckets by reaction sign. The
peer set itself is the same. Verified 2026-06-23 with the AAPL run:
the curated override (NVDA/MSFT/GOOGL/AMZN/META/TSM/AVGO) produced
the same top-3 by beta (TSM, META, NVDA) regardless of tier.
