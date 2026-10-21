# CLAUDE.md — quant-garage

Written for Claude Code (or any AI agent) opening this repo. Read this
before editing. CONTRIBUTING.md has the human-facing detail; this file
tells you what to preserve and where the sharp edges are.

## What this is

A collection of quant/equity research tools that run behind Claude
skills or as pip-installable Python functions. 26 skills, one Massive
API key, one methodology. Each skill answers a specific analyst
question with the supporting numbers cited to a live API call.

The point isn't the tools individually — it's that they share data,
methodology, and audit trail, so they chain into workflows.

## Repo layout

- `quant_garage/` — the installable package. Client, helpers, and
  one module per skill in `quant_garage/skills/`. Skills expose
  `run(...) -> dict` and `render(payload) -> str`.
- `skills/<name>/` — the Claude Code skill definition per tool.
  Must contain SKILL.md, README.md, requires.yml, output-schema.json,
  and references/rendering.md. Optional methodology docs in
  references/.
- `examples/run-<name>.py` — thin CLI wrappers over each skill's
  `run()`. Arg-parse, format, forward. No compute lives here.
- `examples/*.md` — verified example outputs (paired JSON + rendered).
- `assets/` — README OG images. HTML source + rendered PNGs.
  `scripts/render-assets.sh` regenerates the PNGs via headless Chrome.
- `PROPOSED-TOOLS.md` — the design backlog. Nothing here is built.
- `PLAN-MATRIX.md` — which Massive tier each skill needs.
- `REVIEW-FINDINGS.md` — the audit log of live-verified issues.

## Non-negotiable invariants

1. **Dual-layer output.** Every skill returns a canonical JSON dict
   (Layer 1) AND a rendered string (Layer 2). One compute, two
   surfaces. Never emit only rendered text; downstream agents and
   UIs need the JSON.

2. **Skill dir + Python module + CLI wrapper must all exist.**
   Adding a new skill means all three or the audit fails and Claude
   Code can't discover it. See the "Adding a skill" section below.

3. **Every skill exposes `run(...)` and `render(payload)`.** The
   composed skills (stock-one-pager, historical-analog-finder as
   consumer, etc.) import `run()` directly from other skill modules
   rather than shelling out. Do not break this by moving compute
   into the CLI wrapper.

4. **No fabricated numbers.** Every emitted figure has a source
   endpoint and a fetched_at timestamp somewhere in the payload.
   If the data isn't there, emit `null` and a caveat, not a guess.

5. **Zero is not a valid price.** `quant_garage.snapshot.resolve_price`
   rejects zero at every step of the fallback chain. Massive's v2
   snapshot returns zeros for intraday sections outside market hours;
   the chain walks past them to prevDay.c. Do not "fix" this by
   accepting zero — that regresses the 2026-07-04 fix.

6. **No em-dashes in any output text.** Standard AI-prose tell.
   Use colons, parentheses, periods. This applies to rendered
   output, take/read lines, and docs. If you find an em-dash in
   generated text, replace it.

7. **The audit gate is `npm run audit:requires`.** It validates
   every skill's requires.yml against a strict allowlist. Run it
   before committing scaffolding changes. Enums to know:
   - kind: `foundation` or `skill`
   - interface: `rest`, `flat-files`, `websocket`
   - output_mode: `note`, `stream`, `table`, `exception-report`,
     `list`, `dataset`, `hybrid`
   - product: `stocks`, `options`, `crypto`, `forex`, `indices`,
     `futures`, `benzinga_news`, `benzinga_earnings`,
     `benzinga_analyst_ratings`, `benzinga_sentiment`, `any`
   - tier: `basic`, `starter`, `developer`, `advanced`, `business`,
     `addon`

## Common tasks

### Adding a new skill

1. Write `quant_garage/skills/<snake_name>.py` exposing `run(...)`
   and `render(payload)`. Import shared helpers from the top-level
   `quant_garage` package (client, timezones, stats, snapshot).
2. Register it in `quant_garage/skills/__init__.py`.
3. Add the CLI wrapper at `examples/run-<kebab-name>.py`.
4. Create the skill dir at `skills/<kebab-name>/` with:
   - `SKILL.md` — Claude Code frontmatter (name + description)
   - `README.md` — human intro + quick-start
   - `requires.yml` — audit-gated metadata
   - `output-schema.json` — JSON Schema for the payload
   - `references/rendering.md` — Layer 2 format rules
5. Update `PLAN-MATRIX.md` with the tier requirement.
6. Update `README.md` if it belongs in the marketed feature set.
7. Run `npm run audit:requires` and fix any issues.

### Fixing a bug in a shared helper

The client, `resolve_price`, timezones, stats, and technicals are
consumed by every skill. Before touching any of them:

- Grep for callers (`grep -rn 'resolve_price' quant_garage/skills/`)
- Trace what the fix breaks. Some skills (earnings-drilldown,
  crypto-vol-scanner) intentionally bypass shared helpers with a
  comment explaining why. Don't "fix" those to use the helper.
- Verify with a live smoke run on one affected skill before commit.

### Regenerating OG images

The README shows two OG images (og.png, skills.png) and closing.png
lives at the bottom. Their sources are `assets/*.html`. Edit HTML,
then run:

```bash
./scripts/render-assets.sh
```

Chrome headless renders each at 1200x630. The skill count is
hardcoded in the HTML (`26 tools.`, `26 SKILLS`). Bump both when the
count changes, plus the "twenty-six" copy in README.md.

### Committing

Conventional commits: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`.
Direct-to-main is fine for this repo (solo, no CI gate on branches).
Rob's global preference is normally "review before commit," but for
quant-garage the workflow has stabilized to direct-to-main because
it's a solo repo with no other contributors. Confirm before pushing
if unclear.

Do NOT include a Claude co-author trailer. Rob has a standing memory
on this — no "Generated with Claude Code" or Co-Authored-By: Claude
in commit messages.

## Massive API conventions

- Auth: `Authorization: Bearer $MASSIVE_API_KEY` header only. Never
  `?apiKey=` on the URL.
- Host: `api.polygon.io` (the underlying host answers to both; using
  polygon.io keeps `next_url` pagination self-consistent).
- Retry: the shared `MassiveClient` handles socket.timeout, 5xx, and
  429 with exponential backoff. Don't re-implement.
- Fetched-at timestamps: every `client.get()` returns
  `(body, fetched_at)`. Record them in a `sources` array on the
  payload so citations survive to Layer 2.

## Voice and style in generated output

The skills emit prose (take lines, read lines, caveat blocks). Match
Rob's voice from README.md and existing skill output:

- Direct, specific, skeptical.
- Real numbers with sample sizes: never "high correlation," always
  "rho = 0.83 (n = 24)."
- Base rates and percentiles wherever a claim invites "vs what?"
- No filler transitions ("Notably," "Interestingly," "It's worth
  noting").
- No hedging ("arguably," "potentially," "one could argue").
- Take + evidence, not "here are some facts you may find useful."

Rob's global writing rules apply here too. If in doubt, check the
existing skills' render layers as reference.

## What NOT to do

- Add wholesale indicator libraries (`pandas-ta`, `TA-Lib`,
  `OpenBB`). quant-garage sits above signals, not next to them.
- Move compute into CLI wrappers. Wrappers are arg-parse only.
- Accept zero as a valid price in `resolve_price`. See invariant #5.
- Skip the skill-dir scaffolding when adding a Python module. The
  audit will fail and Claude Code can't discover it.
- Add em-dashes to any text a user will see.
- Fabricate a number when the data isn't there. Emit null + caveat.

## Recent context

- **2026-07-04**: added CLAUDE.md (this file). Scaffolded skill
  dirs for the 6 new tools. Fixed the resolve_price zero bug.
- **2026-07-03**: shipped 6 new skills for portfolio decision-
  support and macro context (Part 3 of PROPOSED-TOOLS). README + OG
  assets updated to reflect 26 tools.
- **2026-07-02**: tightened valuation-sanity peer resolution + MC
  robustness (Q1-Q6 findings in REVIEW-FINDINGS.md).

For older context, `git log --oneline -30` covers the arc.
