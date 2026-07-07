# earnings-week-prep

Sunday-night prep for the week's earnings prints. Runs earnings-
blackout across the watchlist, then earnings-drilldown + technical-
briefing per imminent print (top-N by proximity).

## Quick start

```bash
python3 examples/run-earnings-week-prep.py --watchlist "NVDA,ALLO,SOFI,QCOM" --format render
```

## Plan requirement

Stocks Starter (earnings-drilldown needs the financials endpoint).
See top-level [PLAN-MATRIX.md](../../PLAN-MATRIX.md).
