import asyncio

from bitunix_agent.engine.trading import TradingEngine
from bitunix_agent.exchange.rest import normalize

LIVE = {"positionId": "738", "symbol": "PORTALUSDT", "qty": "292.6", "entryPrice": "0.03862", "side": "BUY",
        "positionMode": "HEDGE", "marginMode": "CROSS", "leverage": "30"}


def test_live_buy_sell_side_becomes_long_short():
    rows = normalize("get_pending_positions", [dict(LIVE), {**LIVE, "positionId": "739", "side": "SELL"}])
    assert [r["side"] for r in rows] == ["LONG", "SHORT"] and rows[0]["sideRaw"] == "BUY"
    assert rows[0]["avgOpenPrice"] == "0.03862"
    hist = normalize("get_history_positions", {"total": "1", "positionList": [dict(LIVE)]})
    assert hist["positionList"][0]["side"] == "LONG"


def test_find_position_in_hedge_with_live_side():
    class R:
        async def get_pending_positions(self, **kw):
            return normalize("get_pending_positions", [dict(LIVE)])
    e = TradingEngine.__new__(TradingEngine)
    e.rest = R()
    pos = asyncio.run(e.find_position("PORTALUSDT", "LONG"))
    assert pos and pos["positionId"] == "738"
