# Rendering: prediction-market-monitor

Note-mode. Layout:

1. Header (1 line)
2. Per-event block:
   - Event ticker + close time + strike count
   - Full title (wrapped/truncated at 110 chars)
   - Modal outcome line + expected value (when laddered)
   - Bucket distribution table with ASCII bars (when laddered)
   - Otherwise: per-market rows with P and volume
3. Take + caveats

Bucket bars use `█` (block character) scaled to 40 columns at 100%
probability. `n_bars = int(round(p * 40))`.
