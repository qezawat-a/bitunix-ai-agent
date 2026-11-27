"""Hits the real public Bitunix API (no keys). Run: pytest -m live"""
import pytest

from bitunix_agent.config import Settings
from bitunix_agent.engine.trading import TradingEngine
from bitunix_agent.exchange.rest import BitunixREST

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_every_public_endpoint_live():
    async with BitunixREST() as c:
        assert (await c.get_trading_pairs(symbols="BTCUSDT"))[0]["symbol"] == "BTCUSDT"
        assert len(await c.get_tickers(symbols="BTCUSDT,ETHUSDT")) == 2
        k = await c.get_kline(symbol="BTCUSDT", interval="1h", limit=5, type="MARK_PRICE")
        assert len(k) == 5 and k[0]["time"] < k[-1]["time"]
        assert (await c.get_depth(symbol="BTCUSDT", limit="5"))["bids"]
        assert (await c.get_funding_rate(symbol="BTCUSDT"))["symbol"] == "BTCUSDT"
        assert await c.get_funding_rate_batch()
        assert await c.get_funding_rate_history(symbol="BTCUSDT", limit=3)
        assert await c.get_position_tiers(symbol="BTCUSDT")


@pytest.mark.asyncio
async def test_engine_analysis_live():
    rest = BitunixREST()
    eng = TradingEngine(rest, None, Settings(db_path=":memory:"))
    snap = await eng.market_snapshot("btc")
    assert snap["rules"]["basePrecision"] >= 0
    ta = await eng.technical_analysis("ETHUSDT", "4h")
    assert ta["rsi14"] is not None
    assert len(await eng.scan_market(top=5)) == 5
    await rest.close()
