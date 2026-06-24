---
name: massive-websockets
description: Foundation skill for live streaming workflows backed by Massive WebSockets. Use whenever you need sub-second updates from stocks, options, crypto, or FX feeds. Requires a real-time tier (Stocks Advanced, Options Developer, or Crypto Developer).
---

# massive-websockets

The live streaming foundation. Any skill that needs to react to market
events as they happen reads this first.

## Why this exists

REST snapshots are 15 minutes stale on most stocks plans, and even on
real-time tiers you pay a request-per-event overhead that breaks down
above a few hundred symbols. WebSockets push every update to you as it
hits the exchange. One connection, thousands of symbols, no polling.

## Endpoints

| Asset | URL | Requires |
|---|---|---|
| US stocks (real-time) | `wss://socket.polygon.io/stocks` | Stocks Advanced + signed real-time addendum |
| US stocks (delayed) | `wss://delayed.polygon.io/stocks` | Stocks Starter or higher |
| US stocks (Business) | `wss://business.polygon.io/stocks` | Stocks Business (FMV/AM channels) |
| US options | `wss://socket.polygon.io/options` | Options Developer |
| Crypto | `wss://socket.polygon.io/crypto` | Crypto Developer |
| FX | `wss://socket.polygon.io/forex` | Forex Starter |
| Business cluster | `wss://business.polygon.io/{asset}` | Business plan |

The `socket.polygon.io` host is the legacy real-time domain and still
active when the signed real-time data agreement is in place.

**The undocumented `wss://delayed.polygon.io/{asset}` endpoint** is what
the real-time host suggests in its error message when your key doesn't
have the real-time entitlement: "you can connect to the delayed
websocket at wss://delayed.polygon.io/stocks." It's the fallback for
non-Advanced plans and isn't in the published channel matrix.

## Entitlement and the `not_authorized` case

(Verified 2026-06-23 on a Stocks Business + Options Business +
Currencies Business + Benzinga add-ons key.)

WebSocket entitlements diverge from the published per-channel matrix
in two ways. Plan around both:

1. **Real-time stocks channels (T, Q, A) require a signed real-time
   data agreement** even on Stocks Business. Without the addendum the
   auth call succeeds but every `subscribe` to `T.{ticker}`,
   `Q.{ticker}`, or `A.{ticker}` returns
   `{ev: "status", status: "error", message: "not authorized"}`. The
   `socket.polygon.io/stocks` host returns a different error first:
   "You don't have access real-time data. If you're already subscribed
   to a plan that includes real-time data, you may need to visit the
   dashboard to sign your agreements." The fix is operator-side (sign
   the agreement in the Massive dashboard), not code-side.

2. **The Business cluster (`wss://business.polygon.io/stocks`)
   delivers `FMV.{ticker}` and `AM.{ticker}` to a Stocks Business
   key immediately**, no agreement needed. T/Q/A still return
   `not_authorized` on the same connection. The result: a Business
   key has a usable live stream (minute aggregates and FMV mids) for
   any skill that doesn't require sub-second prints.

**Pattern:** subscribe with channel-preference fallback. Send the
first-preference subscribe, watch for `status: error,
message: "not authorized"`, and on that message resubscribe to the
next preference. The `portfolio-mark` skill uses this pattern with
the order `T → AM → FMV` (or `FMV → AM → T` on a Business key).

```python
def subscribe_with_fallback(ws, tickers, preferences):
    for channel in preferences:
        params = ",".join(f"{channel}.{t}" for t in tickers)
        ws.send(json.dumps({"action": "subscribe", "params": params}))
        ack = wait_for_status(channel, timeout=2)
        if ack == "subscribed":
            return channel
        if ack == "not_authorized":
            continue
    return None
```

Status errors don't include the channel or symbol they apply to.
Track them at the per-subscribe-batch level, not per-symbol.

## Auth flow

Connect, then send an auth message, then subscribe.

```javascript
const ws = new WebSocket("wss://socket.polygon.io/stocks");

ws.on("open", () => {
  ws.send(JSON.stringify({ action: "auth", params: MASSIVE_API_KEY }));
});

ws.on("message", (data) => {
  const messages = JSON.parse(data);
  for (const msg of messages) {
    if (msg.ev === "status" && msg.status === "auth_success") {
      ws.send(JSON.stringify({ action: "subscribe", params: "T.AAPL,T.MSFT" }));
    } else if (msg.ev === "T") {
      handleTrade(msg);
    }
  }
});
```

Messages arrive as JSON arrays (multiple events per frame). Always
iterate.

## Channels

| Code | What | Asset classes |
|---|---|---|
| `T` | Trades (tick) | stocks, options, crypto |
| `Q` | NBBO quote updates | stocks |
| `A` | Per-second aggregates | stocks, options, crypto |
| `AM` | Per-minute aggregates | stocks, options, crypto |
| `XL2` | Level 2 book | crypto only |
| `FMV` | Fair Market Value stream | Business plan only |

Subscribe by channel.symbol: `T.AAPL`, `Q.AAPL`, `AM.*` for all minute
aggregates. You can subscribe to thousands of symbols on one connection.

**Subscribe ack format:** `{"ev": "status", "status": "success",
"message": "subscribed to: T.AAPL"}`. The message string carries the
exact `channel.symbol` you subscribed to, parseable as
`message.split(":")[-1].strip()`. Use this to track which
channel.symbol pairs successfully subscribed when the subscribe
request listed many symbols.

**Data message shapes (most common):**

```json
{"ev":"T",   "sym":"AAPL", "p": 298.74, "s": 200,  "t": <ns>,   "x": 11}
{"ev":"Q",   "sym":"AAPL", "bp": 298.70, "bs": 5,  "ap": 298.78, "as": 3, "t": <ns>}
{"ev":"A",   "sym":"AAPL", "o": 298.6, "c": 298.74, "h": 298.8, "l": 298.5, "v": 12400, "s": <ms_start>, "e": <ms_end>}
{"ev":"AM",  "sym":"AAPL", "o": 298.6, "c": 298.74, "v": 1240000, "s": <ms_start>, "e": <ms_end>}
{"ev":"FMV", "sym":"AAPL", "fmv": 298.72, "t": <ns>}
```

T-channel timestamps are nanoseconds since epoch; A/AM use
milliseconds (note `s` and `e` for start/end of bar); FMV uses
nanoseconds. Convert at the channel-handler boundary and never
propagate raw values.

## FMV stream

Massive's proprietary FMV (Fair Market Value) stream is a Business-tier
metric that emits a synthetic mid-price for every symbol on every tick.
Useful if you have the plan. The skills in this repo do not depend on
FMV: they compute a simpler fallback chain from trades and quotes.

## Reconnect strategy

WebSocket connections drop. Plan for it.

```javascript
function connect() {
  const ws = new WebSocket(url);
  ws.on("close", () => setTimeout(connect, 1000 + Math.random() * 2000));
  ws.on("error", (err) => console.error("ws error", err));
  // ... auth and subscribe
}
```

After a reconnect, resubscribe everything. State on the Massive side is
per-connection, not per-key.

## Listening when no data is flowing

Live runs outside market hours (overnight, weekend, before pre-market)
still let you validate the plumbing end-to-end: the socket connects,
auth_success arrives, and the subscribe status confirms each
ticker.symbol pair. But `T.{ticker}` and `AM.{ticker}` emit nothing
until the market reopens. `FMV.{ticker}` is also silent off-hours
despite docs implying continuous emission.

This is why the `portfolio-mark` skill's live mode falls back to a
REST snapshot for any symbol that received zero ticks during the
listen window. The connection-level test (auth + subscribe) passes;
the data-level test (ticks arriving) doesn't. Both are useful
signals. Surface them separately in caveats:

```
- WebSocket auth + subscribe completed end-to-end (plumbing OK)
- Zero ticks received during 30s listen window (market closed)
```

The first line confirms the foundation works; the second confirms why
the operator isn't seeing live marks.

## Message buffering

Bursts at market open can push 50k+ messages per second on a wide
subscription. The TCP buffer fills up and the connection drops.

```javascript
const queue = [];
let processing = false;

ws.on("message", (data) => {
  queue.push(data);
  if (!processing) drainQueue();
});

async function drainQueue() {
  processing = true;
  while (queue.length) {
    const data = queue.shift();
    await handle(data);
  }
  processing = false;
}
```

Move handling off the receive thread. If you can't keep up, narrow the
subscription instead of dropping messages silently.

## What lives here

- `references/channels.md`: full channel reference per asset class
- `references/reconnect.md`: full reconnect + resubscribe pattern
- `references/buffering.md`: backpressure patterns for wide subscriptions

## What does NOT live here

REST patterns ([`massive-api-patterns`](../massive-api-patterns)) and
bulk historical pulls ([`massive-flat-files`](../massive-flat-files)).
