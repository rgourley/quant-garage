# Audit status

Active scorecard for the 2026-06-26 deep audit. Closed items live in
[`AUDIT-LOG.md`](./AUDIT-LOG.md); the original deep review is preserved
verbatim in `AUDIT-ORIGINAL.md`.

**Tally (as of 2026-06-26):** 42 closed, 2 foundation-addressed, 6
open. All 12 original Critical items closed.

**Legend:**

- `[ ]` Open. Not yet addressed in code.
- `[~]` Foundation-addressed. The fix exists in `lib/quant_garage/` but
  hasn't propagated to every script that needs it.

---

## Open items

### High

| ID | Affects | Resolution path |
|---|---|---|
| H9 | best-ex-check | Rename to "slippage cost" or compute true arrival-price IS |

### Medium

| ID | Affects | Resolution path |
|---|---|---|
| M9 | options-flow, crypto-vol-scanner, news-scanner | Add percentile/base-rate context on composite scores |
| M10 | earnings-drilldown, event-study (single mode) | Universe base rate for single-name skills |

### Doc / code drift

| ID | Affects | Resolution path |
|---|---|---|
| D4 `[~]` | massive-api-patterns | Foundation doc still claims wrong snapshot paths; lib + scripts are correct |
| D6 `[~]` | earnings-drilldown, pitch-comps, options-flow, portfolio-mark, corp-actions | Several scripts still claim documented behavior they don't deliver |

### Findings from the migration (still open)

| ID | Severity | Notes |
|---|---|---|
| N2 | Medium | `run-aapl-tier-b.py` uses `lastQuote.p` for spot, bypassing `lastTrade`. `resolve_price()` doesn't cover lastQuote. Decide whether to extend the chain or keep the inline read |

---

## See also

- [`AUDIT-LOG.md`](./AUDIT-LOG.md) — closure history with commit refs and methodology notes per item
- `AUDIT-ORIGINAL.md` — verbatim deep review from 2026-06-26
