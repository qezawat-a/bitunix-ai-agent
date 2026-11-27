"""WebSocket client for every public and private Bitunix futures channel.

- login exactly like the official demo (seconds timestamp, double SHA-256)
- app-level ping ({"op":"ping","ping":<sec>}), protocol ping disabled like the official demo
- outgoing messages throttled under the 5 msg/sec limit (changelog 2026-06-15)
- max 300 channels per connection: public subscriptions are sharded automatically
- kline interval switch unsubscribes the previous interval first (as the kline doc requires)
- auto reconnect with backoff, re-login and re-subscribe
- live state store (tickers, mark/index/funding, klines, order books, trades, balance, positions, orders, tpsl)
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable

import websockets

from .constants import (TPSL_EVENT_STATUS_MEANING, WS_KLINE_CHANNELS, WS_MAX_CHANNELS_PER_CONNECTION,
                        WS_MAX_MESSAGES_PER_SEC, WS_PRIVATE_URL, WS_PUBLIC_CHANNELS, WS_PUBLIC_URL, ws_kline_channel)
from .signing import ws_login_args

log = logging.getLogger("bitunix.ws")
Handler = Callable[[dict], Awaitable[None] | None]


def parse_ws_time(v: Any) -> int | None:
    """WS ctime/mtime are ISO-8601 nanosecond strings; REST uses ms ints. Returns ms."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        n = int(v)
        return n // 1_000_000 if n > 10**15 else (n * 1000 if n < 10**11 else n)
    s = str(v).replace("Z", "+00:00")
    if "." in s:  # trim nanoseconds to microseconds for fromisoformat
        head, frac = s.split(".", 1)
        tz = ""
        for sep in ("+", "-"):
            if sep in frac:
                frac, tz = frac.split(sep, 1)
                tz = sep + tz
                break
        s = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except ValueError:
        return None


class _Throttle:
    def __init__(self, per_sec: int):
        self.min_gap = 1.0 / max(per_sec - 1, 1)  # stay one under the limit
        self.last = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            gap = self.min_gap - (time.monotonic() - self.last)
            if gap > 0:
                await asyncio.sleep(gap)
            self.last = time.monotonic()


class _Conn:
    """One socket (public shard or private)."""

    def __init__(self, url: str, name: str, owner: "BitunixWS", private: bool = False):
        self.url, self.name, self.owner, self.private = url, name, owner, private
        self.ws = None
        self.subs: dict[tuple[str, str], dict] = {}
        self.throttle = _Throttle(WS_MAX_MESSAGES_PER_SEC)
        self.connected = asyncio.Event()
        self.logged_in = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stop = False
        self.last_msg = 0.0

    async def send(self, msg: dict):
        await self.connected.wait()
        await self.throttle.wait()
        await self.ws.send(json.dumps(msg, separators=(",", ":")))

    def start(self):
        if not self._task:
            self._task = asyncio.create_task(self._run(), name=f"ws-{self.name}")

    async def stop(self):
        self._stop = True
        if self.ws:
            await self.ws.close()
        if self._task:
            self._task.cancel()

    async def _run(self):
        backoff = 1
        while not self._stop:
            try:
                kw = {"ping_interval": None, "close_timeout": 5, "open_timeout": 15, "max_size": 2**24}
                if self.owner.proxy is not None:
                    kw["proxy"] = self.owner.proxy
                async with websockets.connect(self.url, **kw) as ws:
                    self.ws = ws
                    self.connected.set()
                    backoff = 1
                    log.info("ws %s connected", self.name)
                    pinger = asyncio.create_task(self._pinger())
                    try:
                        if self.private:
                            await self._login()
                        if self.subs:
                            await self._send_subs("subscribe", list(self.subs.values()))
                        async for raw in ws:
                            self.last_msg = time.time()
                            await self.owner._dispatch(raw, self)
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                break
            except Exception as e:  # reconnect on anything
                log.warning("ws %s error: %r", self.name, e)
            self.connected.clear()
            self.logged_in.clear()
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _pinger(self):
        while True:
            await asyncio.sleep(self.owner.ping_interval)
            try:
                await self.send({"op": "ping", "ping": int(time.time())})
            except Exception:
                return

    async def _login(self):
        await self.throttle.wait()
        args = ws_login_args(self.owner.api_key, self.owner.secret_key)
        await self.ws.send(json.dumps({"op": "login", "args": [args]}, separators=(",", ":")))
        # official demo subscribes after a short delay; we wait for the login ack or 2 s
        try:
            await asyncio.wait_for(self.logged_in.wait(), 2)
        except asyncio.TimeoutError:
            pass

    async def _send_subs(self, op: str, args: list[dict]):
        # several args fit in one message; chunk to keep messages small
        for i in range(0, len(args), 50):
            chunk = args[i:i + 50]
            if op == "unsubscribe":
                # the unsubscribe example in the docs uses "channel" while the table uses "ch": send both
                chunk = [{**a, "channel": a["ch"]} for a in chunk]
            await self.send({"op": op, "args": chunk})


class BitunixWS:
    def __init__(self, api_key: str = "", secret_key: str = "", *, public_url: str = WS_PUBLIC_URL,
                 private_url: str = WS_PRIVATE_URL, ping_interval: float = 15.0, proxy: Any = None,
                 kline_history: int = 500, trade_history: int = 500):
        self.api_key, self.secret_key = api_key, secret_key
        self.public_url, self.private_url = public_url, private_url
        self.ping_interval = ping_interval
        self.proxy = proxy
        self._public: list[_Conn] = []
        self._private: _Conn | None = None
        self._handlers: dict[str, list[Handler]] = collections.defaultdict(list)
        # ----- live state
        self.tickers: dict[str, dict] = {}       # tickers + ticker channels
        self.prices: dict[str, dict] = {}        # price channel: mp/ip/fr/ft/nft
        self.klines: dict[tuple[str, str], dict] = {}
        self.books: dict[tuple[str, str], dict] = {}
        self.trades: dict[str, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=trade_history))
        self.balance: dict[str, dict] = {}
        self.positions: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.tpsl: dict[str, dict] = {}
        self.events: asyncio.Queue = asyncio.Queue(maxsize=10_000)
        self._kline_hist = kline_history

    # ------------------------------------------------------------------ handlers
    def on(self, channel: str, handler: Handler):
        """channel: 'ticker', 'tickers', 'price', 'trade', 'kline', 'depth', 'balance', 'position', 'order', 'tpsl', '*'"""
        self._handlers[channel].append(handler)

    # ------------------------------------------------------------------ public
    def _shard_for_new(self) -> _Conn:
        for c in self._public:
            if len(c.subs) < WS_MAX_CHANNELS_PER_CONNECTION:
                return c
        c = _Conn(self.public_url, f"public-{len(self._public)}", self)
        self._public.append(c)
        c.start()
        return c

    async def subscribe(self, symbol: str, ch: str):
        if ch not in WS_PUBLIC_CHANNELS and ch not in WS_KLINE_CHANNELS:
            raise ValueError(f"unknown public channel {ch!r}")
        key = (symbol.upper(), ch)
        for c in self._public:
            if key in c.subs:
                return
        # kline: unsubscribe other intervals for this symbol first (official requirement)
        if ch in WS_KLINE_CHANNELS:
            for c in self._public:
                for (s, other) in list(c.subs):
                    if s == key[0] and other in WS_KLINE_CHANNELS and other.split("_kline_")[0] == ch.split("_kline_")[0]:
                        await self.unsubscribe(s, other)
        c = self._shard_for_new()
        arg = {"symbol": key[0], "ch": ch}
        c.subs[key] = arg
        if c.connected.is_set():
            await c._send_subs("subscribe", [arg])

    async def subscribe_kline(self, symbol: str, interval: str, price_type: str = "market"):
        await self.subscribe(symbol, ws_kline_channel(interval, price_type))

    async def unsubscribe(self, symbol: str, ch: str):
        key = (symbol.upper(), ch)
        for c in self._public:
            if key in c.subs:
                arg = c.subs.pop(key)
                if c.connected.is_set():
                    await c._send_subs("unsubscribe", [arg])

    def public_subscriptions(self) -> list[dict]:
        return [a for c in self._public for a in c.subs.values()]

    # ------------------------------------------------------------------ private
    async def start_private(self, channels: tuple[str, ...] = ("balance", "position", "order")):
        if not (self.api_key and self.secret_key):
            raise RuntimeError("private websocket needs api key + secret")
        if self._private:
            return
        self._private = _Conn(self.private_url, "private", self, private=True)
        for ch in channels:  # tpsl is pushed automatically after login
            self._private.subs[("", ch)] = {"ch": ch}
        self._private.start()

    async def close(self):
        for c in self._public + ([self._private] if self._private else []):
            await c.stop()

    def status(self) -> dict:
        return {
            "public_connections": [{"name": c.name, "connected": c.connected.is_set(), "channels": len(c.subs),
                                    "last_msg_age_s": round(time.time() - c.last_msg, 1) if c.last_msg else None} for c in self._public],
            "private": None if not self._private else {"connected": self._private.connected.is_set(),
                                                       "logged_in": self._private.logged_in.is_set(),
                                                       "last_msg_age_s": round(time.time() - self._private.last_msg, 1) if self._private.last_msg else None},
        }

    # ------------------------------------------------------------------ dispatch
    async def _dispatch(self, raw: str | bytes, conn: _Conn):
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        op = msg.get("op")
        if op == "ping":
            return
        if op == "login" or (conn.private and msg.get("event") == "login") or (op and "login" in str(op)):
            conn.logged_in.set()
            log.info("ws private login response: %s", str(msg)[:200])
            return
        ch = msg.get("ch")
        if not ch:
            if conn.private and not conn.logged_in.is_set() and msg.get("code") in (0, "0"):
                conn.logged_in.set()
            log.debug("ws non-channel msg: %s", str(msg)[:300])
            return
        if conn.private:
            conn.logged_in.set()
        kind = self._apply(msg)
        evt = {"kind": kind, **msg}
        if kind in ("order", "position", "tpsl", "balance"):
            try:
                self.events.put_nowait(evt)
            except asyncio.QueueFull:
                pass
        for h in self._handlers.get(kind, []) + self._handlers.get("*", []):
            try:
                r = h(evt)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("ws handler error")

    def _apply(self, msg: dict) -> str:
        ch, sym, data = msg["ch"], msg.get("symbol", ""), msg.get("data")
        ts = msg.get("ts")
        if ch == "tickers":
            for t in data or []:
                self.tickers[t["s"]] = {**self.tickers.get(t["s"], {}), **t, "ts": ts}
            return "tickers"
        if ch == "ticker":
            s = (data or {}).get("s") or sym
            self.tickers[s] = {**self.tickers.get(s, {}), **(data or {}), "ts": ts}
            return "ticker"
        if ch == "price":
            self.prices[sym] = {**(data or {}), "ts": ts}
            return "price"
        if ch == "trade":
            for t in data or []:
                self.trades[sym].append(t)
            return "trade"
        if ch in WS_KLINE_CHANNELS:
            # live (forming) candle, pushed every 500 ms. Closed history comes from REST get_kline.
            self.klines[(sym, ch)] = {**(data or {}), "ts": ts}
            return "kline"
        if ch.startswith("depth_"):
            key = (sym, ch)
            if ch == "depth_books" and key in self.books:
                book = self.books[key]
                for side, field in (("a", "asks"), ("b", "bids")):
                    for p, q in (data or {}).get(side, []):
                        if Decimal(str(q)) == 0:
                            book[field].pop(str(p), None)
                        else:
                            book[field][str(p)] = str(q)
                book["ts"] = ts
            else:
                self.books[key] = {"asks": {str(p): str(q) for p, q in (data or {}).get("a", [])},
                                   "bids": {str(p): str(q) for p, q in (data or {}).get("b", [])}, "ts": ts}
            return "depth"
        if ch == "balance":
            coin = (data or {}).get("coin", "USDT")
            self.balance[coin] = {**(data or {}), "ts": ts}
            return "balance"
        if ch == "position":
            pid = (data or {}).get("positionId")
            if pid:
                if data.get("event") == "CLOSE":
                    self.positions.pop(pid, None)
                else:
                    self.positions[pid] = {**self.positions.get(pid, {}), **data, "ts": ts}
            return "position"
        if ch == "order":
            oid = (data or {}).get("orderId")
            if oid:
                d = {**data, "ctime_ms": parse_ws_time(data.get("ctime")), "mtime_ms": parse_ws_time(data.get("mtime")), "ts": ts}
                if data.get("event") == "CLOSE" or data.get("orderStatus") in ("FILLED", "CANCELED", "PART_FILLED_CANCELED"):
                    self.orders.pop(oid, None)
                else:
                    self.orders[oid] = {**self.orders.get(oid, {}), **d}
            return "order"
        if ch == "tpsl":
            oid = (data or {}).get("orderId")
            if oid:
                meaning = TPSL_EVENT_STATUS_MEANING.get((data.get("event"), data.get("status")), "")
                d = {**data, "meaning": meaning, "ctime_ms": parse_ws_time(data.get("ctime")), "ts": ts}
                if data.get("event") == "CLOSE":
                    self.tpsl.pop(oid, None)
                else:
                    self.tpsl[oid] = d
                msg["data"] = d
            return "tpsl"
        return ch

    # ------------------------------------------------------------------ views
    def best_bid_ask(self, symbol: str) -> tuple[str | None, str | None]:
        t = self.tickers.get(symbol, {})
        if t.get("bd"):
            return t.get("bd"), t.get("ak")
        for ch in ("depth_book1", "depth_book5", "depth_book15", "depth_books"):
            b = self.books.get((symbol, ch))
            if b and b["bids"] and b["asks"]:
                return max(b["bids"], key=Decimal), min(b["asks"], key=Decimal)
        return None, None

    def book_snapshot(self, symbol: str, depth: int = 10) -> dict | None:
        for ch in ("depth_books", "depth_book15", "depth_book5", "depth_book1"):
            b = self.books.get((symbol, ch))
            if b:
                bids = sorted(b["bids"].items(), key=lambda x: Decimal(x[0]), reverse=True)[:depth]
                asks = sorted(b["asks"].items(), key=lambda x: Decimal(x[0]))[:depth]
                return {"channel": ch, "bids": bids, "asks": asks, "ts": b.get("ts")}
        return None
