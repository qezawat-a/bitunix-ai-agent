"""Background loops: price/funding/PnL alerts, trailing stops, scheduled agent tasks, live account event pushes."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from decimal import Decimal
from typing import Awaitable, Callable

from ..exchange.symbols import D
from ..storage.db import DB
from .trading import TradingEngine
from . import autoscan
from .autoscan import ScanConfig, env, env_bool, env_float

log = logging.getLogger("bitunix.monitor")
Notify = Callable[[str, str], Awaitable[None]]   # (chat_id, text)


class Monitor:
    def __init__(self, engine: TradingEngine, db: DB, notify: Notify, run_agent: Callable[[str, str], Awaitable[str]] | None = None,
                 poll_sec: float = 5.0):
        self.engine, self.db, self.notify, self.run_agent = engine, db, notify, run_agent
        self.poll = poll_sec
        self._tasks: list[asyncio.Task] = []
        self._last_task_reply: dict[str, str] = {}
        self.margin_rate_alert = Decimal("0.8")  # notify when marginRate crosses this (closer to 1 = closer to liquidation)
        self._warned: set[str] = set()
        self._guard_done: dict[str, set[str]] = {}   # positionId -> {"be", "trail"}
        self.last_scan: dict[str, dict] = {}            # symbol -> last evaluate() result
        self._scan_cool: dict[str, float] = {}

    def start(self):
        self._tasks = [asyncio.create_task(self._alerts_loop()), asyncio.create_task(self._trailing_loop()),
                       asyncio.create_task(self._tasks_loop()), asyncio.create_task(self._guard_loop()),
                       asyncio.create_task(self._scan_loop()), asyncio.create_task(self._report_loop())]
        if self.engine.ws and self.engine.ws._private:
            self._tasks.append(asyncio.create_task(self._events_loop()))

    async def stop(self):
        for t in self._tasks:
            t.cancel()

    async def _broadcast(self, text: str, chat_id: str | None = None):
        targets = [chat_id] if chat_id else [c["chat_id"] for c in self.db.chats()]
        for cid in targets:
            try:
                await self.notify(cid, text)
            except Exception:
                log.exception("notify failed")

    async def _price(self, symbol: str) -> Decimal:
        return await self.engine.last_price(symbol)

    # ---------------------------------------------------------------- alerts
    async def _alerts_loop(self):
        while True:
            try:
                for a in self.db.alerts():
                    v = D(a["value"])
                    if a["kind"] == "price":
                        cur = await self._price(a["symbol"])
                    elif a["kind"] == "mark":
                        cur = await self.engine.mark_price(a["symbol"])
                    elif a["kind"] == "funding":
                        cur = D((await self.engine.rest.get_funding_rate(symbol=a["symbol"]))["fundingRate"])
                    elif a["kind"] == "pnl":
                        ps = await self.engine.positions(a["symbol"])
                        cur = sum((D(p.get("unrealizedPNL", 0)) for p in ps), Decimal(0))
                    else:
                        continue
                    hit = (a["op"] == ">=" and cur >= v) or (a["op"] == "<=" and cur <= v)
                    if hit:
                        self.db.fire_alert(a["id"], bool(a["repeat"]))
                        await self._broadcast(f"🔔 Alert {a['id']}: {a['symbol']} {a['kind']} {cur} {a['op']} {v}. {a['note'] or ''}".strip(), a["chat_id"])
            except Exception:
                log.exception("alerts loop")
            await asyncio.sleep(self.poll)

    # ---------------------------------------------------------------- trailing stops
    async def _trailing_loop(self):
        while True:
            try:
                for t in self.db.trailing():
                    pos = await self.engine.find_position(t["symbol"], position_id=t["position_id"])
                    if not pos:
                        self.db.stop_trailing(t["position_id"])
                        continue
                    price = await self.engine.mark_price(t["symbol"])
                    long = str(t["side"]).upper() in ("LONG", "BUY")
                    if t["activation_price"] and ((long and price < D(t["activation_price"])) or (not long and price > D(t["activation_price"]))):
                        continue
                    best = D(t["best_price"]) if t["best_price"] else price
                    improved = (long and price > best) or (not long and price < best) or t["best_price"] is None
                    if improved:
                        best = price
                        self.db.update_trailing_best(t["position_id"], float(best))
                        cb = D(t["callback_pct"]) / 100
                        sl = best * (1 - cb) if long else best * (1 + cb)
                        # A trailing stop only ever TIGHTENS. Never move an existing stop (e.g. breakeven) backwards.
                        try:
                            cur = await self.engine.current_stop(t["symbol"], t["position_id"])
                        except Exception:
                            log.warning("trailing: could not read current SL for %s, skipping this tick", t["position_id"])
                            continue
                        if cur is not None and ((long and sl <= cur) or (not long and sl >= cur)):
                            continue
                        res = await self.engine.set_tpsl(symbol=t["symbol"], position_id=t["position_id"], stop_loss=sl)
                        if res.get("ok"):
                            log.info("trailing SL %s -> %s", t["position_id"], sl)
            except Exception:
                log.exception("trailing loop")
            await asyncio.sleep(max(self.poll, 3))

    # ---------------------------------------------------------------- auto guard (no LLM)
    async def _guard_loop(self):
        """Code-side GUARD for every open position, without the LLM (so it keeps working when the LLM is rate-limited):
        ROI >= AUTO_GUARD_BE_ROI -> stop to breakeven (never loosens an existing stop);
        ROI >= AUTO_GUARD_TRAIL_ROI -> trailing stop with AUTO_GUARD_TRAIL_CALLBACK %.
        ROI = unrealizedPNL / margin x 100 (what the Bitunix app shows). Settings are read live, so `set AUTO_GUARD=false` turns it off."""
        while True:
            try:
                if _env_bool("AUTO_GUARD", True):
                    await self._guard_tick()
            except Exception:
                log.exception("guard loop")
            await asyncio.sleep(max(3.0, _env_float("AUTO_GUARD_POLL_SEC", 5.0)))

    async def _guard_tick(self):
        be_roi = _env_float("AUTO_GUARD_BE_ROI", 15.0)
        trail_roi = _env_float("AUTO_GUARD_TRAIL_ROI", 20.0)
        cb = _env_float("AUTO_GUARD_TRAIL_CALLBACK", 1.0)
        positions = await self.engine.positions()
        open_ids = {str(p.get("positionId")) for p in positions}
        for pid in list(self._guard_done):
            if pid not in open_ids:
                self._guard_done.pop(pid, None)
        trailing_ids = {str(t["position_id"]) for t in self.db.trailing()}
        for p in positions:
            pid, sym = str(p.get("positionId")), p.get("symbol")
            roi = position_roi(p)
            if roi is None or not sym:
                continue
            done = self._guard_done.setdefault(pid, set())
            if roi >= be_roi and "be" not in done:
                res = await self.engine.move_stop_to_breakeven(sym, position_id=pid)
                if res.get("ok"):
                    done.add("be")
                    if not res.get("unchanged"):
                        await self._broadcast(f"🛡 {sym}: ROI {roi:.1f}% → stop moved to breakeven.")
            if roi >= trail_roi and "trail" not in done:
                if pid not in trailing_ids:
                    from .trading import pos_direction
                    chat = next((c["chat_id"] for c in self.db.chats()), "")
                    self.db.set_trailing(pid, chat, sym, pos_direction(p), cb, None)
                    await self._broadcast(f"📈 {sym}: ROI {roi:.1f}% → trailing stop on ({cb}% callback).")
                done.add("trail")

    # ---------------------------------------------------------------- auto scan (no LLM)
    def _target(self) -> str | None:
        return env("NOTIFY_CHAT_ID", "") or None

    async def _scan_loop(self):
        while True:
            try:
                if env_bool("AUTO_SCAN", False):
                    await self.scan_once(trade=True)
            except Exception:
                log.exception("scan loop")
            await asyncio.sleep(max(5.0, env_float("SCAN_EVERY_SEC", 15)))

    async def scan_once(self, trade: bool = False) -> dict:
        cfg = ScanConfig()
        positions = await self.engine.positions()
        for sym in cfg.symbols:
            ivs = list(dict.fromkeys(cfg.timeframes + [x for x in (cfg.atr_tf, cfg.trend_tf) if x]))
            res = await asyncio.gather(*(self.engine.technical_analysis(sym, iv) for iv in ivs), return_exceptions=True)
            data = {iv: (r if not isinstance(r, Exception) else {"error": str(r)}) for iv, r in zip(ivs, res)}
            trend = data.get(cfg.trend_tf, {}).get("trend") if cfg.trend_tf else None
            ev = autoscan.evaluate({iv: data[iv] for iv in cfg.timeframes}, cfg, trend)
            ev["time"] = time.time()
            self.last_scan[sym] = ev
            if not (trade and ev["signal"]):
                continue
            if len(positions) >= cfg.max_positions:
                ev["skipped"] = f"{len(positions)} position(s) open (max {cfg.max_positions})"
                continue
            if time.time() - self._scan_cool.get(sym, 0) < cfg.cooldown_sec:
                ev["skipped"] = "cooldown"
                continue
            self._scan_cool[sym] = time.time()
            await self._scan_open(sym, ev, data, cfg)
            positions = await self.engine.positions()
        return self.last_scan

    async def _scan_open(self, sym: str, ev: dict, data: dict, cfg: ScanConfig):
        side = ev["direction"]
        try:
            acct = await self.engine.account()
            margin = float(acct.get("available", 0)) * cfg.margin_pct / 100
            mark = float(await self.engine.mark_price(sym))
            atr = (data.get(cfg.atr_tf) or {}).get("atr14") or (data.get(cfg.timeframes[0]) or {}).get("atr14")
            if not atr or margin <= 0:
                await self._broadcast(f"⚠️ {sym} {side} signal ({ev['reason']}) but no ATR or no available balance; skipped.", self._target())
                return
            dist = cfg.sl_atr * float(atr)
            sl = mark - dist if side == "LONG" else mark + dist
            res = await self.engine.open_position(symbol=sym, side=side, margin_usdt=round(margin, 4), leverage=cfg.leverage,
                                                  margin_mode=cfg.margin_mode, stop_loss=sl)
            if not res.get("ok"):
                why = "; ".join(map(str, res.get("problems") or res.get("reasons") or [])) or str(res)[:300]
                await self._broadcast(f"⚠️ {sym} {side} signal ({ev['reason']}) but the order was not placed: {why}", self._target())
                return
            await asyncio.sleep(2)
            pos = await self.engine.find_position(sym, side)
            tps = []
            if pos and cfg.tp_ladder:
                entry = float(pos.get("avgOpenPrice") or mark)
                for r, pct in cfg.tp_ladder:
                    tp = entry + r * dist if side == "LONG" else entry - r * dist
                    t = await self.engine.set_tpsl(symbol=sym, position_id=pos["positionId"], take_profit=tp, percent=pct)
                    tps.append(f"{tp:.6g} ({pct:g}%)" + ("" if t.get("ok") else " failed"))
            lines = [f"🚀 Opened {side} {sym}: {ev['reason']}",
                     f"Qty {res.get('qty')} · lev {cfg.leverage}x {cfg.margin_mode} · margin ~{margin:.4f} USDT",
                     f"Entry ~{(pos or {}).get('avgOpenPrice', mark)} · SL {res.get('stop_loss')}"]
            if tps:
                lines.append("TP: " + ", ".join(tps))
            if pos and pos.get("liqPrice"):
                lines.append(f"Liq {pos['liqPrice']}")
            await self._broadcast("\n".join(lines), self._target())
        except Exception as e:
            log.exception("scan open")
            await self._broadcast(f"⚠️ {sym} {side} signal, but opening failed: {type(e).__name__}: {str(e)[:200]}", self._target())

    # ---------------------------------------------------------------- status report (no LLM)
    async def _report_loop(self):
        while True:
            every = env_float("REPORT_EVERY_SEC", 0)
            if every > 0:
                try:
                    await self._broadcast(await self.status_text(), self._target())
                except Exception:
                    log.exception("report loop")
            await asyncio.sleep(max(10.0, every) if every > 0 else 10.0)

    async def status_text(self) -> str:
        lines = []
        try:
            acct = await self.engine.account()
            lines.append(f"💰 Available {float(acct.get('available', 0)):.4f} {acct.get('marginCoin', 'USDT')}")
        except Exception as e:
            lines.append(f"Balance unavailable ({type(e).__name__})")
        positions = await self.engine.positions()
        if not positions:
            lines.append("No open position.")
        for p in positions:
            sym, roi = p.get("symbol"), position_roi(p)
            try:
                mark = await self.engine.mark_price(sym)
                sl = await self.engine.current_stop(sym, p["positionId"])
            except Exception:
                mark, sl = None, None
            trail = next((t for t in self.db.trailing() if str(t["position_id"]) == str(p["positionId"])), None)
            lines.append(f"📊 {sym} {p.get('side')} qty {p.get('qty')} · entry {p.get('avgOpenPrice')} · mark {mark}\n"
                         f"ROI {roi:.1f}% · uPnL {p.get('unrealizedPNL')} · SL {sl or 'none'} · liq {p.get('liqPrice') or '?'}"
                         + (f" · trailing {trail['callback_pct']}%" if trail else "") if roi is not None else
                         f"📊 {sym} {p.get('side')} qty {p.get('qty')}")
        for sym, ev in self.last_scan.items():
            tf = " ".join(f"{iv}:{(r['direction'] or '-')[0]}{int(r['confidence'])}" for iv, r in ev["timeframes"].items())
            lines.append(f"🔎 {sym} {tf} → {ev['reason']}")
        if not env_bool("AUTO_SCAN", False):
            lines.append("Scan is off (set AUTO_SCAN=true).")
        return "\n".join(lines)

    # ---------------------------------------------------------------- scheduled agent prompts
    async def _tasks_loop(self):
        while True:
            try:
                for t in self.db.due_tasks():
                    self.db.bump_task(t["id"], t["every_sec"])
                    if self.run_agent:
                        prompt = t["prompt"]
                        always = prompt.startswith("!ALWAYS ")
                        prompt = prompt.removeprefix("!ALWAYS ")
                        if not always:
                            prompt += ("\n\n(Quiet task: message the user ONLY if something happened since the last run: "
                                       "an entry/exit signal, an order placed or filled, TP/SL hit, a NEW error, or a big move. "
                                       "Otherwise reply with exactly: SILENT)")
                        reply = await self.run_agent(t["chat_id"], f"[Scheduled task {t['id']}] {prompt}")
                        if not always and _is_quiet(reply, self._last_task_reply.get(t["id"])):
                            continue
                        self._last_task_reply[t["id"]] = reply
                        await self._broadcast(reply, t["chat_id"])
            except Exception:
                log.exception("tasks loop")
            await asyncio.sleep(5)

    # ---------------------------------------------------------------- live account events
    async def _events_loop(self):
        q = self.engine.ws.events
        while True:
            evt = await q.get()
            try:
                text = self._format_event(evt)
                if text:
                    await self._broadcast(text)
            except Exception:
                log.exception("event format")

    def _format_event(self, evt: dict) -> str | None:
        k, d = evt.get("kind"), evt.get("data") or {}
        if k == "order":
            st = d.get("orderStatus")
            if st in ("FILLED", "PART_FILLED", "CANCELED", "PART_FILLED_CANCELED"):
                return (f"📦 Order {st}: {d.get('symbol')} {d.get('side')} {d.get('type')} qty {d.get('qty')} "
                        f"@ {d.get('averagePrice') or d.get('price')} (dealt {d.get('dealAmount')}) id {d.get('orderId')}")
        if k == "position":
            ev = d.get("event")
            if ev in ("OPEN", "CLOSE"):
                return (f"📈 Position {ev}: {d.get('symbol')} {d.get('side')} qty {d.get('qty')} lev {d.get('leverage')}x "
                        f"rPnL {d.get('realizedPNL')} uPnL {d.get('unrealizedPNL')}")
        if k == "tpsl":
            if d.get("event") == "CLOSE":
                return f"🎯 TP/SL {d.get('status')}: {d.get('symbol')} position {d.get('positionId')} – {d.get('meaning')}"
        return None


def _is_quiet(reply: str | None, last: str | None) -> bool:
    """Suppress 'nothing new' runs: SILENT, empty, or the same message as last time (ignoring numbers)."""
    import re
    r = (reply or "").strip()
    if not r or r.upper().startswith("SILENT") or r.upper().endswith("SILENT"):
        return True
    if last:
        norm = lambda x: re.sub(r"[\d.,:%+\-~]+", "#", x)
        return norm(r) == norm(last)
    return False


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, default)).replace("%", "").strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v.strip().lower() in ("1", "true", "yes", "on")


def position_roi(p: dict) -> float | None:
    """ROI % as the Bitunix app shows it: unrealizedPNL / margin x 100."""
    try:
        m = float(p.get("margin") or 0)
        if m > 0:
            return float(p.get("unrealizedPNL") or 0) / m * 100
    except (TypeError, ValueError):
        pass
    return None
