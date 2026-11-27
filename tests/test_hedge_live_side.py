import asyncio

from bitunix_agent.engine.trading import TradingEngine, pos_is_long

# exact shape of a live HEDGE long from get_pending_positions (2026-11-28): side is BUY
LIVE = {"positionId": "4349998199904008138", "symbol": "PORTALUSDT", "side": "BUY", "positionMode": "HEDGE",
        "qty": "402.5", "avgOpenPrice": "0.03895", "entryValue": "15.677375"}


def test_live_buy_is_long_and_found():
    class R:
        async def get_pending_positions(self, **kw):
            return [dict(LIVE)]
    e = TradingEngine.__new__(TradingEngine)
    e.rest = R()
    assert pos_is_long(LIVE) and not pos_is_long({**LIVE, "side": "SELL"})
    assert asyncio.run(e.find_position("PORTALUSDT", "LONG"))["positionId"] == LIVE["positionId"]
    assert asyncio.run(e.find_position("PORTALUSDT", "SHORT")) is None
