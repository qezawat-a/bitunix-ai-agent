"""High-level trading engine on top of the raw endpoints.

Knows the rules the docs spread across pages, so the agent does not have to:
- HEDGE vs ONE_WAY order parameters (tradeSide / positionId / reduceOnly)
- leverage + margin mode set before opening, only when different
- qty from USDT notional, margin, or risk-to-stop; rounded to basePrecision; min/max volume
- price rounded to quotePrecision; priceProtectScope band; TP/SL direction checks vs mark/entry/liq
- orders above maxLimit/maxMarket volume split into batch_order chunks of <= 5
- full close via flash_close_position, partial close via place_order, both modes
- position TP/SL vs partial TP/SL chosen automatically; existing ones modified instead of duplicated
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from ..config import Settings
from ..exchange.errors import BitunixAPIError
from ..exchange.rest import BitunixREST
from ..exchange.symbols import D, SymbolCache, normalize_symbol
from ..exchange.validation import fmt_decimal
from ..exchange.ws import BitunixWS
from . import indicators
from .risk import RiskManager

log = logging.getLogger("bitunix.engine")

LONG_ALIASES = {"LONG", "BUY", "BULL", "UP", "L"}
SHORT_ALIASES = {"SHORT", "SELL", "BEAR", "DOWN", "S"}


def direction(x: str) -> str:
    u = x.strip().upper()
    if u in LONG_ALIASES:
        return "LONG"
    if u in SHORT_ALIASES:
        return "SHORT"
    raise ValueError(f"direction must be LONG or SHORT, got {x!r}")


class TradingEngine:
    def __init__(self, rest: BitunixREST, ws: BitunixWS | None, settings: Settings, risk: RiskManager | None = None):
        self.rest = rest
        self.ws = ws
        self.s = settings
        self.symbols = SymbolCache(rest)
        self.risk = risk or RiskManager(settings)

    # ------------------------------------------------------------------ reads
    async def account(self) -> dict:
        acct = await self.rest.get_single_account(marginCoin=self.s.margin_coin)
        return acct

    async def position_mode(self) -> str:
        if self.rest.position_mode:
            return self.rest.position_mode
        try:
            acct = await self.account()
            return acct.get("positionMode", "ONE_WAY")
        except Exception:
            return "ONE_WAY"

    async def positions(self, symbol: str | None = None) -> list[dict]:
        kw = {"symbol": normalize_symbol(symbol)} if symbol else {}
        return await self.rest.get_pending_positions(**kw) or []

    async def find_position(self, symbol: str, side: str | None = None, position_id: str | None = None) -> dict | None:
        ps = await self.positions(symbol)
        if position_id:
            return next((p for p in ps if str(p["positionId"]) == str(position_id)), None)
        if side:
            ps = [p for p in ps if p.get("side") == direction(side)]
        return ps[0] if len(ps) == 1 else (max(ps, key=lambda p: abs(D(p.get("qty", 0)))) if ps else None)

    async def mark_price(self, symbol: str) -> Decimal:
        symbol = normalize_symbol(symbol)
        if self.ws and symbol in self.ws.prices and self.ws.prices[symbol].get("mp"):
            return D(self.ws.prices[symbol]["mp"])
        fr = await self.rest.get_funding_rate(symbol=symbol)
        return D(fr["markPrice"])

    async def last_price(self, symbol: str) -> Decimal:
        symbol = normalize_symbol(symbol)
        if self.ws and self.ws.tickers.get(symbol, {}).get("la"):
            return D(self.ws.tickers[symbol]["la"])
        t = await self.rest.get_tickers(symbols=symbol)
        return D(t[0]["lastPrice"])

    async def overview(self) -> dict:
        acct, pos, orders, tpsl = await asyncio.gather(
            self.account(), self.positions(), self.rest.get_pending_orders(limit=100),
            self.rest.get_pending_tpsl_orders(limit=100), return_exceptions=True)
        def ok(x):
            return {"error": str(x)} if isinstance(x, Exception) else x
        return {"account": ok(acct), "positions": ok(pos), "open_orders": ok(orders), "tpsl_orders": ok(tpsl),
                "risk": self.risk.status(), "dry_run": self.rest.dry_run}

    async def market_snapshot(self, symbol: str) -> dict:
        symbol = normalize_symbol(symbol)
        rules = await self.symbols.get(symbol)
        tick, fr, depth = await asyncio.gather(self.rest.get_tickers(symbols=symbol), self.rest.get_funding_rate(symbol=symbol),
                                               self.rest.get_depth(symbol=symbol, limit="50"))
        t = tick[0] if tick else {}
        bids = sum(D(q) for _, q in depth["bids"][:20]); asks = sum(D(q) for _, q in depth["asks"][:20])
        best_bid, best_ask = D(depth["bids"][0][0]), D(depth["asks"][0][0])
        last, opn = D(t.get("lastPrice") or t.get("last")), D(t.get("open") or 0)
        return {
            "symbol": symbol, "last": str(last), "mark": fr.get("markPrice"), "index": fr.get("indexPrice"),
            "change_24h_pct": round(float((last / opn - 1) * 100), 3) if opn else None,
            "high_24h": t.get("high"), "low_24h": t.get("low"), "base_vol_24h": t.get("baseVol"), "quote_vol_24h": t.get("quoteVol"),
            "funding_rate_raw": fr.get("fundingRate"),
            "funding_note": "get_funding_rate returns the rate in percent units on the live API (e.g. 0.0083 = 0.0083%); funding history returns a plain fraction.",
            "funding_interval_h": fr.get("fundingInterval"), "next_funding_time": fr.get("nextFundingTime"),
            "best_bid": str(best_bid), "best_ask": str(best_ask), "spread_bps": round(float((best_ask - best_bid) / best_ask * 10000), 3),
            "book_imbalance_top20": round(float((bids - asks) / (bids + asks)), 4) if bids + asks else None,
            "rules": {"minTradeVolume": str(rules.min_trade_volume), "basePrecision": rules.base_precision,
                      "quotePrecision": rules.quote_precision, "maxLeverage": rules.max_leverage, "minLeverage": rules.min_leverage,
                      "maxLimitOrderVolume": str(rules.max_limit_order_volume), "maxMarketOrderVolume": str(rules.max_market_order_volume),
                      "priceProtectScope": str(rules.price_protect_scope), "symbolStatus": rules.symbol_status, "isApiSupported": rules.is_api_supported},
        }

    async def technical_analysis(self, symbol: str, interval: str = "1h", limit: int = 200, kline_type: str = "LAST_PRICE") -> dict:
        symbol = normalize_symbol(symbol)
        candles = await self.rest.get_kline(symbol=symbol, interval=interval, limit=min(limit, 200), type=kline_type)
        out = indicators.summarize(candles)
        out.update({"symbol": symbol, "interval": interval, "first_open_time": candles[0]["time"] if candles else None,
                    "last_open_time": candles[-1]["time"] if candles else None})
        return out

    async def multi_timeframe(self, symbol: str, intervals=("15m", "1h", "4h", "1d")) -> dict:
        res = await asyncio.gather(*(self.technical_analysis(symbol, iv) for iv in intervals), return_exceptions=True)
        return {iv: (r if not isinstance(r, Exception) else {"error": str(r)}) for iv, r in zip(intervals, res)}

    async def scan_market(self, top: int = 15, sort_by: str = "change", min_quote_vol: float = 1_000_000) -> list[dict]:
        tickers = await self.rest.get_tickers()
        rows = []
        for t in tickers or []:
            try:
                last, opn, qv = D(t.get("lastPrice") or t["last"]), D(t["open"]), D(t.get("quoteVol") or 0)
                if qv < D(min_quote_vol) or opn == 0:
                    continue
                rows.append({"symbol": t["symbol"], "last": str(last), "change_pct": float((last / opn - 1) * 100),
                             "quote_vol": float(qv), "range_pct": float((D(t["high"]) - D(t["low"])) / opn * 100)})
            except Exception:
                continue
        key = {"change": lambda r: r["change_pct"], "loss": lambda r: -r["change_pct"], "volume": lambda r: r["quote_vol"],
               "volatility": lambda r: r["range_pct"]}.get(sort_by, lambda r: abs(r["change_pct"]))
        return sorted(rows, key=key, reverse=True)[:top]

    async def funding_scan(self, top: int = 15) -> dict:
        data = await self.rest.get_funding_rate_batch()
        rows = [{"symbol": d["symbol"], "fundingRate": d["fundingRate"], "interval_h": d.get("fundingInterval"),
                 "next": d.get("nextFundingTime")} for d in data or []]
        rows.sort(key=lambda r: float(r["fundingRate"]))
        return {"most_negative": rows[:top], "most_positive": rows[::-1][:top]}

    # ------------------------------------------------------------------ sizing
    async def compute_qty(self, symbol: str, *, price: Decimal, qty=None, notional_usdt=None, margin_usdt=None,
                          leverage: int | None = None, risk_usdt=None, risk_pct=None, stop_loss=None) -> tuple[Decimal, str]:
        rules = await self.symbols.get(symbol)
        how = ""
        if qty is not None:
            q, how = D(qty), "qty"
        elif notional_usdt is not None:
            q, how = D(notional_usdt) / price, f"notional {notional_usdt} USDT"
        elif margin_usdt is not None:
            if not leverage:
                raise ValueError("margin_usdt needs leverage")
            q, how = D(margin_usdt) * leverage / price, f"margin {margin_usdt} USDT x {leverage}"
        elif (risk_usdt is not None or risk_pct is not None) and stop_loss is not None:
            r = D(risk_usdt) if risk_usdt is not None else D((await self.account()).get("available", 0)) * D(risk_pct) / 100
            dist = abs(price - D(stop_loss))
            if dist == 0:
                raise ValueError("stop_loss equals entry price")
            q, how = r / dist, f"risk {r:.4f} USDT to stop {stop_loss}"
        else:
            raise ValueError("give one of qty, notional_usdt, margin_usdt, or risk_usdt/risk_pct + stop_loss")
        q = rules.round_qty(q, "down")
        if q < rules.min_trade_volume:
            q = rules.min_trade_volume
            how += f" (raised to minTradeVolume {rules.min_trade_volume})"
        return q, how

    async def ensure_leverage_and_mode(self, symbol: str, leverage: int | None, margin_mode: str | None) -> dict:
        symbol = normalize_symbol(symbol)
        done = {}
        cur = await self.rest.get_leverage_margin_mode(symbol=symbol, marginCoin=self.s.margin_coin)
        if margin_mode:
            mm = {"ISOLATED": "ISOLATION"}.get(margin_mode.upper(), margin_mode.upper())
            if cur.get("marginMode") != mm:
                done["margin_mode"] = await self.rest.change_margin_mode(marginMode=mm, symbol=symbol, marginCoin=self.s.margin_coin)
        if leverage:
            rules = await self.symbols.get(symbol)
            lev = max(rules.min_leverage, min(int(leverage), rules.max_leverage))
            if lev != int(leverage):
                done["leverage_clamped"] = f"{leverage}x outside {rules.min_leverage}..{rules.max_leverage}x; using {lev}x"
            if int(cur.get("leverage") or 0) != lev:
                done["leverage"] = await self.rest.change_leverage(marginCoin=self.s.margin_coin, symbol=symbol, leverage=lev)
        done["current"] = cur
        return done

    # ------------------------------------------------------------------ TP/SL checks
    @staticmethod
    def check_tpsl(side: str, ref_price: Decimal, tp=None, sl=None, liq=None) -> list[str]:
        p = []
        if tp is not None:
            tp = D(tp)
            if side == "LONG" and tp <= ref_price:
                p.append(f"LONG take-profit {tp} must be above {ref_price} (30008/30020/30026/30027)")
            if side == "SHORT" and tp >= ref_price:
                p.append(f"SHORT take-profit {tp} must be below {ref_price} (30007/30021/30032/30033)")
        if sl is not None:
            sl = D(sl)
            if side == "LONG" and sl >= ref_price:
                p.append(f"LONG stop-loss {sl} must be below {ref_price} (30010/30023/30028/30029)")
            if side == "SHORT" and sl <= ref_price:
                p.append(f"SHORT stop-loss {sl} must be above {ref_price} (30009/30022/30030/30031)")
            if liq and D(liq) > 0:
                if side == "LONG" and sl <= D(liq):
                    p.append(f"LONG stop-loss {sl} must be above liquidation price {liq} (30025)")
                if side == "SHORT" and sl >= D(liq):
                    p.append(f"SHORT stop-loss {sl} must be below liquidation price {liq} (30024)")
        return p

    # ------------------------------------------------------------------ open
    async def open_position(self, *, symbol: str, side: str, order_type: str = "MARKET", price=None, qty=None,
                            notional_usdt=None, margin_usdt=None, risk_usdt=None, risk_pct=None, leverage: int | None = None,
                            margin_mode: str | None = None, take_profit=None, stop_loss=None, tp_stop_type: str = "MARK_PRICE",
                            sl_stop_type: str = "MARK_PRICE", tp_order_type: str = "MARKET", sl_order_type: str = "MARKET",
                            tp_order_price=None, sl_order_price=None, effect: str = "GTC", client_id: str | None = None,
                            skip_risk: bool = False) -> dict:
        symbol = normalize_symbol(symbol)
        side = direction(side)
        order_type = order_type.upper()
        rules = await self.symbols.get(symbol)
        mark = await self.mark_price(symbol)
        ref = D(price) if (order_type == "LIMIT" and price is not None) else mark
        if order_type == "LIMIT" and price is None:
            raise ValueError("LIMIT order needs price")
        if order_type == "LIMIT":
            price = rules.round_price(price)
            ref = price
        q, how = await self.compute_qty(symbol, price=ref, qty=qty, notional_usdt=notional_usdt, margin_usdt=margin_usdt,
                                        leverage=leverage, risk_usdt=risk_usdt, risk_pct=risk_pct, stop_loss=stop_loss)
        tp = rules.round_price(take_profit) if take_profit is not None else None
        sl = rules.round_price(stop_loss) if stop_loss is not None else None
        problems = self.check_tpsl(side, ref, tp, sl)
        # marketable-limit band check uses mark
        problems += [x for x in rules.check_order(q, price, order_type, "BUY" if side == "LONG" else "SELL", mark)
                     if "max " not in x or "split" not in x]
        if problems:
            return {"ok": False, "stage": "preflight", "problems": problems, "mark_price": str(mark), "qty": fmt_decimal(q), "sizing": how,
                    "hint": "Adjust the listed values (or ask me to) and resend."}
        lev = leverage or int((await self.rest.get_leverage_margin_mode(symbol=symbol, marginCoin=self.s.margin_coin)).get("leverage") or rules.default_leverage)
        equity = None
        try:
            acct = await self.account()
            equity = D(acct.get("available", 0)) + D(acct.get("margin", 0)) + D(acct.get("crossUnrealizedPNL", 0))
        except Exception:
            pass
        verdict = self.risk.check_open(symbol=symbol, qty=q, price=ref, leverage=lev, stop_loss=sl, equity=equity, side=side)
        if not verdict.ok and not skip_risk:
            return {"ok": False, "stage": "risk", **verdict.to_dict(), "qty": fmt_decimal(q), "price": str(ref),
                    "hint": "Offer the adjusted_plan to the user, or resend with skip_risk=true if the user explicitly overrides."}
        setup = await self.ensure_leverage_and_mode(symbol, leverage, margin_mode)
        base: dict[str, Any] = {"side": "BUY" if side == "LONG" else "SELL", "tradeSide": "OPEN", "orderType": order_type}
        if order_type == "LIMIT":
            base["price"] = price
            base["effect"] = effect
        if tp is not None:
            base.update({"tpPrice": tp, "tpStopType": tp_stop_type, "tpOrderType": tp_order_type})
            if tp_order_type.upper() == "LIMIT":
                base["tpOrderPrice"] = rules.round_price(tp_order_price or tp)
        if sl is not None:
            base.update({"slPrice": sl, "slStopType": sl_stop_type, "slOrderType": sl_order_type})
            if sl_order_type.upper() == "LIMIT":
                base["slOrderPrice"] = rules.round_price(sl_order_price or sl)
        max_vol = rules.max_limit_order_volume if order_type == "LIMIT" else rules.max_market_order_volume
        chunks = _split(q, max_vol, rules) if max_vol and q > max_vol else [q]
        if len(chunks) == 1:
            res = await self.rest.place_order(symbol=symbol, qty=chunks[0], clientId=client_id, **base)
            results = [res]
        else:
            results = []
            for i in range(0, len(chunks), 5):
                results.append(await self.rest.batch_order(symbol=symbol, orderList=[{"qty": c, **base} for c in chunks[i:i + 5]]))
        return {"ok": True, "symbol": symbol, "direction": side, "qty": fmt_decimal(q), "sizing": how, "order_type": order_type,
                "price": str(price) if price is not None else f"market (~{mark})", "leverage": lev, "take_profit": str(tp) if tp else None,
                "stop_loss": str(sl) if sl else None, "chunks": [fmt_decimal(c) for c in chunks], "setup": setup, "result": results,
                "warnings": verdict.warnings,
                "note": "Order accepted by the API; fills are confirmed on the order/position WebSocket channels."}

    # ------------------------------------------------------------------ close
    async def close_position(self, *, symbol: str, side: str | None = None, position_id: str | None = None,
                             percent: float | None = None, qty=None, order_type: str = "MARKET", price=None) -> dict:
        symbol = normalize_symbol(symbol)
        pos = await self.find_position(symbol, side, position_id)
        if not pos:
            return {"ok": False, "problems": [f"No open position on {symbol}" + (f" side {side}" if side else "")],
                    "hint": "Check get_pending_positions."}
        rules = await self.symbols.get(symbol)
        pqty = D(pos["qty"])
        if qty is None and (percent is None or float(percent) >= 100) and order_type.upper() == "MARKET":
            res = await self.rest.flash_close_position(positionId=pos["positionId"])
            return {"ok": True, "method": "flash_close_position", "position": pos, "result": res}
        q = D(qty) if qty is not None else pqty * D(percent) / 100
        q = min(rules.round_qty(q, "down"), pqty)
        if q <= 0:
            return {"ok": False, "problems": [f"close qty rounds to 0 (basePrecision {rules.base_precision})"]}
        mode = pos.get("positionMode") or await self.position_mode()
        is_long = pos["side"] == "LONG"
        common: dict[str, Any] = {"symbol": symbol, "qty": q, "orderType": order_type.upper()}
        if order_type.upper() == "LIMIT":
            common["price"] = rules.round_price(price)
            common["effect"] = "GTC"
        if mode == "HEDGE":
            # official table: close long = side BUY + tradeSide CLOSE; close short = side SELL + tradeSide CLOSE
            primary = {**common, "side": "BUY" if is_long else "SELL", "tradeSide": "CLOSE", "positionId": pos["positionId"]}
            fallback = {**primary, "side": "SELL" if is_long else "BUY"}
        else:
            primary = {**common, "side": "SELL" if is_long else "BUY", "reduceOnly": True}
            fallback = {**common, "side": "SELL" if is_long else "BUY", "tradeSide": "CLOSE", "positionId": pos["positionId"]}
        try:
            res = await self.rest.place_order(**primary)
            used = primary
        except BitunixAPIError as e:
            # parameter-shape rejections never execute anything, so trying the alternate documented form is safe
            if e.code in (10002, 10008, 30004, 30018, 30019):
                res = await self.rest.place_order(**fallback)
                used = fallback
            else:
                raise
        return {"ok": True, "method": "place_order", "mode": mode, "closed_qty": fmt_decimal(q), "of": str(pqty), "request": used, "result": res}

    async def close_all(self, symbol: str | None = None) -> dict:
        kw = {"symbol": normalize_symbol(symbol)} if symbol else {}
        return {"ok": True, "result": await self.rest.close_all_position(**kw)}

    async def reverse_position(self, symbol: str, side: str | None = None) -> dict:
        pos = await self.find_position(symbol, side)
        if not pos:
            return {"ok": False, "problems": ["no position to reverse"]}
        closed = await self.close_position(symbol=symbol, position_id=pos["positionId"])
        new_side = "SHORT" if pos["side"] == "LONG" else "LONG"
        opened = await self.open_position(symbol=symbol, side=new_side, qty=pos["qty"], leverage=int(pos.get("leverage") or 0) or None)
        return {"ok": True, "closed": closed, "opened": opened}

    # ------------------------------------------------------------------ TP/SL
    async def set_tpsl(self, *, symbol: str, side: str | None = None, position_id: str | None = None, take_profit=None,
                       stop_loss=None, qty=None, percent: float | None = None, tp_stop_type: str = "MARK_PRICE",
                       sl_stop_type: str = "MARK_PRICE", tp_order_type: str = "MARKET", sl_order_type: str = "MARKET",
                       tp_order_price=None, sl_order_price=None) -> dict:
        symbol = normalize_symbol(symbol)
        if take_profit is None and stop_loss is None:
            return {"ok": False, "problems": ["give take_profit and/or stop_loss (30006)"]}
        pos = await self.find_position(symbol, side, position_id)
        if not pos:
            return {"ok": False, "problems": [f"no open position on {symbol}"], "hint": "TP/SL needs a positionId; open the position first or attach tp/sl to the order."}
        rules = await self.symbols.get(symbol)
        mark = await self.mark_price(symbol)
        tp = rules.round_price(take_profit) if take_profit is not None else None
        sl = rules.round_price(stop_loss) if stop_loss is not None else None
        problems = self.check_tpsl(pos["side"], mark, tp, sl, pos.get("liqPrice"))
        if problems:
            return {"ok": False, "stage": "preflight", "problems": problems, "mark_price": str(mark), "position": pos}
        pid = pos["positionId"]
        partial = qty is not None or (percent is not None and float(percent) < 100) or \
            tp_order_type.upper() == "LIMIT" or sl_order_type.upper() == "LIMIT"
        if not partial:
            existing = [o for o in (await self.rest.get_pending_tpsl_orders(symbol=symbol, positionId=pid) or [])
                        if not o.get("tpQty") and not o.get("slQty")]
            args = {"symbol": symbol, "positionId": pid}
            if tp is not None:
                args.update(tpPrice=tp, tpStopType=tp_stop_type)
            if sl is not None:
                args.update(slPrice=sl, slStopType=sl_stop_type)
            try:
                if existing:
                    res = await self.rest.modify_position_tpsl_order(**args)
                    return {"ok": True, "method": "modify_position_tpsl_order", "result": res}
                res = await self.rest.place_position_tpsl_order(**args)
                return {"ok": True, "method": "place_position_tpsl_order", "result": res}
            except BitunixAPIError as e:
                if not existing and e.code in (10002, 30011, 40004):
                    res = await self.rest.modify_position_tpsl_order(**args)  # one position TP/SL per position: modify it
                    return {"ok": True, "method": "modify_position_tpsl_order", "result": res}
                raise
        q = D(qty) if qty is not None else D(pos["qty"]) * D(percent if percent is not None else 100) / 100
        q = min(rules.round_qty(q), D(pos["qty"]))
        args = {"symbol": symbol, "positionId": pid}
        if tp is not None:
            args.update(tpPrice=tp, tpStopType=tp_stop_type, tpOrderType=tp_order_type, tpQty=q)
            if tp_order_type.upper() == "LIMIT":
                args["tpOrderPrice"] = rules.round_price(tp_order_price or tp)
        if sl is not None:
            args.update(slPrice=sl, slStopType=sl_stop_type, slOrderType=sl_order_type, slQty=q)
            if sl_order_type.upper() == "LIMIT":
                args["slOrderPrice"] = rules.round_price(sl_order_price or sl)
        res = await self.rest.place_tpsl_order(**args)
        return {"ok": True, "method": "place_tpsl_order", "qty": fmt_decimal(q), "result": res}

    async def move_stop_to_breakeven(self, symbol: str, side: str | None = None, offset_pct: float = 0.05) -> dict:
        pos = await self.find_position(symbol, side)
        if not pos:
            return {"ok": False, "problems": ["no position"]}
        entry = D(pos.get("avgOpenPrice") or D(pos["entryValue"]) / D(pos["qty"]))
        off = entry * D(offset_pct) / 100
        sl = entry + off if pos["side"] == "LONG" else entry - off
        return await self.set_tpsl(symbol=symbol, position_id=pos["positionId"], stop_loss=sl)

    # ------------------------------------------------------------------ ladders
    async def ladder_orders(self, *, symbol: str, side: str, total_qty=None, total_notional_usdt=None, price_from, price_to,
                            count: int = 5, leverage: int | None = None, take_profit=None, stop_loss=None) -> dict:
        """Scale in with up to N limit orders evenly spaced between two prices (batch_order, 5 per request)."""
        symbol = normalize_symbol(symbol)
        side = direction(side)
        rules = await self.symbols.get(symbol)
        p0, p1 = D(price_from), D(price_to)
        prices = [rules.round_price(p0 + (p1 - p0) * i / max(count - 1, 1)) for i in range(count)]
        avg = sum(prices) / len(prices)
        tq = D(total_qty) if total_qty is not None else D(total_notional_usdt) / avg
        each = rules.round_qty(tq / count)
        if each < rules.min_trade_volume:
            return {"ok": False, "problems": [f"each order {each} < minTradeVolume {rules.min_trade_volume}; use fewer orders or more size"]}
        await self.ensure_leverage_and_mode(symbol, leverage, None)
        orders = []
        for p in prices:
            o: dict[str, Any] = {"qty": each, "price": p, "side": "BUY" if side == "LONG" else "SELL", "tradeSide": "OPEN",
                                 "orderType": "LIMIT", "effect": "GTC"}
            if take_profit is not None:
                o.update(tpPrice=rules.round_price(take_profit), tpStopType="MARK_PRICE", tpOrderType="MARKET")
            if stop_loss is not None:
                o.update(slPrice=rules.round_price(stop_loss), slStopType="MARK_PRICE", slOrderType="MARKET")
            orders.append(o)
        results = [await self.rest.batch_order(symbol=symbol, orderList=orders[i:i + 5]) for i in range(0, len(orders), 5)]
        return {"ok": True, "prices": [str(p) for p in prices], "qty_each": str(each), "results": results}

    # ------------------------------------------------------------------ risk math
    async def estimate_liquidation(self, symbol: str, side: str, entry, leverage: int, qty) -> dict:
        """Isolated-margin estimate using the official position tiers (maintenanceMarginRate)."""
        symbol = normalize_symbol(symbol)
        tiers = await self.rest.get_position_tiers(symbol=symbol)
        entry, qty = D(entry), D(qty)
        notional = entry * qty
        mmr = D("0.005")
        for t in tiers or []:
            if D(t["startValue"]) <= notional <= D(t["endValue"]):
                mmr = D(t["maintenanceMarginRate"]); break
        im = D(1) / leverage
        liq = entry * (1 - im + mmr) if direction(side) == "LONG" else entry * (1 + im - mmr)
        return {"symbol": symbol, "estimated_liq_price": str(liq.quantize(D("0.0001"))), "maintenance_margin_rate": str(mmr),
                "notional": str(notional), "note": "Estimate for isolated margin, excluding fees and funding."}


def _split(q: Decimal, max_vol: Decimal, rules) -> list[Decimal]:
    chunks = []
    rest = q
    while rest > 0:
        c = min(rest, max_vol)
        c = rules.round_qty(c)
        if c <= 0:
            break
        chunks.append(c)
        rest -= c
    return chunks
