import json

import httpx
import pytest

from bitunix_agent.agent.core import Agent
from bitunix_agent.config import Settings
from bitunix_agent.engine import indicators
from bitunix_agent.engine.trading import TradingEngine
from bitunix_agent.exchange.rest import BitunixREST
from bitunix_agent.exchange.ws import BitunixWS
from tests.fake_exchange import FakeExchange


def make(mode="HEDGE", **s):
    fx = FakeExchange(mode)
    rest = BitunixREST("K", "S", transport=httpx.MockTransport(fx))
    st = Settings(dry_run=False, db_path=":memory:", **s)
    return fx, rest, TradingEngine(rest, None, st)


def posts(fx, suffix):
    return [b for p, b in fx.posted if p.endswith(suffix)]


@pytest.mark.asyncio
async def test_open_long_by_margin_sets_leverage_and_rounds():
    fx, rest, eng = make()
    r = await eng.open_position(symbol="btc", side="long", margin_usdt=100, leverage=20, stop_loss=59000, take_profit=62000)
    assert r["ok"], r
    assert posts(fx, "/change_leverage")[0]["leverage"] == 20
    o = posts(fx, "/place_order")[0]
    # 100 * 20 / 60000 = 0.03333 -> basePrecision 4, round down
    assert o["qty"] == "0.0333" and o["side"] == "BUY" and o["tradeSide"] == "OPEN"
    assert o["tpStopType"] == "MARK_PRICE" and o["slPrice"] == "59000" and o["clientId"]


@pytest.mark.asyncio
async def test_bad_tpsl_direction_is_caught_before_sending():
    fx, rest, eng = make()
    r = await eng.open_position(symbol="BTCUSDT", side="SHORT", qty=0.01, take_profit=61000)
    assert not r["ok"] and "SHORT take-profit" in r["problems"][0]
    assert not posts(fx, "/place_order")


@pytest.mark.asyncio
async def test_oversize_market_order_is_split_into_batches():
    fx, rest, eng = make()
    r = await eng.open_position(symbol="BTCUSDT", side="LONG", qty=1.2)  # maxMarketOrderVolume 0.5
    assert r["ok"] and r["chunks"] == ["0.5", "0.5", "0.2"]
    assert len(posts(fx, "/batch_order")[0]["orderList"]) == 3


@pytest.mark.asyncio
async def test_hedge_partial_close_uses_documented_params():
    fx, rest, eng = make("HEDGE")
    fx.positions = [{"positionId": "p1", "symbol": "BTCUSDT", "qty": "0.1", "side": "LONG", "positionMode": "HEDGE"}]
    r = await eng.close_position(symbol="BTCUSDT", percent=50)
    o = posts(fx, "/place_order")[0]
    assert o == {**o, "side": "BUY", "tradeSide": "CLOSE", "positionId": "p1", "qty": "0.05"}


@pytest.mark.asyncio
async def test_one_way_partial_close_reduce_only_with_fallback():
    fx, rest, eng = make("ONE_WAY")
    fx.reject_first_close = True
    fx.positions = [{"positionId": "p1", "symbol": "BTCUSDT", "qty": "0.1", "side": "LONG", "positionMode": "ONE_WAY"}]
    r = await eng.close_position(symbol="BTCUSDT", percent=30)
    assert r["ok"]
    first, second = posts(fx, "/place_order")
    assert first["side"] == "SELL" and first["reduceOnly"] is True
    assert second["tradeSide"] == "CLOSE" and second["positionId"] == "p1"


@pytest.mark.asyncio
async def test_full_close_uses_flash_close():
    fx, rest, eng = make()
    fx.positions = [{"positionId": "p9", "symbol": "BTCUSDT", "qty": "0.1", "side": "SHORT", "positionMode": "HEDGE"}]
    r = await eng.close_position(symbol="BTCUSDT")
    assert r["method"] == "flash_close_position" and posts(fx, "/flash_close_position")[0] == {"positionId": "p9"}


@pytest.mark.asyncio
async def test_position_tpsl_vs_partial_tpsl():
    fx, rest, eng = make()
    fx.positions = [{"positionId": "p1", "symbol": "BTCUSDT", "qty": "0.1", "side": "LONG", "positionMode": "HEDGE", "liqPrice": "40000"}]
    r = await eng.set_tpsl(symbol="BTCUSDT", take_profit=65000, stop_loss=58000)
    assert r["method"] == "place_position_tpsl_order"
    r = await eng.set_tpsl(symbol="BTCUSDT", take_profit=65000, percent=50)
    assert r["method"] == "place_tpsl_order"
    b = posts(fx, "/tpsl/place_order")[0]
    assert b["tpQty"] == "0.05" and b["tpOrderType"] == "MARKET"
    bad = await eng.set_tpsl(symbol="BTCUSDT", stop_loss=39000)
    assert not bad["ok"] and "liquidation" in bad["problems"][0]


@pytest.mark.asyncio
async def test_risk_guard_returns_adjusted_plan():
    fx, rest, eng = make(max_leverage=20)
    r = await eng.open_position(symbol="BTCUSDT", side="LONG", qty=0.01, leverage=50)
    assert not r["ok"] and r["stage"] == "risk" and r["adjusted_plan"]["leverage"] == 20


def test_indicators_basic():
    candles = [{"open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100.5 + i, "baseVol": 10} for i in range(120)]
    s = indicators.summarize(candles)
    assert s["trend"] == "up" and s["rsi14"] > 70 and s["atr14"] > 0


# ---------------------------------------------------------------------- agent loop with a scripted LLM
class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.model = "scripted"

    async def chat(self, system, messages, tools):
        assert "NEVER SAY" in system and len(tools) > 70
        step = self.script.pop(0)
        if isinstance(step, str):
            return {"content": step, "tool_calls": [], "raw_assistant": {"role": "assistant", "content": step}}
        name, args = step
        if callable(args):
            args = args(messages)
        call = {"id": f"c{len(self.script)}", "name": name, "arguments": args}
        return {"content": "", "tool_calls": [call], "raw_assistant": {"role": "assistant", "content": "", "tool_calls": [
            {"id": call["id"], "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}


@pytest.mark.asyncio
async def test_agent_confirmation_flow_end_to_end():
    fx = FakeExchange("HEDGE")
    st = Settings(dry_run=False, db_path=":memory:", confirm_trades="always", llm_api_key="", bitunix_api_key="K", bitunix_secret_key="S")
    agent = Agent(st)
    agent.rest._http = httpx.AsyncClient(transport=httpx.MockTransport(fx))
    def last_cid(messages):
        return {"confirmation_id": json.loads(messages[-1]["content"])["confirmation_id"]}
    agent.llm = ScriptedLLM([("open_position", {"symbol": "BTCUSDT", "side": "LONG", "qty": 0.01}), "Confirm long 0.01 BTC?"])
    r1 = await agent.chat("t", "long 0.01 btc")
    assert "Confirm" in r1 and not posts(fx, "/place_order")
    pend = agent.db.pending_confirmations("t")
    assert len(pend) == 1
    agent.llm = ScriptedLLM([("confirm_action", {"confirmation_id": pend[0]["id"]}), "Done, order accepted."])
    r2 = await agent.chat("t", "yes")
    assert posts(fx, "/place_order")[0]["qty"] == "0.01" and "accepted" in r2
    assert agent.db.journal_rows()[0]["ok"] == 1
    await agent.stop()


@pytest.mark.asyncio
async def test_agent_tool_error_is_fed_back_not_raised():
    st = Settings(dry_run=False, db_path=":memory:", llm_api_key="")
    agent = Agent(st)
    agent.rest._http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"code": 20001, "msg": "Market not exists"})))
    seen = {}
    def grab(messages):
        seen["err"] = json.loads(messages[-1]["content"])
        return {"name": "20001"}
    agent.llm = ScriptedLLM([("api_get_funding_rate", {"symbol": "NOPEUSDT"}), ("api_docs", grab), "Symbol not listed; try get_trading_pairs."])
    await agent.chat("t2", "funding for NOPE")
    assert seen["err"]["code"] == 20001 and seen["err"]["hint"]
    await agent.stop()


def test_ws_dispatch_official_samples():
    import asyncio
    ws = BitunixWS()
    async def run():
        class C:
            private = False
            logged_in = asyncio.Event()
        await ws._dispatch(json.dumps({"ch": "tickers", "ts": 1, "data": [{"s": "BTCUSDT", "la": "68650.9", "o": "69141.6", "h": "70319.9", "l": "68241.9",
                                                                       "b": "1", "q": "2", "r": "-0.7", "bd": "68650.9", "ak": "68651", "bv": "1", "av": "2"}]}), C)
        await ws._dispatch(json.dumps({"ch": "price", "symbol": "BTCUSDT", "ts": 1, "data": {"ip": "1", "mp": "10000", "fr": "0.01", "ft": "x", "nft": "y"}}), C)
        await ws._dispatch(json.dumps({"ch": "depth_books", "symbol": "BTCUSDT", "ts": 1, "data": {"b": [["100", "1"], ["99", "2"]], "a": [["101", "1"]]}}), C)
        await ws._dispatch(json.dumps({"ch": "depth_books", "symbol": "BTCUSDT", "ts": 2, "data": {"b": [["100", "0"]], "a": []}}), C)
        C.private = True
        await ws._dispatch(json.dumps({"ch": "tpsl", "ts": 1, "data": {"event": "CREATE", "status": "NEW", "orderId": "t", "positionId": "p"}}), C)
        await ws._dispatch(json.dumps({"ch": "position", "ts": 1, "data": {"event": "OPEN", "positionId": "p", "qty": "1"}}), C)
    asyncio.run(run())
    assert ws.best_bid_ask("BTCUSDT") == ("68650.9", "68651")
    assert ws.prices["BTCUSDT"]["mp"] == "10000"
    assert ws.book_snapshot("BTCUSDT")["bids"] == [("99", "2")]
    assert ws.tpsl["t"]["meaning"] == "TP/SL order became effective" and "p" in ws.positions
