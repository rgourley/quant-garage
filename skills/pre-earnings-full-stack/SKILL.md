---
name: pre-earnings-full-stack
description: Workflow composite for a single ticker heading into an earnings print. Chains earnings-blackout (timing check) + event-study (prior print reaction distribution) + guidance-tracker (management raise/cut track record) + analyst-tracker (sell-side positioning) + mc-portfolio-simulator (P&L distribution at proposed weight over the horizon). Emits a posture verdict (constructive_setup / mixed_setup / avoid_or_hedge / no_imminent_print). Requires Stocks Basic; guidance-tracker and analyst-tracker sections skip gracefully without Benzinga entitlements.
---

# pre-earnings-full-stack

Full pre-earnings prep on a single ticker. Chains five sub-skills and
emits an integrated read plus a size-sensitive posture.

## When to invoke

- Pre-print decision: "should I trade this print?"
- Position sizing given an outlook
- Reading the reaction distribution before committing conviction
- The user says "full pre-earnings", "earnings prep", "should I
  trade the print"

## What you need

- A ticker (`--ticker`)
- `MASSIVE_API_KEY` exported
- Stocks Basic minimum
- Optional: Benzinga Corporate Guidance and Analyst Ratings add-ons

Optional:

- `--proposed-weight` (default 0.10)
- `--n-prior-quarters` (default 8)
- `--horizon-days` (default 10)
- `--n-paths` (default 10000)

## What you get back

**Layer 1: JSON** with all five sub-skill outputs nested + a
`posture` block containing verdict + signals + warnings +
reaction_take.

**Layer 2: rendered note**. Posture header + signals / warnings +
per-sub-skill summary blocks + Take.

## How it works

1. earnings-blackout: check timing.
2. event-study (aggregate mode, last ~2 years): reaction distribution
   for prior prints.
3. guidance-tracker: management's own trajectory (Benzinga add-on).
4. analyst-tracker: sell-side positioning (Benzinga add-on).
5. mc-portfolio-simulator: forward P&L at proposed weight.

Verdict logic:
- `constructive_setup`: >=2 signals, 0 warnings, print imminent
- `avoid_or_hedge`: >=2 warnings, <=1 signal, print imminent
- `mixed_setup`: everything else with print imminent
- `no_imminent_print`: no upcoming print detected
