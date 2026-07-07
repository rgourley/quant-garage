# preflight-trade rendering

Note mode. Header + verdict block + flags + 4 sub-skill renders.

## Header

```
Preflight: {TICKER} · Action: {ACTION_UPPERCASE}
As of {AS_OF}
```

## Verdict block

```
VERDICT: GO | WAIT | REVIEW — {SHORT_EXPLANATION}
```

Explanations:
- GO: `no material red flags`
- WAIT: `multiple red flags, consider deferring`
- REVIEW: `mixed signals, human judgment required`

## Flags

```
Red flags:
  - {FLAG}
  ...

Green flags:
  + {FLAG}
  ...
```

Skip either block if empty.

## Sections

1. `TECHNICAL` — technical_briefing.render()
2. `EARNINGS BLACKOUT` — earnings_blackout.render()
3. `CORPORATE ACTIONS (90d)` — corporate_actions_scanner.render()
4. `RECENT NEWS` — news_scanner.render()

## Errors

Standard block.
