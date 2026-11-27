"""Tool registry for the agent.

Two layers:
1. api_<endpoint>: one tool per official REST endpoint (35), JSON schema generated from the official spec
   (every parameter, type, enum and requirement), so the model can reach anything the API offers.
2. Smart tools: sizing, opening/closing in either position mode, TP/SL, trailing stops, ladders, analysis,
   scanners, alerts, schedules, live WebSocket data, risk controls, journal, memory, calculator.
"""
from __future__ import annotations

import ast
import json
import math
import operator
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Awaitable, Callable

from ..exchange.errors import BitunixAPIError, BitunixNetworkError, ValidationError
from ..exchange.spec_generated import SPEC
from ..exchange.symbols import normalize_symbol
from ..engine.trading import pos_direction
from ..exchange.ws_spec import WS_CHANNELS

JSON_T = {"string": "string", "int": "integer", "bool": "boolean", "list": "array"}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Awaitable[Any]]
    mutating: bool = False

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description[:1000], "parameters": self.parameters}


@dataclass
class ToolContext:
    chat_id: str
    services: Any  # agent.core.Services
    confirmed: bool = False
    extra: dict = field(default_factory=dict)


def _param_schema(p: dict) -> dict:
    t = JSON_T.get(p["type"], "string")
    s: dict[str, Any] = {"description": p["description"][:300] or p["name"]}
    if t == "array":
        s["type"] = "array"
        props = {c["name"]: _param_schema(c) for c in p.get("children", [])}
        req = [c["name"] for c in p.get("children", []) if c["required"]]
        s["items"] = {"type": "object", "properties": props, **({"required": req} if req else {})}
    else:
        s["type"] = t
    if p.get("enum"):
        s["enum"] = p["enum"]
    return s


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, **({"required": required} if required else {})}


S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}


def _d(desc: str, base: dict) -> dict:
    return {**base, "description": desc}


# ---------------------------------------------------------------------- safe calculator
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.FloorDiv: operator.floordiv}
_FUNCS = {k: getattr(math, k) for k in ("sqrt", "log", "log10", "exp", "floor", "ceil", "fabs")} | {"abs": abs, "min": min, "max": max, "round": round}


def safe_eval(expr: str) -> float:
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError("unsupported expression")
    return ev(ast.parse(expr.replace("^", "**"), mode="eval"))


# ---------------------------------------------------------------------- registry
def build_tools() -> dict[str, Tool]:
    tools: dict[str, Tool] = {}

    def add(name, desc, params, mutating=False):
        def deco(fn):
            tools[name] = Tool(name, desc, params, fn, mutating)
            return fn
        return deco

    # ---------------- 1) raw endpoints, generated from the official spec
    for key, ep in SPEC.items():
        props = {p["name"]: _param_schema(p) for p in ep["params"]}
        req = [p["name"] for p in ep["params"] if p["required"]]
        rules = "; ".join(json.dumps(r) for r in ep["rules"])
        desc = (f"Official Bitunix futures endpoint {ep['method']} {ep['path']} ({'public' if ep['public'] else 'signed'}). "
                + (" ".join(ep["notes"]) + " " if ep["notes"] else "") + (f"Rules: {rules}" if rules else ""))

        def make(k):
            async def fn(ctx: ToolContext, **args):
                return await ctx.services.rest.call(k, args)
            return fn
        tools[f"api_{key}"] = Tool(f"api_{key}", desc, _obj(props, req), make(key), ep["mutating"])

    # ---------------- 2) smart tools
    trade_props = {
        "symbol": _d("e.g. BTCUSDT (BTC, btc/usdt also accepted)", S),
        "side": _d("LONG or SHORT", {"type": "string", "enum": ["LONG", "SHORT"]}),
        "order_type": {"type": "string", "enum": ["MARKET", "LIMIT"], "description": "default MARKET"},
        "price": _d("limit price (LIMIT only)", N),
        "qty": _d("size in base coin", N),
        "notional_usdt": _d("position value in USDT (qty = notional / price)", N),
        "margin_usdt": _d("margin to commit in USDT (qty = margin x leverage / price)", N),
        "risk_usdt": _d("USDT to lose if stop_loss hits (needs stop_loss)", N),
        "risk_pct": _d("percent of available balance to risk if stop_loss hits (needs stop_loss)", N),
        "leverage": _d("set leverage before opening (only changed if different)", I),
        "margin_mode": {"type": "string", "enum": ["ISOLATION", "CROSS"]},
        "take_profit": N, "stop_loss": N,
        "tp_stop_type": {"type": "string", "enum": ["MARK_PRICE", "LAST_PRICE"]},
        "sl_stop_type": {"type": "string", "enum": ["MARK_PRICE", "LAST_PRICE"]},
        "tp_order_type": {"type": "string", "enum": ["MARKET", "LIMIT"]},
        "sl_order_type": {"type": "string", "enum": ["MARKET", "LIMIT"]},
        "tp_order_price": N, "sl_order_price": N,
        "effect": {"type": "string", "enum": ["GTC", "IOC", "FOK", "POST_ONLY"]},
        "client_id": S,
        "skip_risk": _d("only when the user explicitly overrides the risk guard rails", B),
    }

    @add("open_position", "Open a LONG or SHORT futures position the smart way: sizes from qty/notional/margin/risk, rounds to the "
         "symbol's precision, sets leverage & margin mode if needed, validates TP/SL direction, HEDGE mode (tradeSide OPEN), "
         "splits oversize orders. Attach TP/SL in the same call.", _obj(trade_props, ["symbol", "side"]), mutating=True)
    async def _open(ctx, **a):
        return await ctx.services.engine.open_position(**a)

    @add("close_position", "Close a position fully (flash close) or partially (percent or qty), market or limit. HEDGE mode: tradeSide CLOSE + positionId.",
         _obj({"symbol": S, "side": {"type": "string", "enum": ["LONG", "SHORT"]}, "position_id": S,
               "percent": _d("1-100, default 100", N), "qty": N, "order_type": {"type": "string", "enum": ["MARKET", "LIMIT"]}, "price": N},
              ["symbol"]), mutating=True)
    async def _close(ctx, **a):
        return await ctx.services.engine.close_position(**a)

    @add("close_all_positions", "Close every position at market (optionally only one symbol).", _obj({"symbol": S}), mutating=True)
    async def _close_all(ctx, symbol=None):
        return await ctx.services.engine.close_all(symbol)

    @add("reverse_position", "Close the position and open the same size in the opposite direction.",
         _obj({"symbol": S, "side": {"type": "string", "enum": ["LONG", "SHORT"]}}, ["symbol"]), mutating=True)
    async def _rev(ctx, symbol, side=None):
        return await ctx.services.engine.reverse_position(symbol, side)

    @add("set_tpsl", "Set or change take-profit / stop-loss on an open position. No qty/percent = whole-position TP/SL "
         "(modified if one exists); qty/percent or LIMIT trigger orders = partial TP/SL order.",
         _obj({"symbol": S, "side": {"type": "string", "enum": ["LONG", "SHORT"]}, "position_id": S, "take_profit": N, "stop_loss": N,
               "qty": N, "percent": N, **{k: trade_props[k] for k in ("tp_stop_type", "sl_stop_type", "tp_order_type", "sl_order_type",
                                                                         "tp_order_price", "sl_order_price")}}, ["symbol"]), mutating=True)
    async def _tpsl(ctx, **a):
        return await ctx.services.engine.set_tpsl(**a)

    @add("move_stop_to_breakeven", "Move the stop loss to entry (+ small offset %) for a position.",
         _obj({"symbol": S, "side": {"type": "string", "enum": ["LONG", "SHORT"]}, "offset_pct": N}, ["symbol"]), mutating=True)
    async def _be(ctx, symbol, side=None, offset_pct=0.05):
        return await ctx.services.engine.move_stop_to_breakeven(symbol, side, offset_pct)

    @add("set_trailing_stop", "Trail the stop loss behind the best mark price by callback_pct, optionally only after activation_price.",
         _obj({"symbol": S, "side": {"type": "string", "enum": ["LONG", "SHORT"]}, "callback_pct": N, "activation_price": N},
              ["symbol", "callback_pct"]), mutating=True)
    async def _trail(ctx, symbol, callback_pct, side=None, activation_price=None):
        pos = await ctx.services.engine.find_position(symbol, side)
        if not pos:
            return {"ok": False, "problems": ["no open position"]}
        ctx.services.db.set_trailing(pos["positionId"], ctx.chat_id, normalize_symbol(symbol), pos_direction(pos), callback_pct, activation_price)
        return {"ok": True, "trailing": {"positionId": pos["positionId"], "callback_pct": callback_pct, "activation_price": activation_price}}

    @add("stop_trailing_stop", "Stop trailing for a position.", _obj({"position_id": S}, ["position_id"]))
    async def _untrail(ctx, position_id):
        ctx.services.db.stop_trailing(position_id)
        return {"ok": True}

    @add("ladder_orders", "Scale in with several LIMIT orders evenly spaced between price_from and price_to.",
         _obj({"symbol": S, "side": {"type": "string", "enum": ["LONG", "SHORT"]}, "total_qty": N, "total_notional_usdt": N,
               "price_from": N, "price_to": N, "count": I, "leverage": I, "take_profit": N, "stop_loss": N},
              ["symbol", "side", "price_from", "price_to"]), mutating=True)
    async def _ladder(ctx, **a):
        return await ctx.services.engine.ladder_orders(**a)

    @add("calc_position_size", "Compute the exact order qty (rounded to the symbol rules) without trading.",
         _obj({"symbol": S, "price": N, "qty": N, "notional_usdt": N, "margin_usdt": N, "leverage": I, "risk_usdt": N, "risk_pct": N,
               "stop_loss": N}, ["symbol"]))
    async def _size(ctx, symbol, price=None, **a):
        eng = ctx.services.engine
        p = Decimal(str(price)) if price else await eng.mark_price(symbol)
        q, how = await eng.compute_qty(symbol, price=p, **a)
        from ..exchange.validation import fmt_decimal
        return {"qty": fmt_decimal(q), "price": str(p), "notional_usdt": fmt_decimal(q * p), "how": how,
                "margin_needed": str(q * p / a["leverage"]) if a.get("leverage") else None}

    @add("estimate_liquidation", "Estimate the liquidation price for a planned isolated position from official position tiers.",
         _obj({"symbol": S, "side": {"type": "string", "enum": ["LONG", "SHORT"]}, "entry": N, "leverage": I, "qty": N},
              ["symbol", "side", "entry", "leverage", "qty"]))
    async def _liq(ctx, **a):
        return await ctx.services.engine.estimate_liquidation(**a)

    @add("account_overview", "Balance, positions, open orders, TP/SL orders, risk status, dry-run flag in one call.", _obj({}))
    async def _ov(ctx):
        return await ctx.services.engine.overview()

    @add("market_snapshot", "Price, mark/index, 24h stats, funding, spread, order-book imbalance and trading rules for a symbol.",
         _obj({"symbol": S}, ["symbol"]))
    async def _snap(ctx, symbol):
        return await ctx.services.engine.market_snapshot(symbol)

    @add("technical_analysis", "Indicators from closed candles: EMA20/50/200, RSI, MACD, Bollinger, ATR, StochRSI, ADX, VWAP, swings, trend.",
         _obj({"symbol": S, "interval": {"type": "string", "enum": next(p["enum"] for p in SPEC["get_kline"]["params"] if p["name"] == "interval")},
               "limit": I, "kline_type": {"type": "string", "enum": ["LAST_PRICE", "MARK_PRICE"]}}, ["symbol"]))
    async def _ta(ctx, symbol, interval="1h", limit=200, kline_type="LAST_PRICE"):
        return await ctx.services.engine.technical_analysis(symbol, interval, limit, kline_type)

    @add("multi_timeframe_analysis", "Technical analysis on 15m, 1h, 4h and 1d at once.", _obj({"symbol": S}, ["symbol"]))
    async def _mtf(ctx, symbol):
        return await ctx.services.engine.multi_timeframe(symbol)

    @add("scan_market", "Rank all futures by 24h change, loss, volume or volatility.",
         _obj({"sort_by": {"type": "string", "enum": ["change", "loss", "volume", "volatility", "abs_change"]}, "top": I, "min_quote_vol": N}))
    async def _scan(ctx, sort_by="change", top=15, min_quote_vol=1_000_000):
        return await ctx.services.engine.scan_market(top, sort_by, min_quote_vol)

    @add("funding_scan", "Most positive and most negative funding rates across all symbols.", _obj({"top": I}))
    async def _fscan(ctx, top=15):
        return await ctx.services.engine.funding_scan(top)

    @add("performance_report", "Win rate, total realized PnL, fees and funding from closed positions.",
         _obj({"symbol": S, "days": N}))
    async def _perf(ctx, symbol=None, days=30):
        start = int((time.time() - float(days) * 86400) * 1000)
        rows, skip = [], 0
        while True:
            kw = {"startTime": start, "limit": 100, "skip": skip}
            if symbol:
                kw["symbol"] = normalize_symbol(symbol)
            page = await ctx.services.rest.get_history_positions(**kw)
            lst = (page or {}).get("positionList", [])
            rows += lst
            if len(lst) < 100 or len(rows) >= 1000:
                break
            skip += 100
        pnl = [Decimal(r.get("realizedPNL") or 0) - Decimal(r.get("fee") or 0) + Decimal(r.get("funding") or 0) for r in rows]
        wins = [p for p in pnl if p > 0]
        return {"positions": len(rows), "wins": len(wins), "win_rate_pct": round(len(wins) / len(rows) * 100, 2) if rows else None,
                "net_pnl": str(sum(pnl, Decimal(0))), "gross_realized": str(sum((Decimal(r.get("realizedPNL") or 0) for r in rows), Decimal(0))),
                "fees": str(sum((Decimal(r.get("fee") or 0) for r in rows), Decimal(0))),
                "funding": str(sum((Decimal(r.get("funding") or 0) for r in rows), Decimal(0))),
                "best": str(max(pnl)) if pnl else None, "worst": str(min(pnl)) if pnl else None,
                "note": "net = realizedPNL - fee + funding (realizedPNL excludes fee and funding per docs)."}

    # ---------------- live websocket data
    @add("live_subscribe", "Subscribe to a live WebSocket channel: ticker, tickers, price, trade, depth_books, depth_book1/5/15, "
         "or kline (give interval + price_type market|mark).",
         _obj({"symbol": S, "channel": S, "interval": S, "price_type": {"type": "string", "enum": ["market", "mark"]}}, ["symbol", "channel"]))
    async def _wsub(ctx, symbol, channel, interval=None, price_type="market"):
        ws = ctx.services.ws
        if channel == "kline":
            await ws.subscribe_kline(normalize_symbol(symbol), interval or "1m", price_type)
        else:
            await ws.subscribe(normalize_symbol(symbol), channel)
        return {"ok": True, "subscriptions": ws.public_subscriptions()}

    @add("live_snapshot", "Latest live data received over WebSocket for a symbol (ticker, mark/index/funding, best bid/ask, book, last trades, live candle).",
         _obj({"symbol": S}, ["symbol"]))
    async def _wsnap(ctx, symbol):
        ws, s = ctx.services.ws, normalize_symbol(symbol)
        bid, ask = ws.best_bid_ask(s)
        return {"ticker": ws.tickers.get(s), "price": ws.prices.get(s), "best_bid": bid, "best_ask": ask,
                "book": ws.book_snapshot(s, 10), "last_trades": list(ws.trades.get(s, []))[-10:],
                "live_candles": {ch: v for (sym, ch), v in ws.klines.items() if sym == s}, "status": ws.status()}

    @add("live_account_state", "Positions, orders, TP/SL and balance as pushed by the private WebSocket.", _obj({}))
    async def _wacct(ctx):
        ws = ctx.services.ws
        return {"balance": ws.balance, "positions": ws.positions, "orders": ws.orders, "tpsl": ws.tpsl, "status": ws.status()}

    # ---------------- alerts & schedules
    @add("create_alert", "Notify the user when a condition is met. kind: price (last), mark, funding, pnl (unrealized PnL of the symbol).",
         _obj({"symbol": S, "kind": {"type": "string", "enum": ["price", "mark", "funding", "pnl"]}, "op": {"type": "string", "enum": [">=", "<="]},
               "value": N, "note": S, "repeat": B}, ["symbol", "kind", "op", "value"]))
    async def _alert(ctx, symbol, kind, op, value, note="", repeat=False):
        aid = ctx.services.db.add_alert(ctx.chat_id, normalize_symbol(symbol), kind, op, value, note, repeat)
        return {"ok": True, "alert_id": aid}

    @add("list_alerts", "Active alerts for this chat.", _obj({}))
    async def _alerts(ctx):
        return ctx.services.db.alerts(chat_id=ctx.chat_id)

    @add("delete_alert", "Delete an alert.", _obj({"alert_id": S}, ["alert_id"]))
    async def _dalert(ctx, alert_id):
        ctx.services.db.delete_alert(alert_id)
        return {"ok": True}

    @add("schedule_task", "Run an instruction on a repeating schedule, as fast as every 0.25 minutes (15 s). "
         "Quiet by default: the user is messaged only when something happens (signal, order, fill, TP/SL, new error, big move). "
         "Set report_every_run=true only if the user wants a message on every run.",
         _obj({"every_minutes": N, "instruction": S, "start_in_minutes": N, "report_every_run": B}, ["every_minutes", "instruction"]))
    async def _sched(ctx, every_minutes, instruction, start_in_minutes=None, report_every_run=False):
        every = max(15, int(float(every_minutes) * 60))
        first = time.time() + float(start_in_minutes) * 60 if start_in_minutes is not None else None
        prompt = ("!ALWAYS " if report_every_run else "") + instruction
        tid = ctx.services.db.add_task(ctx.chat_id, every, prompt, first)
        return {"ok": True, "task_id": tid, "every_seconds": every, "quiet": not report_every_run}

    @add("update_task", "Change a scheduled task: its interval and/or quiet mode.",
         _obj({"task_id": S, "every_minutes": N, "report_every_run": B}, ["task_id"]))
    async def _upd_task(ctx, task_id, every_minutes=None, report_every_run=None):
        db = ctx.services.db
        t = next((x for x in db.tasks(ctx.chat_id) if x["id"] == task_id), None)
        if not t:
            return {"ok": False, "error": f"no task {task_id}", "tasks": db.tasks(ctx.chat_id)}
        every = max(15, int(float(every_minutes) * 60)) if every_minutes is not None else t["every_sec"]
        prompt = t["prompt"]
        if report_every_run is not None:
            prompt = ("!ALWAYS " if report_every_run else "") + prompt.removeprefix("!ALWAYS ")
        db._x("UPDATE tasks SET every_sec=?, prompt=? WHERE id=?", (every, prompt, task_id))
        return {"ok": True, "task_id": task_id, "every_seconds": every, "quiet": not prompt.startswith("!ALWAYS ")}

    @add("list_tasks", "Scheduled tasks for this chat.", _obj({}))
    async def _tasks(ctx):
        return ctx.services.db.tasks(ctx.chat_id)

    @add("delete_task", "Stop a scheduled task.", _obj({"task_id": S}, ["task_id"]))
    async def _dtask(ctx, task_id):
        ctx.services.db.delete_task(task_id)
        return {"ok": True}

    # ---------------- risk & modes
    @add("risk_status", "Current guard rails, kill switch and dry-run state.", _obj({}))
    async def _rs(ctx):
        return {**ctx.services.engine.risk.status(), "dry_run": ctx.services.rest.dry_run,
                "confirm_trades": ctx.services.settings.confirm_trades}

    @add("update_risk_settings", "Change guard rails at runtime (only when the user asks).",
         _obj({"max_leverage": I, "max_position_notional": N, "max_risk_per_trade_pct": N, "daily_loss_limit": N,
               "require_stop_loss": B, "kill_switch": B}))
    async def _ur(ctx, **a):
        s = ctx.services.settings
        for k, v in a.items():
            if k == "kill_switch":
                ctx.services.engine.risk.kill_switch = bool(v)
            else:
                setattr(s, k, v)
        return {"ok": True, **ctx.services.engine.risk.status()}

    @add("set_dry_run", "Switch paper mode on/off. When on, mutating calls are validated and signed-ready but not sent.",
         _obj({"enabled": B}, ["enabled"]))
    async def _dry(ctx, enabled):
        ctx.services.rest.dry_run = bool(enabled)
        try:  # remember it across restarts
            from ..extensions.store import SettingsStore
            SettingsStore(ctx.services.settings.db_path).set("DRY_RUN", "true" if enabled else "false")
        except Exception:
            pass
        return {"ok": True, "dry_run": ctx.services.rest.dry_run}

    @add("set_confirmation_mode", "always = ask before every trade, never = execute directly, above = only above CONFIRM_ABOVE_NOTIONAL USDT.",
         _obj({"mode": {"type": "string", "enum": ["always", "never", "above"]}, "above_notional": N}, ["mode"]))
    async def _conf(ctx, mode, above_notional=None):
        ctx.services.settings.confirm_trades = mode
        if above_notional is not None:
            ctx.services.settings.confirm_above_notional = float(above_notional)
        return {"ok": True, "confirm_trades": mode}

    # ---------------- confirmation flow
    @add("confirm_action", "Execute a pending action after the user said yes. Pass the confirmation id.", _obj({"confirmation_id": S}, ["confirmation_id"]))
    async def _confirm(ctx, confirmation_id):
        row = ctx.services.db.take_confirmation(confirmation_id, ctx.chat_id)
        if not row:
            return {"ok": False, "problems": ["confirmation id not found, already used or older than 10 minutes"],
                    "pending": ctx.services.db.pending_confirmations(ctx.chat_id)}
        return await ctx.services.executor.execute(row["tool"], row["args"], ctx, confirmed=True)

    @add("list_pending_confirmations", "Actions waiting for the user's yes.", _obj({}))
    async def _pend(ctx):
        return ctx.services.db.pending_confirmations(ctx.chat_id)

    # ---------------- knowledge & utilities
    @add("api_docs", "Read the official spec of a Bitunix endpoint or WebSocket channel (params, enums, rules, notes, response fields, doc URL). "
         "Call with no name to list everything.", _obj({"name": S}))
    async def _docs(ctx, name=None):
        if not name:
            return {"rest_endpoints": {k: f"{v['method']} {v['path']}" for k, v in SPEC.items()}, "ws_channels": list(WS_CHANNELS)}
        name = name.replace("api_", "")
        if name in SPEC:
            return SPEC[name]
        if name in WS_CHANNELS:
            return WS_CHANNELS[name]
        from ..exchange.errors import ERROR_CODES, FIX_HINTS
        if name.isdigit():
            c = int(name)
            return {"code": c, "official": ERROR_CODES.get(c), "hint": FIX_HINTS.get(c)}
        return {"error": f"unknown {name}", "available": list(SPEC) + list(WS_CHANNELS)}

    @add("raw_request", "Signed request to any Bitunix futures path (for endpoints added after this build). GET params go in params, POST in params as body.",
         _obj({"method": {"type": "string", "enum": ["GET", "POST"]}, "path": S, "params": {"type": "object"}, "signed": B}, ["method", "path"]), mutating=True)
    async def _raw(ctx, method, path, params=None, signed=True):
        if method == "GET":
            return await ctx.services.rest.request("GET", path, query=params or {}, signed=signed)
        return await ctx.services.rest.request("POST", path, body=params or {}, signed=signed)

    @add("calculator", "Evaluate arithmetic (+ - * / ** %, sqrt, log, exp, min, max, round, abs).", _obj({"expression": S}, ["expression"]))
    async def _calc(ctx, expression):
        return {"result": safe_eval(expression)}

    @add("remember", "Save a long-term note about the user's preferences or strategy (key/value).", _obj({"key": S, "value": S}, ["key", "value"]))
    async def _rem(ctx, key, value):
        ctx.services.db.set_note(key, value)
        return {"ok": True}

    @add("forget", "Delete a long-term note.", _obj({"key": S}, ["key"]))
    async def _forget(ctx, key):
        ctx.services.db.delete_note(key)
        return {"ok": True}

    @add("trade_journal", "Recent actions this agent took on the exchange (requests, responses, success).", _obj({"limit": I}))
    async def _journal(ctx, limit=20):
        return ctx.services.db.journal_rows(limit)

    @add("server_time", "Current UTC and local time, and the client clock offset vs the Bitunix server.", _obj({}))
    async def _time(ctx):
        return {"utc_ms": int(time.time() * 1000), "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                "clock_offset_ms": ctx.services.rest.clock_offset_ms}

    return tools


# ---------------------------------------------------------------------- executor
class ToolExecutor:
    def __init__(self, tools: dict[str, Tool], services):
        self.tools = tools
        self.s = services

    def needs_confirmation(self, tool: Tool, args: dict) -> bool:
        if not tool.mutating or self.s.rest.dry_run:
            return False
        mode = self.s.settings.confirm_trades
        if mode == "never":
            return False
        if mode == "above":
            n = args.get("notional_usdt") or (float(args["qty"]) * float(args["price"]) if args.get("qty") and args.get("price") else None)
            if n is not None and float(n) < self.s.settings.confirm_above_notional and tool.name not in ("close_all_positions", "api_close_all_position", "raw_request"):
                return False
        return True

    async def execute(self, name: str, args: dict, ctx: ToolContext, confirmed: bool = False) -> Any:
        tool = self.tools.get(name)
        if not tool:
            return {"error": "unknown_tool", "hint": f"Available tools include: {', '.join(list(self.tools)[:80])}"}
        args = {k: v for k, v in (args or {}).items() if v is not None and k != "_raw"}
        if not confirmed and self.needs_confirmation(tool, args):
            summary = f"{name} {json.dumps(args, default=str)}"
            cid = self.s.db.add_confirmation(ctx.chat_id, name, args, summary)
            return {"status": "PENDING_CONFIRMATION", "confirmation_id": cid, "summary": summary,
                    "instruction": "Show the user exactly what will happen (symbol, side, size, price, leverage, TP/SL, est. margin) and ask for a yes. "
                                   "When they confirm, call confirm_action with this id."}
        try:
            res = await tool.fn(ctx, **args)
            if tool.mutating:
                self.s.db.journal(ctx.chat_id, name, args, res, not (isinstance(res, dict) and res.get("ok") is False))
            return res
        except (BitunixAPIError, ValidationError, BitunixNetworkError) as e:
            if tool.mutating:
                self.s.db.journal(ctx.chat_id, name, args, e.to_dict(), False)
            return {**e.to_dict(), "next_step": "Use the hint to correct the call and try again, or choose another tool. Do not give up."}
        except TypeError as e:
            return {"error": "bad_arguments", "detail": str(e), "schema": tool.parameters}
        except Exception as e:  # never crash the conversation
            return {"error": type(e).__name__, "detail": str(e), "next_step": "Try a different approach or tool."}
