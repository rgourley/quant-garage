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
| US stocks | `wss://socket.polygon.io/stocks` | Stocks Advanced |
| US options | `wss://socket.polygon.io/options` | Options Developer |
| Crypto | `wss://socket.polygon.io/crypto` | Crypto Developer |
| FX | `wss://socket.polygon.io/forex` | Forex Starter |
| Business cluster | `wss://business.polygon.io/{asset}` | Business plan |

The `socket.polygon.io` host is the legacy domain and still active.

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
