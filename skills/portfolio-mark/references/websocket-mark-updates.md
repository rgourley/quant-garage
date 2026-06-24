## WebSocket mark updates

How live mode handles incoming stream events. The pattern below is
the implementation contract for the live socket. The
[`massive-websockets`](../../massive-websockets) foundation covers
the connection-level concerns (auth, reconnect, backpressure); this
reference covers the mark-keeping state machine on top of it.

## Per-symbol state

For every symbol in the book, maintain:

```python
state[ticker] = {
    "channel": "T" | "AM" | "FMV",       # which channel produced the latest
    "mark": float,                        # most recent price
    "as_of_utc": datetime,                # most recent update timestamp
    "tick_count": int,                    # events seen during the window
    "tape": deque(maxlen=5),              # most recent 5 events for the trailer
}
```

The state is initialised when the subscribe ack arrives, not when the
listen window starts, so the very first tick on a symbol updates an
already-existing entry.

## Message handling per channel

Massive sends JSON arrays of events. Always iterate; multiple events
per frame is normal.

| Channel | `ev` code | Mark field | Time field (ns since epoch) |
|---|---|---|---|
| Trades | `T` | `p` (price) | `t` (sip_timestamp) |
| Per-minute aggregates | `AM` | `c` (close of minute) | `e` (end ts, ms since epoch) |
| Fair Market Value | `FMV` | `fmv` (value) | `t` (event ts, ns since epoch) |

The skill normalizes all timestamps to UTC at ingest and never
propagates raw ns/ms past the channel handler.

## The drain loop

Move work off the receive thread. The receive callback enqueues raw
frames; a worker thread drains, parses, and updates state. This
prevents TCP backpressure during market-open bursts (50k+ msg/s on
a wide subscription).

```python
queue = collections.deque()
queue_lock = threading.Lock()

def on_message(ws, raw):
    with queue_lock:
        queue.append(raw)

def drain():
    while not done.is_set():
        with queue_lock:
            batch = list(queue)
            queue.clear()
        for raw in batch:
            handle(raw)
        time.sleep(0.05)
```

If the queue grows past a soft cap (e.g. 5000 messages), batch-apply
updates (keep only the last event per ticker in the batch) and warn
in the caveats. The book-level mark only needs the most recent
value per symbol.

## Reconnect strategy

The foundation skill handles socket-level reconnects. The mark layer's
contract on top of it:

1. On socket close inside the listen window, log the disconnect
   timestamp and the per-symbol gap (now - as_of_utc per ticker).
2. Reconnect, re-auth, re-subscribe to the full position list.
3. Resume the drain loop. The state dict persists across reconnects;
   only the connection is reset.
4. Append a `socket_reconnect` entry to the caveats with the gap
   duration so the operator sees the run wasn't smooth.

```python
def run_with_reconnect(positions, listen_seconds):
    deadline = time.time() + listen_seconds
    while time.time() < deadline:
        try:
            run_one_socket(positions, deadline - time.time())
        except SocketDisconnect as e:
            caveats.append(f"socket_reconnect after {e.uptime:.0f}s")
            time.sleep(1 + random.random())
```

State on Massive's side is per-connection. After reconnect you start
fresh and the resubscribe replays the entire symbol list.

## Subscribe-time channel fallback

The skill's preferred channel order on a Business key is
`FMV → AM → T`. On Advanced + the real-time agreement,
`T → AM → FMV`. The actual order is data-driven: send the first-
preference subscribe, watch for status messages, and on
`status: error, message: "not authorized"` for the channel,
resubscribe with the next preference.

```python
def subscribe_with_fallback(ws, ticker, preferences):
    for channel in preferences:
        ws.send(json.dumps({"action": "subscribe", "params": f"{channel}.{ticker}"}))
        ack = wait_for_status(ticker, channel, timeout=2)
        if ack == "subscribed":
            return channel
        if ack == "not_authorized":
            continue
    return None  # no channel worked; rely on snapshot backfill
```

For book-wide subscribes (`AM.AAPL,AM.SPY,...` as one params string),
batch by channel and watch the status messages to detect partial
auth failures. The skill defaults to one batch subscribe per channel
to keep the wire chatty but the logic compact.

## End-of-window finalization

When `listen_seconds` elapse:

1. Close the socket cleanly.
2. For any symbol where `tick_count == 0`, run a one-shot REST
   snapshot per [`snapshot-fallback-chain.md`](./snapshot-fallback-chain.md)
   and merge into state with `mark_source: snapshot.*`. Add
   `no_ticks_in_window` to that position's flagged reason codes.
3. Compute confidence per
   [`confidence-scoring.md`](./confidence-scoring.md). The reference
   time is the moment the listen window ended.
4. Render and emit.

## A note on FMV

FMV is a synthetic mid-price Massive emits on every tick. When it's
available (Business plan) it's the highest-quality mark of the three
channels for the purposes of this skill: tick-rate updates without
the directional bias a raw trade print can carry. Prefer it when
present.
