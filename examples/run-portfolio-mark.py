#!/usr/bin/env python3
"""
Reference implementation of the portfolio-mark skill.

Marks a CSV of positions to current fair value. Two modes:

  delayed:  one REST snapshot per symbol, walks the fallback chain
            (snapshot.last.price -> lastTrade.p -> min.c -> day.c -> prevDay.c)
            and emits a one-shot report.

  live:     opens one WebSocket to wss://business.polygon.io/stocks,
            subscribes per-symbol with channel fallback
            (FMV -> AM -> T), listens for --listen seconds, then emits
            the most recent mark per symbol plus an optional live-tape
            trailer. Symbols that never ticked during the window get a
            REST snapshot backfill.

Usage:
    python3 examples/run-portfolio-mark.py examples/sample-book.csv
    python3 examples/run-portfolio-mark.py examples/sample-book.csv --mode delayed
    python3 examples/run-portfolio-mark.py examples/sample-book.csv --mode live --listen 30

Reads MASSIVE_API_KEY from env, never from a file.
Writes output to examples/portfolio-mark-output.md (gitignored).
"""
import argparse
import csv
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional

try:
    import websocket  # websocket-client package
except ImportError:
    print("ERROR: websocket-client not installed. Run: pip3 install websocket-client", file=sys.stderr)
    sys.exit(1)

# Make `lib.quant_garage` importable when running this script from any cwd.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.quant_garage import (
    MassiveClient,
    FetchError,
    ET,
    utc_to_et,
    utcnow_iso,
    resolve_price,
    resolve_output_format,
    emit_to_stdout,
)


# ----- Config -----

KEY = os.environ.get("MASSIVE_API_KEY")
if not KEY:
    print("ERROR: MASSIVE_API_KEY not set", file=sys.stderr)
    sys.exit(1)

WS_URL = "wss://business.polygon.io/stocks"

# Channel preference order on a Business key (FMV/AM available, T gated).
# On Stocks Advanced this list can start with T.
PREFERRED_CHANNELS = ["T", "AM", "FMV"]

# Confidence thresholds (see references/confidence-scoring.md)
HIGH_RECENCY_SECONDS = 60
MEDIUM_RECENCY_SECONDS = 5 * 60
HIGH_SPREAD_BPS = 10
MEDIUM_SPREAD_BPS = 50
HIGH_ADV_SHARES = 10_000_000
MEDIUM_ADV_SHARES = 500_000

client = MassiveClient()

# ----- CLI -----

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("csv_path", help="Position CSV with columns: ticker,shares[,cost_basis,as_of_date]")
    ap.add_argument("--mode", choices=["delayed", "live"], default="delayed")
    ap.add_argument("--listen", type=int, default=30, help="Live mode listen window (seconds)")
    ap.add_argument("--output", default=None, help="Output markdown path (default: examples/portfolio-mark-output.md)")
    ap.add_argument("--format", choices=["render", "json", "both"], default=None,
                    help="stdout format. Overrides QUANT_GARAGE_OUTPUT_FORMAT. Default: render.")
    return ap.parse_args()


# ----- CSV loading -----

def load_positions(path: str) -> list:
    positions = []
    with open(path) as f:
        for row in csv.DictReader(f):
            ticker = row["ticker"].strip().upper()
            shares = float(row["shares"])
            cost_basis = float(row["cost_basis"]) if row.get("cost_basis") else None
            positions.append({
                "ticker": ticker,
                "shares": shares,
                "cost_basis": cost_basis,
                "as_of_date": row.get("as_of_date"),
            })
    return positions


# ----- REST helpers -----
#
# Snapshot reads use the lib's resolve_price() for the price-fallback chain
# (D4/D5 audit items). The chain here matches resolve_price() (lastTrade.p →
# min.c → day.c → prevDay.c). The old per-script chain had a redundant
# "snapshot.last.price" step that always returned the same field as
# "snapshot.lastTrade.p" — D5 dismissed.
#
# resolve_price() returns source as bare "lastTrade" etc. We re-map to the
# "snapshot.*" prefixes the rest of this script (and the rendered output)
# expects, so the SHORT_SOURCE table doesn't need to change.

_PRICE_SOURCE_MAP = {
    "lastTrade": "snapshot.lastTrade.p",
    "min.c": "snapshot.min.c",
    "day.c": "snapshot.day.c",
    "prevDay.c": "snapshot.prevDay.c",
    "no_price": None,
}


def snapshot_mark(ticker: str) -> dict:
    """Walk the fallback chain and return mark + source + freshness + quote."""
    try:
        doc, _ = client.get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}")
    except FetchError as e:
        return {"mark_price": None, "mark_source": None, "as_of_utc": None,
                "bid": None, "ask": None, "day_volume": None,
                "error": f"HTTP {e.status_code}" if e.status_code else "fetch_error"}

    t = (doc.get("ticker") or {})
    last_quote = t.get("lastQuote") or {}
    minute = t.get("min") or {}
    day = t.get("day") or {}

    resolution = resolve_price(doc)
    mark_price = resolution.price
    # Zero-price guard: legacy snapshots can return lastTrade.p == 0 when the
    # symbol hasn't traded today. Treat as null and re-walk the chain manually.
    if mark_price == 0:
        # Re-walk: skip the lastTrade step, try min.c → day.c → prevDay.c
        if minute.get("c"):
            mark_price = float(minute["c"])
            mark_source = "snapshot.min.c"
            mark_ts_ns = minute.get("t")
        elif day.get("c"):
            mark_price = float(day["c"])
            mark_source = "snapshot.day.c"
            mark_ts_ns = None
        elif (t.get("prevDay") or {}).get("c"):
            mark_price = float(t["prevDay"]["c"])
            mark_source = "snapshot.prevDay.c"
            mark_ts_ns = None
        else:
            mark_price = None
            mark_source = None
            mark_ts_ns = None
    else:
        mark_source = _PRICE_SOURCE_MAP.get(resolution.source)
        mark_ts_ns = resolution.timestamp_ns

    # Convert timestamp. lastTrade.t and lastQuote.t are ns; min.t is ms;
    # day/prevDay have no per-field timestamp so fall back to ticker.updated.
    as_of_utc = None
    if mark_ts_ns:
        if mark_source == "snapshot.min.c":
            as_of_utc = datetime.fromtimestamp(mark_ts_ns / 1000, tz=timezone.utc)
        else:
            as_of_utc = datetime.fromtimestamp(mark_ts_ns / 1e9, tz=timezone.utc)
    elif t.get("updated"):
        as_of_utc = datetime.fromtimestamp(t["updated"] / 1e9, tz=timezone.utc)

    # Bid / ask. lastQuote.p is bid_price, .P is ask_price (Massive's casing).
    bid = last_quote.get("p")
    ask = last_quote.get("P")
    # Sanity-check: snapshot's lastQuote can be a stale closing-auction
    # quote with extreme size mismatches (ask 40 vs spot 294, etc).
    # Drop the pair if mid is more than 5% off the mark, or if the
    # quote inverts (ask < bid).
    if bid and ask and mark_price:
        mid_quote = (bid + ask) / 2
        bad = (
            mid_quote <= 0
            or ask < bid
            or abs(mid_quote - mark_price) / mark_price > 0.05
        )
        if bad:
            bid, ask = None, None
    else:
        bid, ask = None, None  # null any partial pair

    return {
        "mark_price": mark_price,
        "mark_source": mark_source,
        "as_of_utc": as_of_utc,
        "bid": bid,
        "ask": ask,
        "day_volume": day.get("v"),
        "error": None,
    }


# ----- Confidence -----

def confidence_for(mark_age_sec: Optional[float], spread_bps: Optional[float],
                   day_volume: Optional[float], mark_source: Optional[str]) -> tuple:
    """Returns (confidence, reason_codes)."""
    reasons: list = []

    # Recency bucket
    if mark_age_sec is None:
        recency_bucket = "low"
        reasons.append("stale_mark")
    elif mark_age_sec <= HIGH_RECENCY_SECONDS:
        recency_bucket = "high"
    elif mark_age_sec <= MEDIUM_RECENCY_SECONDS:
        recency_bucket = "medium"
    else:
        recency_bucket = "low"
        reasons.append("stale_mark")

    # Spread bucket
    if spread_bps is None:
        spread_bucket = "medium"  # missing quote drops to medium
        reasons.append("thin_quote")
    elif spread_bps <= HIGH_SPREAD_BPS:
        spread_bucket = "high"
    elif spread_bps <= MEDIUM_SPREAD_BPS:
        spread_bucket = "medium"
    else:
        spread_bucket = "low"
        reasons.append("wide_spread")

    # ADV bucket
    if day_volume is None:
        adv_bucket = "medium"
    elif day_volume >= HIGH_ADV_SHARES:
        adv_bucket = "high"
    elif day_volume >= MEDIUM_ADV_SHARES:
        adv_bucket = "medium"
    else:
        adv_bucket = "low"
        reasons.append("low_adv")

    # Fallback-chain step >= 3 (min/day/prev) is itself a confidence hit
    if mark_source in ("snapshot.min.c", "snapshot.day.c", "snapshot.prevDay.c"):
        reasons.append("fallback_chain_step_3_or_later")
    if mark_source == "snapshot.prevDay.c":
        reasons.append("prev_day_only")

    # Worst of the three buckets
    order = {"high": 0, "medium": 1, "low": 2}
    worst = max([recency_bucket, spread_bucket, adv_bucket], key=lambda b: order[b])
    return worst, reasons


# ----- Detail-text mapping for FLAGGED block -----

def detail_lines(reasons: list, pos: dict, ref_utc: datetime) -> list:
    out = []
    as_of_dt = pos.get("as_of_utc")
    if isinstance(as_of_dt, str):
        try:
            as_of_dt = datetime.fromisoformat(as_of_dt)
        except ValueError:
            as_of_dt = None
    for r in reasons:
        if r == "stale_mark":
            if as_of_dt:
                age = (ref_utc - as_of_dt).total_seconds()
                out.append(f"Last trade {fmt_duration(age)} stale (vs {ref_utc.strftime('%H:%M')} UTC reference time)")
            else:
                out.append("Mark timestamp unavailable")
        elif r == "wide_spread":
            if pos["bid"] and pos["ask"]:
                out.append(f"Bid x Ask: ${pos['bid']:.2f} x ${pos['ask']:.2f} ({pos['spread_bps']:.0f}bps spread)")
            else:
                out.append("Spread above 50bps")
        elif r == "thin_quote":
            out.append("Bid or ask missing in snapshot; quote book likely thin")
        elif r == "low_adv":
            vol = pos.get("day_volume")
            if vol is not None:
                out.append(f"Today's volume {int(vol):,} (well below 500k mid-ADV cutoff)")
            else:
                out.append("Day volume unavailable; ADV tier defaulted to thin")
        elif r == "fallback_chain_step_3_or_later":
            short = {
                "snapshot.min.c": "minute_close",
                "snapshot.day.c": "day_close",
                "snapshot.prevDay.c": "prev_close",
            }.get(pos["mark_source"], pos["mark_source"])
            out.append(f"Mark from {short}; earlier chain steps returned null or zero")
        elif r == "no_ticks_in_window":
            out.append("Subscribed for the listen window but received 0 ticks; mark backfilled from REST")
        elif r == "stream_downgrade":
            out.append("Resubscribed to AM after T returned not_authorized")
        elif r == "prev_day_only":
            out.append("Only prev_close available; symbol may be halted or pre-open")
    return out


def fmt_duration(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"


# ----- Delayed mode -----

def run_delayed(positions: list) -> dict:
    marked_at = datetime.now(timezone.utc)
    print(f"Marking {len(positions)} positions via REST snapshot...", file=sys.stderr)

    out_positions = []
    flagged = []
    sources = [{
        "endpoint": "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
        "fetched_at": utcnow_iso(),
        "context": "one snapshot per unique symbol",
    }]

    for pos in positions:
        snap = snapshot_mark(pos["ticker"])
        mark = snap["mark_price"]
        as_of = snap["as_of_utc"]
        bid, ask = snap["bid"], snap["ask"]
        spread_bps = None
        if bid and ask:
            mid = (bid + ask) / 2
            if mid > 0:
                spread_bps = (ask - bid) / mid * 10000

        age = (marked_at - as_of).total_seconds() if as_of else None
        conf, reasons = confidence_for(age, spread_bps, snap.get("day_volume"), snap["mark_source"])

        pos_record = {
            "ticker": pos["ticker"],
            "shares": pos["shares"],
            "mark_price": mark,
            "mark_source": snap["mark_source"],
            "confidence": conf,
            "as_of_et": fmt_et(as_of) if as_of else "n/a",
            "as_of_utc": as_of.isoformat() if as_of else None,
            "bid": bid,
            "ask": ask,
            "spread_bps": round(spread_bps, 1) if spread_bps is not None else None,
            "cost_basis": pos["cost_basis"],
            "unrealized_pnl_usd": (mark - pos["cost_basis"]) * pos["shares"]
                if (mark is not None and pos["cost_basis"] is not None) else None,
            "market_value_usd": (pos["shares"] * mark) if mark is not None else None,
            "tick_count": None,
            "day_volume": snap.get("day_volume"),
        }
        out_positions.append(pos_record)

        if conf != "high":
            flagged.append({
                "ticker": pos["ticker"],
                "mark_price": mark,
                "confidence": conf,
                "reason_codes": reasons,
                "detail_text": detail_lines(reasons, pos_record, marked_at),
                "source_endpoint": "snapshot",
            })

    book_value = sum(p["market_value_usd"] for p in out_positions if p["market_value_usd"] is not None)
    pnl_total = sum(p["unrealized_pnl_usd"] for p in out_positions if p["unrealized_pnl_usd"] is not None)
    if all(p["unrealized_pnl_usd"] is None for p in out_positions):
        pnl_total = None

    most_recent_as_of = max(
        (datetime.fromisoformat(p["as_of_utc"]) for p in out_positions if p["as_of_utc"]),
        default=None,
    )
    lag_sec = int((marked_at - most_recent_as_of).total_seconds()) if most_recent_as_of else None

    # If every position is stale by more than 4 hours, the market is
    # closed and the per-position staleness flags are uninformative.
    # Surface the situation in caveats once instead.
    caveats = ["Stocks Starter or higher: REST snapshot is 15-min delayed unless on Stocks Advanced."]
    if (lag_sec is not None and lag_sec > 4 * 3600
        and all(p["confidence"] == "low" for p in out_positions)):
        caveats.insert(0, "Run was after-hours: every mark is end-of-session, so every position is flagged low confidence as a class. Compare confidence relative to one another, not against high.")

    return {
        "tier": "B",
        "tier_caveats": caveats,
        "mode": "delayed",
        "marked_at": marked_at.isoformat(),
        "reference_time": marked_at.isoformat(),
        "listen_window_seconds": None,
        "positions": out_positions,
        "flagged": flagged,
        "book_value_usd": round(book_value, 2),
        "unrealized_pnl_usd": round(pnl_total, 2) if pnl_total is not None else None,
        "last_update_lag_seconds": lag_sec,
        "live_tape": None,
        "sources": sources,
    }


# ----- Live mode -----

class LiveRunner:
    """Manages the WebSocket lifecycle for a single listen window.

    Implements:
      - auth on open
      - subscribe with channel-preference fallback (T -> AM -> FMV)
      - per-symbol state with most recent mark + last 5 tape entries
      - reconnect within the listen window
      - graceful close at deadline
    """

    def __init__(self, tickers: list, listen_seconds: int):
        self.tickers = tickers
        self.listen_seconds = listen_seconds
        self.deadline = time.time() + listen_seconds
        self.state: dict = {
            t: {"channel": None, "mark": None, "as_of_utc": None,
                "tick_count": 0, "tape": deque(maxlen=5)}
            for t in tickers
        }
        self.caveats: list = []
        self.channel_used: dict[str, Optional[str]] = {t: None for t in tickers}
        self.reconnect_count = 0
        self.auth_ok = False
        self.subscribe_attempts: dict[str, list] = {t: [] for t in tickers}
        self.subscribe_complete = False
        self._lock = threading.Lock()
        self._queue: deque = deque()
        self._stop = threading.Event()
        self._ws: Optional["websocket.WebSocketApp"] = None

    # ----- handlers -----

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        ws.send(json.dumps({"action": "auth", "params": KEY}))

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        with self._lock:
            self._queue.append(raw)

    def _on_error(self, ws: websocket.WebSocketApp, err) -> None:
        print(f"ws error: {str(err)[:200]}", file=sys.stderr)

    def _on_close(self, ws: websocket.WebSocketApp, code, msg) -> None:
        pass

    # ----- drain worker -----

    def _drain_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                batch = list(self._queue)
                self._queue.clear()
            for raw in batch:
                try:
                    parsed = json.loads(raw)
                except Exception:
                    continue
                for m in parsed:
                    self._handle(m)
            time.sleep(0.05)

    def _handle(self, m: dict) -> None:
        ev = m.get("ev")
        if ev == "status":
            self._handle_status(m)
            return
        if ev in ("T", "AM", "FMV"):
            self._handle_data(m, ev)

    def _handle_status(self, m: dict) -> None:
        status = m.get("status")
        message = m.get("message") or ""
        if status == "auth_success":
            self.auth_ok = True
            self._subscribe_pass(self._ws, channel=PREFERRED_CHANNELS[0])
            return
        if status == "success" and "subscribed to" in message.lower():
            # message format: "subscribed to: T.AAPL"
            param = message.split(":", 1)[-1].strip()
            if "." in param:
                channel, tk = param.split(".", 1)
                if tk in self.state:
                    self.channel_used[tk] = channel
                    self.state[tk]["channel"] = channel
            return
        if status == "error" and "not authorized" in message.lower():
            # Channel-level downgrade. We tried T (or AM); fall back.
            # We don't know from the status which symbol this maps to,
            # so on the first not_authorized after a subscribe pass we
            # roll the entire pass to the next channel.
            self._handle_not_authorized()
            return

    def _handle_not_authorized(self) -> None:
        # Find next preferred channel we haven't tried yet
        tried = set()
        for t, attempts in self.subscribe_attempts.items():
            tried.update(attempts)
        for ch in PREFERRED_CHANNELS:
            if ch not in tried:
                if "stream_downgrade" not in self.caveats:
                    self.caveats.append("stream_downgrade")
                self._subscribe_pass(self._ws, channel=ch)
                return

    def _subscribe_pass(self, ws: Optional["websocket.WebSocketApp"], channel: str) -> None:
        if ws is None:
            return
        for t in self.tickers:
            if channel not in self.subscribe_attempts[t]:
                self.subscribe_attempts[t].append(channel)
        params = ",".join(f"{channel}.{t}" for t in self.tickers)
        ws.send(json.dumps({"action": "subscribe", "params": params}))

    def _handle_data(self, m: dict, ev: str) -> None:
        # T:   {"ev":"T","sym":"AAPL","p":<price>,"s":<size>,"t":<ns>}
        # AM:  {"ev":"AM","sym":"AAPL","c":<close>,"e":<end ms>}
        # FMV: {"ev":"FMV","sym":"AAPL","fmv":<value>,"t":<ns>}
        sym = m.get("sym")
        if not sym or sym not in self.state:
            return
        st = self.state[sym]
        if ev == "T":
            price = m.get("p")
            ts_ns = m.get("t")
            size = m.get("s")
            ts = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc) if ts_ns else datetime.now(timezone.utc)
        elif ev == "AM":
            price = m.get("c")
            ts_ms = m.get("e") or m.get("s")
            size = m.get("v")
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else datetime.now(timezone.utc)
        else:  # FMV
            price = m.get("fmv")
            ts_ns = m.get("t")
            size = None
            ts = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc) if ts_ns else datetime.now(timezone.utc)
        if price is None:
            return
        st["mark"] = float(price)
        st["as_of_utc"] = ts
        st["tick_count"] += 1
        st["tape"].appendleft({
            "ticker": sym,
            "channel": ev,
            "trade_price": float(price),
            "trade_size": int(size) if size else None,
            "trade_time_et": fmt_et(ts),
        })

    # ----- top-level run -----

    def run(self) -> None:
        drain_thread = threading.Thread(target=self._drain_loop, daemon=True)
        drain_thread.start()

        while time.time() < self.deadline and not self._stop.is_set():
            remaining = self.deadline - time.time()
            self._ws = websocket.WebSocketApp(
                WS_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            # run_forever blocks until the socket closes
            start = time.time()
            try:
                # Run in a thread so we can enforce the deadline
                t = threading.Thread(
                    target=self._ws.run_forever,
                    kwargs={"ping_interval": 20},
                    daemon=True,
                )
                t.start()
                t.join(timeout=remaining)
                if t.is_alive():
                    # Deadline reached; close cleanly
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                    t.join(timeout=2)
                    break
            except Exception as e:
                print(f"ws lifecycle error: {e}", file=sys.stderr)

            uptime = time.time() - start
            if time.time() < self.deadline:
                self.reconnect_count += 1
                self.caveats.append(f"socket_reconnect after {uptime:.0f}s")
                time.sleep(1)

        self._stop.set()
        drain_thread.join(timeout=2)


def run_live(positions: list, listen_seconds: int) -> dict:
    marked_at_start = datetime.now(timezone.utc)
    tickers = [p["ticker"] for p in positions]
    print(f"Live mode: opening WebSocket to {WS_URL}", file=sys.stderr)
    print(f"  subscribing to {len(tickers)} symbols, listening {listen_seconds}s...", file=sys.stderr)

    runner = LiveRunner(tickers, listen_seconds)
    runner.run()
    marked_at = datetime.now(timezone.utc)

    print(f"  socket closed. auth_ok={runner.auth_ok}, reconnects={runner.reconnect_count}", file=sys.stderr)
    total_ticks = sum(s["tick_count"] for s in runner.state.values())
    print(f"  total ticks across window: {total_ticks}", file=sys.stderr)

    caveats = []
    if runner.auth_ok:
        caveats.append("WebSocket auth + subscribe completed end-to-end on wss://business.polygon.io/stocks.")
    if "stream_downgrade" in runner.caveats:
        caveats.append("Channel T returned not_authorized on this key; resubscribed to AM. See live-vs-delayed.md for the entitlement matrix.")
    if total_ticks == 0:
        caveats.append("Zero stream events received during the listen window (market may be closed or symbols inactive). Marks backfilled from REST snapshot.")
    for c in runner.caveats:
        if c.startswith("socket_reconnect"):
            caveats.append(c)

    # Backfill any symbol that received no ticks
    out_positions = []
    flagged = []
    live_tape: list = []
    sources = [{"endpoint": WS_URL, "fetched_at": utcnow_iso(),
                "context": f"WebSocket stream, listen window {listen_seconds}s"}]
    snapshot_used = False

    for pos in positions:
        sym = pos["ticker"]
        s = runner.state[sym]
        snap = None
        mark = s["mark"]
        as_of = s["as_of_utc"]
        mark_source = None
        bid = ask = None
        day_volume = None
        reason_extra: list = []

        channel_used = runner.channel_used.get(sym) or s["channel"]

        if mark is None:
            snap = snapshot_mark(sym)
            snapshot_used = True
            mark = snap["mark_price"]
            mark_source = snap["mark_source"]
            as_of = snap["as_of_utc"]
            bid, ask = snap["bid"], snap["ask"]
            day_volume = snap.get("day_volume")
            if s["tick_count"] == 0:
                reason_extra.append("no_ticks_in_window")
        else:
            mark_source = f"stream.{channel_used}" if channel_used else "stream.T"
            # Pull bid/ask + day volume from a cheap REST snapshot for confidence
            snap = snapshot_mark(sym)
            bid, ask = snap["bid"], snap["ask"]
            day_volume = snap.get("day_volume")

        spread_bps = None
        if bid and ask:
            mid = (bid + ask) / 2
            if mid > 0:
                spread_bps = (ask - bid) / mid * 10000

        age = (marked_at - as_of).total_seconds() if as_of else None
        conf, reasons = confidence_for(age, spread_bps, day_volume, mark_source)
        if "stream_downgrade" in runner.caveats and channel_used != "T":
            reasons.append("stream_downgrade")
            if conf == "high":
                conf = "medium"
        reasons.extend(reason_extra)

        pos_record = {
            "ticker": sym,
            "shares": pos["shares"],
            "mark_price": mark,
            "mark_source": mark_source,
            "confidence": conf,
            "as_of_et": fmt_et(as_of) if as_of else "n/a",
            "as_of_utc": as_of.isoformat() if as_of else None,
            "bid": bid,
            "ask": ask,
            "spread_bps": round(spread_bps, 1) if spread_bps is not None else None,
            "cost_basis": pos["cost_basis"],
            "unrealized_pnl_usd": (mark - pos["cost_basis"]) * pos["shares"]
                if (mark is not None and pos["cost_basis"] is not None) else None,
            "market_value_usd": (pos["shares"] * mark) if mark is not None else None,
            "tick_count": s["tick_count"],
            "day_volume": day_volume,
        }
        out_positions.append(pos_record)

        if conf != "high":
            flagged.append({
                "ticker": sym,
                "mark_price": mark,
                "confidence": conf,
                "reason_codes": list(dict.fromkeys(reasons)),  # dedupe, keep order
                "detail_text": detail_lines(list(dict.fromkeys(reasons)), pos_record, marked_at),
                "source_endpoint": mark_source or "snapshot",
            })

        # Surface tape entries
        live_tape.extend(list(s["tape"]))

    if snapshot_used:
        sources.append({
            "endpoint": "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
            "fetched_at": utcnow_iso(),
            "context": "REST backfill for symbols that received no ticks during the listen window",
        })

    book_value = sum(p["market_value_usd"] for p in out_positions if p["market_value_usd"] is not None)
    pnl_total = sum(p["unrealized_pnl_usd"] for p in out_positions if p["unrealized_pnl_usd"] is not None)
    if all(p["unrealized_pnl_usd"] is None for p in out_positions):
        pnl_total = None

    most_recent_as_of = max(
        (datetime.fromisoformat(p["as_of_utc"]) for p in out_positions if p["as_of_utc"]),
        default=None,
    )
    lag_sec = int((marked_at - most_recent_as_of).total_seconds()) if most_recent_as_of else None

    return {
        "tier": "A",
        "tier_caveats": caveats,
        "mode": "live",
        "marked_at": marked_at.isoformat(),
        "reference_time": marked_at.isoformat(),
        "listen_window_seconds": listen_seconds,
        "positions": out_positions,
        "flagged": flagged,
        "book_value_usd": round(book_value, 2),
        "unrealized_pnl_usd": round(pnl_total, 2) if pnl_total is not None else None,
        "last_update_lag_seconds": lag_sec,
        "live_tape": live_tape if live_tape else None,
        "sources": sources,
    }


# ----- Rendering -----

SHORT_SOURCE = {
    "stream.T": "live_trade",
    "stream.AM": "live_minute",
    "stream.FMV": "live_fmv",
    "snapshot.last.price": "last_trade",
    "snapshot.lastTrade.p": "last_trade",
    "snapshot.min.c": "minute_close",
    "snapshot.day.c": "day_close",
    "snapshot.prevDay.c": "prev_close",
}


def fmt_et(dt: Optional[datetime]) -> str:
    if dt is None:
        return "n/a"
    local = utc_to_et(dt)
    return local.strftime("%H:%M:%S")


def fmt_shares(n: float) -> str:
    if n < 0:
        return f"({int(abs(n)):,})"
    return f"{int(n):,}" if n == int(n) else f"{n:,.2f}"


def fmt_lag(seconds: Optional[int]) -> str:
    if seconds is None:
        return "n/a"
    return fmt_duration(seconds)


def render(payload: dict) -> str:
    lines: list = []
    marked_at = datetime.fromisoformat(payload["marked_at"])
    n_pos = len(payload["positions"])
    tier = payload["tier"]
    mode = payload["mode"]

    lines.append(f"Book marked: {marked_at.strftime('%Y-%m-%d %H:%M:%S')} UTC · {n_pos} positions · Tier: {tier} ({mode})")

    if payload["tier_caveats"]:
        lines.append(f"Note: {payload['tier_caveats'][0]}")
    lines.append("")
    lines.append("Marked")

    has_cost = any(p["cost_basis"] is not None for p in payload["positions"])
    if has_cost:
        header = "| Ticker | Shares | Mark      | Source       | Confidence | As-of (ET)  | Cost      | Unrealized P&L  |"
        divider = "|--------|--------|-----------|--------------|------------|-------------|-----------|-----------------|"
    else:
        header = "| Ticker | Shares | Mark      | Source       | Confidence | As-of (ET)  |"
        divider = "|--------|--------|-----------|--------------|------------|-------------|"
    lines.append(header)
    lines.append(divider)

    for p in payload["positions"]:
        mark_str = f"${p['mark_price']:.2f}" if p["mark_price"] is not None else "n/a"
        source_str = SHORT_SOURCE.get(p["mark_source"], p["mark_source"] or "n/a")
        row = (
            f"| {p['ticker']:<6} "
            f"| {fmt_shares(p['shares']):>6} "
            f"| {mark_str:>9} "
            f"| {source_str:<12} "
            f"| {p['confidence']:<10} "
            f"| {p['as_of_et']:<11} "
        )
        if has_cost:
            cost_str = f"${p['cost_basis']:.2f}" if p["cost_basis"] is not None else "n/a"
            pnl = p["unrealized_pnl_usd"]
            if pnl is None:
                pnl_str = "n/a"
            else:
                sign = "+" if pnl >= 0 else "-"
                pnl_str = f"{sign}${abs(pnl):,.2f}"
            row += f"| {cost_str:>9} | {pnl_str:>15} |"
        else:
            row += "|"
        lines.append(row)

    lines.append("")
    book_val = payload["book_value_usd"]
    sub = f"Book value: ${book_val:,.2f}"
    if payload.get("unrealized_pnl_usd") is not None:
        pnl = payload["unrealized_pnl_usd"]
        sign = "+" if pnl >= 0 else "-"
        sub += f" · Unrealized P&L: {sign}${abs(pnl):,.2f}"
    sub += f" · Last update lag: {fmt_lag(payload['last_update_lag_seconds'])}"
    lines.append(sub)

    if payload["flagged"]:
        lines.append("")
        lines.append(f"FLAGGED ({len(payload['flagged'])})")
        for i, f in enumerate(payload["flagged"]):
            if i > 0:
                lines.append("")
            mark = f"${f['mark_price']:.2f}" if f["mark_price"] is not None else "n/a"
            lines.append(f"{f['ticker']} · {mark} · {f['confidence']} confidence")
            for d in f["detail_text"]:
                lines.append(f"  - {d}")
            verified = next((p["as_of_et"] for p in payload["positions"] if p["ticker"] == f["ticker"]), "n/a")
            short_src = f["source_endpoint"]
            if short_src.startswith("wss://"):
                short_src = "stream"
            elif short_src.startswith("stream."):
                short_src = short_src
            else:
                short_src = "snapshot"
            lines.append(f"  - Source: {short_src} · Verified: {verified}")

    if payload.get("live_tape"):
        lines.append("")
        lines.append(f"Live tape (last 5 per ticker that received ticks)")
        for entry in payload["live_tape"]:
            size_str = f" x {entry['trade_size']:,}" if entry.get("trade_size") else ""
            lines.append(f"{entry['ticker']:<5} {entry['trade_time_et']}  ${entry['trade_price']:.2f}{size_str}")

    if payload["tier_caveats"] and len(payload["tier_caveats"]) > 1:
        lines.append("")
        lines.append("Caveats:")
        for c in payload["tier_caveats"][1:]:
            lines.append(f"  - {c}")

    return "\n".join(lines)


# ----- Main -----

def main() -> None:
    args = parse_args()
    fmt = resolve_output_format(args.format)
    positions = load_positions(args.csv_path)

    if args.mode == "delayed":
        payload = run_delayed(positions)
    else:
        payload = run_live(positions, args.listen)

    rendered = render(payload)

    out_path = args.output or os.path.join(os.path.dirname(__file__), "portfolio-mark-output.md")
    with open(out_path, "w") as f:
        f.write("# portfolio-mark run\n\n")
        f.write(f"Generated: {payload['marked_at']}\n")
        f.write(f"Mode: {payload['mode']} (Tier {payload['tier']})\n")
        f.write(f"Input: {os.path.basename(args.csv_path)} ({len(positions)} positions)\n\n")
        f.write("## Layer 1: canonical JSON\n\n")
        f.write("```json\n")
        f.write(json.dumps(payload, indent=2, default=str))
        f.write("\n```\n\n")
        f.write("## Layer 2: rendered hybrid output\n\n")
        f.write("```\n")
        f.write(rendered)
        f.write("\n```\n")

    print(f"\nDONE. Output written to {out_path}", file=sys.stderr)
    emit_to_stdout(rendered, payload, fmt)


if __name__ == "__main__":
    main()
