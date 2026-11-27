"""Trading-pair rules cache (get_trading_pairs) and precision/limit helpers so orders are valid before sending."""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Any


def D(x: Any) -> Decimal:
    return Decimal(str(x))


@dataclass
class SymbolRules:
    symbol: str
    base: str
    quote: str
    min_trade_volume: Decimal
    min_buy_price_offset: Decimal
    max_sell_price_offset: Decimal
    max_limit_order_volume: Decimal
    max_market_order_volume: Decimal
    base_precision: int
    quote_precision: int
    max_leverage: int
    min_leverage: int
    default_leverage: int
    default_margin_mode: str
    price_protect_scope: Decimal
    symbol_status: str
    is_api_supported: bool
    max_funding_rate: Decimal
    min_funding_rate: Decimal
    launch_time: int | None
    delist_time: int | None
    raw: dict

    @classmethod
    def from_api(cls, d: dict) -> "SymbolRules":
        def opt_int(v):
            try:
                return int(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None
        return cls(
            symbol=d["symbol"], base=d.get("base", ""), quote=d.get("quote", ""),
            min_trade_volume=D(d.get("minTradeVolume", "0")),
            min_buy_price_offset=D(d.get("minBuyPriceOffset", "0")),
            max_sell_price_offset=D(d.get("maxSellPriceOffset", "0")),
            max_limit_order_volume=D(d.get("maxLimitOrderVolume", "0")),
            max_market_order_volume=D(d.get("maxMarketOrderVolume", "0")),
            base_precision=int(d.get("basePrecision", 8)), quote_precision=int(d.get("quotePrecision", 8)),
            max_leverage=int(d.get("maxLeverage", 1)), min_leverage=int(d.get("minLeverage", 1)),
            default_leverage=int(d.get("defaultLeverage", 1)), default_margin_mode=str(d.get("defaultMarginMode", "")),
            price_protect_scope=D(d.get("priceProtectScope", "0")), symbol_status=d.get("symbolStatus", "OPEN"),
            is_api_supported=bool(d.get("isApiSupported", True)),
            max_funding_rate=D(d.get("maxFundingRate", "0")), min_funding_rate=D(d.get("minFundingRate", "0")),
            launch_time=opt_int(d.get("launchTime")), delist_time=opt_int(d.get("delistTime")), raw=d,
        )

    @property
    def qty_step(self) -> Decimal:
        return Decimal(1).scaleb(-self.base_precision)

    @property
    def price_step(self) -> Decimal:
        return Decimal(1).scaleb(-self.quote_precision)

    def round_qty(self, qty: Any, mode: str = "down") -> Decimal:
        r = {"down": ROUND_DOWN, "up": ROUND_UP, "nearest": ROUND_HALF_UP}[mode]
        return D(qty).quantize(self.qty_step, rounding=r)

    def round_price(self, price: Any, mode: str = "nearest") -> Decimal:
        r = {"down": ROUND_DOWN, "up": ROUND_UP, "nearest": ROUND_HALF_UP}[mode]
        return D(price).quantize(self.price_step, rounding=r)

    def price_band(self, mark_price: Any) -> tuple[Decimal, Decimal]:
        """(min sell price, max buy price) from priceProtectScope, per the official example."""
        m = D(mark_price)
        return m * (1 - self.price_protect_scope), m * (1 + self.price_protect_scope)

    def check_order(self, qty: Any, price: Any | None, order_type: str, side: str, mark_price: Any | None) -> list[str]:
        """Pre-flight checks mirroring error codes 20008/20012/20015/30013/30014/30015/30017."""
        problems = []
        q = D(qty)
        if self.symbol_status != "OPEN":
            problems.append(f"{self.symbol} status is {self.symbol_status} (20012)")
        if not self.is_api_supported:
            problems.append(f"{self.symbol} has API trading disabled (20015)")
        if q < self.min_trade_volume:
            problems.append(f"qty {q} < minTradeVolume {self.min_trade_volume} (30017)")
        if q != self.round_qty(q):
            problems.append(f"qty {q} has more than basePrecision={self.base_precision} decimals")
        mx = self.max_limit_order_volume if order_type == "LIMIT" else self.max_market_order_volume
        if mx and q > mx:
            problems.append(f"qty {q} > max {order_type} order volume {mx} (30013/30039): split it")
        if price is not None:
            p = D(price)
            if p != self.round_price(p):
                problems.append(f"price {p} has more than quotePrecision={self.quote_precision} decimals")
            if mark_price is not None and self.price_protect_scope > 0 and order_type == "LIMIT":
                lo, hi = self.price_band(mark_price)
                # protect band only bites for marketable prices; a resting buy below mark / sell above mark is fine
                if side == "BUY" and p > hi:
                    problems.append(f"buy price {p} > max buy price {hi:.{self.quote_precision}f} (30014)")
                if side == "SELL" and p < lo:
                    problems.append(f"sell price {p} < min sell price {lo:.{self.quote_precision}f} (30015)")
        return problems


class SymbolCache:
    def __init__(self, rest, ttl_sec: int = 600):
        self.rest = rest
        self.ttl = ttl_sec
        self._rules: dict[str, SymbolRules] = {}
        self._loaded = 0.0

    async def refresh(self) -> None:
        data = await self.rest.get_trading_pairs()
        self._rules = {d["symbol"]: SymbolRules.from_api(d) for d in data or []}
        self._loaded = time.time()

    async def get(self, symbol: str) -> SymbolRules:
        symbol = normalize_symbol(symbol)
        if not self._rules or time.time() - self._loaded > self.ttl:
            await self.refresh()
        if symbol not in self._rules:
            await self.refresh()
        if symbol not in self._rules:
            close = [s for s in self._rules if symbol.replace("USDT", "") in s][:8]
            raise KeyError(f"symbol {symbol} not found on Bitunix futures. Similar: {close}")
        return self._rules[symbol]

    async def all(self) -> dict[str, SymbolRules]:
        if not self._rules or time.time() - self._loaded > self.ttl:
            await self.refresh()
        return self._rules


def normalize_symbol(s: str) -> str:
    s = s.strip().upper().replace("/", "").replace("-", "").replace("_", "").replace(":USDT", "")
    if s.endswith("PERP"):
        s = s[:-4]
    if not s.endswith(("USDT", "USDC", "USD")):
        s += "USDT"
    return s
