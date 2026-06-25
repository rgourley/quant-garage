# Example: event-study cross-section across mega-cap tech earnings

Real data run: the five largest US tech names (AAPL, NVDA, MSFT,
GOOGL, META) on their most-recent earnings prints as of 2026-06-24.
The print dates land in late April / mid-May 2026 (the late-April
cycle for AAPL/MSFT/GOOGL/META, mid-May for NVDA on its offset
fiscal calendar).

This example shows the cross-section mode output. Single-event and
aggregate examples live alongside in
`examples/event-study-single-output.md` and
`examples/event-study-aggregate-output.md` (gitignored real-output
files).

## Layer 1: canonical JSON (excerpt)

The full JSON has per-subject `event_window_returns`,
`abnormal_returns`, and `t_stat_vs_history`. The discriminant fields
and cross-section summary are what UIs typically render:

```json
{
  "mode": "cross_section",
  "tier": "A",
  "tier_caveats": [],
  "event_class": "earnings",
  "model": "spy",
  "take": "Surprise explains 55% of T+5 CAR variation (ρ=+0.74). Cross-section average isn't significant at n=5 (avg -1.7%, t-stat -0.42).",
  "subjects": [
    {
      "ticker": "AAPL",
      "event_date": "2026-04-30",
      "event_session": "AMC",
      "event_metadata": {
        "fiscal_period": "Q2",
        "fiscal_year": 2026,
        "surprise_eps_pct": 0.0361,
        "estimated_eps": 1.62,
        "release_time_et": "16:30:00"
      },
      "abnormal_returns": {
        "ar_t1_pct": 0.0300,
        "ar_t3_pct": 0.0345,
        "ar_t5_pct": 0.0411,
        "car_t5_pct": 0.0411
      },
      "t_stat_vs_history": {
        "prior_n": 55,
        "prior_mean_t5_car_pct": 0.0098,
        "prior_std_t5_car_pct": 0.0653,
        "this_event_t5_car_pct": 0.0411,
        "z_score": 0.48,
        "percentile": 0.71,
        "underpowered": false,
        "direction_concurrence": "30/55"
      }
    }
    // ... NVDA, MSFT, GOOGL, META blocks omitted for brevity
  ],
  "summary": {
    "n_subjects": 5,
    "n_tickers": 5,
    "mean_t5_car_pct": -0.0166,
    "median_t5_car_pct": -0.0563,
    "std_t5_car_pct": 0.0876,
    "t_stat_avg_vs_zero": -0.42,
    "significant": false,
    "horizon_breakdown": [
      { "horizon": "T+1", "mean_ar_pct": -0.0089, "t_stat": -0.27, "n": 5 },
      { "horizon": "T+3", "mean_ar_pct": -0.0181, "t_stat": -0.42, "n": 5 },
      { "horizon": "T+5", "mean_ar_pct": -0.0166, "t_stat": -0.42, "n": 5 }
    ],
    "percentiles": {
      "p10_pct": -0.1066,
      "p25_pct": -0.0594,
      "p50_pct": -0.0563,
      "p75_pct": 0.0411,
      "p90_pct": 0.0851
    },
    "surprise_reaction_correlation": {
      "rho": 0.74,
      "n": 5,
      "r_squared": 0.55
    }
  }
}
```

## Layer 2: rendered output (cross_section mode)

```
Event study: AAPL,GOOGL,META,MSFT,NVDA · earnings · most_recent · 5 events

| Ticker | Surprise | T+1 Abn | T+5 CAR | t-stat (vs hist) | Concur |
|--------|--------:|--------:|--------:|-----------------:|-------:|
| AAPL  | +3.6%  | +3.0% |  +4.1% | +0.48 | 30/55 |
| GOOGL | +92.1% | +9.0% | +10.6% | +1.78 | 25/42 |
| META  | +9.6%  | -9.5% | -11.5% | -0.37 | 14/19 |
| MSFT  | +4.9%  | -4.9% |  -5.6% | -1.11 | 31/55 |
| NVDA  | +6.2%  | -2.0% |  -5.9% | -0.82 | 33/55 |

Cross-section
- Avg T+5 CAR:    -1.7%
- Median:         -5.6%
- t-stat (avg vs 0): -0.42 (not significant at n=5)
- Surprise vs reaction ρ: +0.74 (R² = 55%, n=5)

Take: Surprise explains 55% of T+5 CAR variation (ρ=+0.74). Cross-section average isn't significant at n=5 (avg -1.7%, t-stat -0.42).
```

## What the output reveals about mega-cap tech earnings

- **Surprise still predicts reaction.** ρ=+0.74 across the five
  names says the cross-section is internally consistent: bigger beats
  produced larger T+5 CARs. GOOGL's +92% surprise (a Waymo /
  cloud-services accounting one-off in the consensus number) maps to
  the biggest +10.6% CAR; META's +9.6% surprise was hammered to a
  -11.5% CAR because guidance and capex commentary trumped the print.
- **Average is not significant.** At n=5 the cross-section mean of
  -1.7% has a t-stat of -0.42. The dispersion (std 8.8%) dwarfs the
  mean. A reader who wants tradeable signal from "mega-cap tech
  earnings" needs to specify which name or which subset, not the
  group average.
- **AAPL is the only positive print across the five.** Its +4.1%
  CAR sits at the 71st percentile of AAPL's own 55-event history
  (z 0.48). Not exceptional for AAPL, but the only name that didn't
  get sold post-print.

## What a UI builder does with this

Consume the JSON. Render the subjects array as a sortable table.
Make the t-stat column hover-to-show the prior distribution as a
histogram with the current event marked. Render the surprise vs
reaction correlation as a scatter plot. The take goes in a banner.

The rendered text format above is the Claude Code default. The JSON
is the contract.
