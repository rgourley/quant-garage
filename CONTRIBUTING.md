# Contributing

Pull requests welcome. This guide covers the shape of a new skill, the
audit gates, and the methodology bar.

## Adding a new skill

Each skill lives in its own directory under `skills/`. The required files:

```
skills/<skill-name>/
├── SKILL.md             # When to invoke, what it does, both output layers
├── requires.yml         # Plan tier, interface, output mode, foundations
├── output-schema.json   # Canonical JSON Schema (Layer 1)
└── references/
    ├── rendering.md             # Layer 2 format rules for this skill's mode
    └── <analytical-recipe>.md   # The IP: statistical methods, base rates
```

Plus, in `examples/`:
```
examples/
└── <ticker-or-scenario>.md    # Paired JSON + rendered note
```

## What the audit checks

`npm run audit:requires` validates every skill's metadata. It catches:

- Missing `SKILL.md`, `requires.yml`, `output-schema.json`, or `references/rendering.md`
- Invalid YAML or JSON
- Wrong `kind` (must be `foundation` or `skill`)
- Wrong `interface` (must be `rest`, `flat-files`, or `websocket`)
- Wrong `output_mode` (must be `note`, `stream`, `table`, `exception-report`,
  `list`, `dataset`, or `hybrid`)
- Invalid product or tier in the `requires` array
- Fallback skill references that don't exist (warning, not error)

Run it before opening a PR. CI runs the same check.

## The dual-layer pattern

Every skill ships two outputs from one analysis:

1. **Layer 1: canonical JSON.** Defined by `output-schema.json` (JSON
   Schema draft-07). This is what custom UIs, downstream agents, and
   Python scripts consume. Every field has a description; every
   number includes its source endpoint and timestamp.

2. **Layer 2: rendered output.** Defined by `references/rendering.md`.
   Format follows the skill's `output_mode`:
   - `note` mode: sell-side morning note style. Bold take + grouped
     sections. Used by `earnings-drilldown`.
   - `stream` mode: per-event blocks, Cheddar Flow style. Used by
     options-flow, news-scanner.
   - `table` mode: tabular comparison, Bloomberg-screener style.
     Used by universe-builder, factor-research, pitch-comps.
   - `exception-report` mode: only flagged items. Used by
     reconcilers and slippage-cost.
   - `list`, `dataset`, `hybrid` are also valid; see the existing
     skills for examples.

A user reading the rendered output should never see the JSON. A
developer building a UI never has to parse the rendered text. Both
get the same compute and the same citations.

## The methodology bar

Skills targeting finance professionals must produce senior-analyst
output, not junior data dumps. Specifically:

- **Statistical context:** percentile, z-score, rolling window
  comparison. Not just "AAPL is +2%" but "AAPL +2% on 3.2x 30-day
  avg volume, 95th percentile of trailing year."
- **Base rates:** "Last 6 of 8 prints with this setup resolved X."
- **Sample sizes everywhere:** never quote an average without `n=` and
  never claim significance without a t-stat.
- **Microstructure where relevant:** bid-ask spreads, dark prints,
  GEX, dealer positioning. Not for every skill, but for execution
  and options skills, yes.
- **Citations:** every emitted number includes its endpoint and
  fetched_at timestamp in the JSON. Rendered output usually omits
  these for readability, but UIs can surface them.

When in doubt, ask: would a senior buy-side analyst respect this
output, or would they roll their eyes? If the latter, the skill
isn't ready.

## Output format follows what users already consume

Don't invent a new format. Find the tool the workflow's users
already read (Bloomberg EE, Cheddar Flow, sell-side morning note,
Stripe-style dashboard, etc.) and match its conventions. The four
output modes in this repo are derived from those real tools.

No em-dashes in any output (it's a standard AI-prose tell).
Use colons, parentheses, periods.

## Testing a skill

Three layers of test, in order of importance:

1. **Real data run.** Get a Massive API key, run the skill against
   a real ticker. Compare the output to what a sell-side analyst
   would produce for the same question. If the output looks like
   "shallow LLM finance demo," iterate the methodology references
   until it doesn't.

2. **Baseline subagent test.** Dispatch a Claude with no skill
   loaded and ask it the same question. Capture what it produces.
   The skill should be obviously better; if it's not, the skill's
   value-add isn't clear and probably needs sharpening.

3. **Audit script.** `npm run audit:requires` validates the
   metadata shape.

## Style and tone

The README's voice carries through every skill's docs and rendered
output:

- Direct and specific. No filler words.
- No em-dashes.
- Real names and real numbers in examples.
- Active voice.
- No sycophancy in skill output.
- Take + supporting evidence, not "here are some facts you may
  find useful."

## Commit messages

Conventional commits: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`.
Short imperative subject line. Body explains the why if non-obvious.

## What NOT to commit

- API keys (the `.gitignore` blocks `.env*`)
- Real run outputs that contain a key fingerprint (the `.gitignore`
  blocks `examples/*-real-output*` and `examples/*-tier-b-output*`)
- `node_modules`, `__pycache__`, `.venv`

If you accidentally commit a secret, rotate immediately on Massive's
dashboard and force-push the cleaned history. Don't try to recover the
key.

## Questions

Open an issue. Or fork it, build the skill, and PR. Most discussions
will happen on PR threads.
