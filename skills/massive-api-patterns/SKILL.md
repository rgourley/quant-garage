---
name: massive-api-patterns
description: Foundation skill for any REST workflow hitting api.massive.com. Use when calling any /v1, /v2, /v3 endpoint. Covers auth header, rate limit handling, pagination, and the best-price fallback chain. Every other REST-using skill depends on this.
---

# massive-api-patterns

The REST foundation. Every skill that calls `api.massive.com` reads these
rules first.

## Auth

API key goes in the `Authorization` header as a bearer token:

```
Authorization: Bearer ${MASSIVE_API_KEY}
```

Query-string auth (`?apiKey=...`) also works but logs the key in URLs.
Use the header.

## Rate limits

- **Free Basic:** 5 requests per minute. Hard cap. 429 if you exceed it.
- **Any paid tier:** unlimited requests per minute. Soft cap at 100
  requests per second to avoid throttling.

The skills in this repo declare their rate assumptions in `requires.yml`.
A skill that fans out parallel calls will warn the user if it detects a
free key.

Always handle 429 explicitly: back off with exponential delay, retry
once, then surface the error.

## Pagination

List endpoints return a `next_url` field when more results exist:

```json
{
  "results": [...],
  "next_url": "https://api.massive.com/v3/reference/tickers?cursor=..."
}
```

Follow `next_url` directly, don't reconstruct it. The cursor is opaque
and includes auth context.

## The best-price fallback chain

Never quote a price from a single field. The v2 snapshot response nests
everything under `ticker`, so all reads start there. Walk this 4-step
chain and stop at the first non-null value:

1. `snapshot.ticker.lastTrade.p` (most recent trade across exchanges)
2. `snapshot.ticker.min.c` (current minute bar close, intraday only)
3. `snapshot.ticker.day.c` (today's session close)
4. `snapshot.ticker.prevDay.c` (previous session close, off-hours or
   quiet names)

Always emit the timestamp of whichever field won so the caller knows how
stale the price is. `lastTrade.t` and `min.t` are nanosecond epochs; the
daily fields don't always carry a timestamp.

`lib/quant_garage/snapshot.py::resolve_price` is the canonical
implementation. Every skill in this repo that needs a current price
imports it rather than rewriting the walk.

This is the generic REST pattern. Massive's proprietary **FMV** metric
is a different thing (Business plan, stream-only on the WebSocket FMV
channel) and not what these skills mean by "fair value." It is not a
field on the v2 snapshot response.

## Error codes

| Code | Meaning | What to do |
|---|---|---|
| 401 | Bad or missing API key | Surface immediately, don't retry |
| 403 | Endpoint not available on user's plan | Surface with plan upgrade hint |
| 404 | Ticker or contract not found | Skip, don't retry |
| 429 | Rate limited | Back off, retry once |
| 5xx | Server error | Retry with exponential backoff, max 3 attempts |

## Real-time vs delayed

Stocks REST is 15-minute delayed on Basic, Starter, and Developer tiers.
Real-time stocks need Advanced. Options REST is real-time on Developer
and above. Crypto REST is real-time on Developer.

If a skill needs real-time, declare `realtime: yes` in `requires.yml` and
fail fast if the user's tier doesn't support it.

## Example: snapshot a ticker

```bash
curl -sS --max-time 15 \
  -H "Authorization: Bearer ${MASSIVE_API_KEY}" \
  "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers/AAPL"
```

The response includes the full snapshot. Walk the fallback chain to pick
the price field, then emit `{ticker, price, source_field, timestamp}`.

## What does NOT live here

WebSocket streaming patterns (see [`massive-websockets`](../massive-websockets))
and bulk flat-file downloads (see [`massive-flat-files`](../massive-flat-files)).
