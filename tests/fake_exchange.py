"""In-memory fake of the Bitunix futures API (shapes taken from the official response examples + live API)."""
import json

import httpx

PAIR = {"symbol": "BTCUSDT", "base": "BTC", "quote": "USDT", "minTradeVolume": "0.0001", "minBuyPriceOffset": "-0.8",
        "maxSellPriceOffset": "2", "maxLimitOrderVolume": "1200", "maxMarketOrderVolume": "0.5", "basePrecision": 4,
        "quotePrecision": 1, "maxLeverage": 200, "minLeverage": 1, "defaultLeverage": 10, "defaultMarginMode": "2",
        "priceProtectScope": "0.01", "symbolStatus": "OPEN", "isApiSupported": True, "maxFundingRate": "0.3", "minFundingRate": "-0.3"}


class FakeExchange:
    def __init__(self, position_mode="HEDGE", mark="60000"):
        self.mode = position_mode
        self.mark = mark
        self.leverage = 10
        self.margin_mode = "CROSS"
        self.positions = []
        self.posted = []   # (path, body)
        self.tpsl = []
        self.reject_first_close = None

    def ok(self, data):
        return httpx.Response(200, json={"code": 0, "data": data, "msg": "Success"})

    def __call__(self, req: httpx.Request):
        p = req.url.path
        body = json.loads(req.content) if req.content else {}
        if req.method == "POST":
            self.posted.append((p, body))
        if p.endswith("/market/trading_pairs"):
            return self.ok([PAIR])
        if p.endswith("/market/funding_rate"):
            return self.ok({"symbol": "BTCUSDT", "markPrice": self.mark, "lastPrice": self.mark, "indexPrice": self.mark,
                            "fundingRate": "0.01", "fundingInterval": 8, "nextFundingTime": "1"})
        if p.endswith("/market/tickers"):
            return self.ok([{"symbol": "BTCUSDT", "markPrice": self.mark, "lastPrice": self.mark, "open": "59000", "last": self.mark,
                             "quoteVol": "1", "baseVol": "1", "high": "61000", "low": "58000"}])
        if p.endswith("/market/depth"):
            return self.ok({"asks": [["60000.1", "1"]], "bids": [["60000", "2"]]})
        if p.endswith("/position/get_position_tiers"):
            return self.ok([{"symbol": "BTCUSDT", "level": 1, "startValue": "0", "endValue": "100000", "leverage": 200, "maintenanceMarginRate": "0.003"}])
        if p == "/api/v1/futures/account":
            return self.ok([{"marginCoin": "USDT", "available": "1000", "frozen": "0", "margin": "0", "transfer": "1000",
                             "positionMode": self.mode, "crossUnrealizedPNL": "0", "isolationUnrealizedPNL": "0", "bonus": "0"}])
        if p.endswith("/account/get_leverage_margin_mode"):
            return self.ok({"symbol": "BTCUSDT", "marginCoin": "USDT", "leverage": self.leverage, "marginMode": self.margin_mode})
        if p.endswith("/account/change_leverage"):
            self.leverage = body["leverage"]
            return self.ok([body])
        if p.endswith("/account/change_margin_mode"):
            self.margin_mode = body["marginMode"]
            return self.ok(body)
        if p.endswith("/position/get_pending_positions"):
            return self.ok(self.positions)
        if p.endswith("/trade/place_order"):
            if self.reject_first_close and body.get("tradeSide") == "CLOSE" or (self.reject_first_close and body.get("reduceOnly")):
                self.reject_first_close = None
                return httpx.Response(200, json={"code": 10002, "msg": "Parameter Error"})
            return self.ok({"orderId": "o1", "clientId": body.get("clientId")})
        if p.endswith("/trade/batch_order"):
            return self.ok({"successList": [{"id": str(i), "clientId": o.get("clientId")} for i, o in enumerate(body["orderList"])], "failureList": []})
        if p.endswith("/trade/flash_close_position"):
            return self.ok({"positionId": body["positionId"]})
        if p.endswith("/tpsl/get_pending_orders"):
            return self.ok(self.tpsl)
        if p.endswith("/tpsl/position/place_order") or p.endswith("/tpsl/position/modify_order") or p.endswith("/tpsl/place_order"):
            return self.ok({"orderId": "t1"})
        if p.endswith("/trade/get_pending_orders"):
            return self.ok({"orderList": [], "total": 0})
        return self.ok({})
