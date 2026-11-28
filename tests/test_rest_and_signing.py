import hashlib
import json

import httpx
import pytest

from bitunix_agent.exchange.errors import BitunixAPIError, ValidationError
from bitunix_agent.exchange.rest import BitunixREST
from bitunix_agent.exchange.signing import compact_json, rest_sign, sorted_query_for_sign, ws_login_args
from bitunix_agent.exchange.spec_generated import SPEC
from bitunix_agent.exchange.validation import validate


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


def test_rest_sign_matches_official_example():
    # values from common/sign official Python example
    nonce, ts, key, secret = "123456", "20241120123045", "yourApiKey", "yourSecretKey"
    q = "id1uid200"
    body = '{"uid":"2899","arr":[{"id":1,"name":"maple"},{"id":2,"name":"lily"}]}'
    expect = h(h(nonce + ts + key + q + body) + secret)
    assert rest_sign(nonce, ts, key, q, body, secret) == expect
    assert sorted_query_for_sign({"uid": "200", "id": "1"}) == "id1uid200"
    assert compact_json({"uid": "2899", "arr": [{"id": 1, "name": "maple"}, {"id": 2, "name": "lily"}]}) == body


def test_ws_login_sign():
    a = ws_login_args("k", "s")
    assert isinstance(a["timestamp"], int) and a["timestamp"] < 10**11  # seconds
    assert a["sign"] == h(h(a["nonce"] + str(a["timestamp"]) + "k") + "s")
    assert len(a["nonce"]) < 64


class Recorder:
    def __init__(self, data=None):
        self.reqs = []
        self.data = data

    def __call__(self, request: httpx.Request):
        self.reqs.append(request)
        return httpx.Response(200, json={"code": 0, "data": self.data if self.data is not None else {}, "msg": "Success"},
                              headers={"date": "Thu, 26 Nov 2026 11:00:00 GMT"})


def verify_signature(req: httpx.Request, secret="SECRET"):
    q = dict(httpx.QueryParams(req.url.query))
    body = req.content.decode()
    expect = h(h(req.headers["nonce"] + req.headers["timestamp"] + req.headers["api-key"] + sorted_query_for_sign(q) + body) + secret)
    assert req.headers["sign"] == expect
    assert " " not in body
    assert len(req.headers["nonce"]) < 64
    assert req.headers["Content-Type"] == "application/json"


def sample(p):
    if p.get("enum"):
        return p["enum"][0]
    return {"string": "1", "int": 1, "bool": True}.get(p["type"], "1")


def minimal_args(key):
    ep = SPEC[key]
    args = {}
    for p in ep["params"]:
        if p["required"]:
            if p["type"] == "list":
                args[p["name"]] = [{c["name"]: sample(c) for c in p["children"] if c["required"]}]
            else:
                args[p["name"]] = sample(p)
    if key in ("place_order",):
        args.update(orderType="MARKET", symbol="BTCUSDT", tradeSide="OPEN")  # HEDGE mode: tradeSide required
    if key == "batch_order":
        args["orderList"][0].update(orderType="MARKET", tradeSide="OPEN")
    if key in ("modify_order", "get_order_detail"):
        args["orderId"] = "1"
    if key == "cancel_orders":
        args["orderList"] = [{"orderId": "1"}]
    if key == "adjust_position_margin":
        args["side"] = "LONG"
    if key in ("place_tpsl_order", "modify_tpsl_order"):
        args.update(tpPrice="1", tpQty="1")
    if key in ("place_position_tpsl_order", "modify_position_tpsl_order"):
        args["tpPrice"] = "1"
    return args


@pytest.mark.parametrize("key", list(SPEC))
@pytest.mark.asyncio
async def test_every_endpoint_builds_a_correct_signed_request(key):
    rec = Recorder()
    c = BitunixREST("KEY", "SECRET", transport=httpx.MockTransport(rec))
    await getattr(c, key)(**minimal_args(key))
    r = rec.reqs[0]
    ep = SPEC[key]
    assert r.method == ep["method"] and r.url.path == ep["path"]
    if ep["public"]:
        assert "sign" not in r.headers
    else:
        verify_signature(r)
    if ep["method"] == "POST":
        assert isinstance(json.loads(r.content), dict)  # body is JSON
    await c.close()


@pytest.mark.asyncio
async def test_full_place_order_all_params_and_aliases():
    rec = Recorder({"orderId": "1", "clientId": "c"})
    c = BitunixREST("KEY", "SECRET", transport=httpx.MockTransport(rec))
    await c.place_order(symbol="btc/usdt", qty=0.0010, price=60000.10, side="buy", tradeSide="open", positionId="9",
                        orderType="limit", effect="post_only", clientId="abc", reduceOnly=False, tpPrice=61000,
                        tpStopType="MARK", tpOrderType="LIMIT", tpOrderPrice="61000.1", slPrice=59000, slStopType="LAST",
                        slOrderType="MARKET", slOrderPrice=None)
    body = json.loads(rec.reqs[0].content)
    assert body == {"symbol": "BTCUSDT", "qty": "0.001", "price": "60000.1", "side": "BUY", "tradeSide": "OPEN", "positionId": "9",
                    "orderType": "LIMIT", "effect": "POST_ONLY", "clientId": "abc", "reduceOnly": False, "tpPrice": "61000",
                    "tpStopType": "MARK_PRICE", "tpOrderType": "LIMIT", "tpOrderPrice": "61000.1", "slPrice": "59000",
                    "slStopType": "LAST_PRICE", "slOrderType": "MARKET"}
    verify_signature(rec.reqs[0])
    await c.close()


@pytest.mark.asyncio
async def test_get_query_sorted_and_bool_lowercase():
    rec = Recorder([])
    c = BitunixREST("KEY", "SECRET", transport=httpx.MockTransport(rec))
    await c.get_pending_positions(symbol="ETHUSDT", includeSubAccounts=True, subAccountId=5)
    r = rec.reqs[0]
    assert r.url.query.decode() == "includeSubAccounts=true&subAccountId=5&symbol=ETHUSDT"
    verify_signature(r)
    await c.close()


@pytest.mark.asyncio
async def test_error_codes_raise_with_hint_and_retry():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"code": 10006, "msg": "Request too frequently"})
        return httpx.Response(200, json={"code": 20003, "msg": "Insufficient balance"})
    c = BitunixREST("KEY", "SECRET", transport=httpx.MockTransport(handler))
    with pytest.raises(BitunixAPIError) as e:
        await c.place_order(symbol="BTCUSDT", qty="1", side="BUY", orderType="MARKET", tradeSide="OPEN")
    assert e.value.code == 20003 and "balance" in e.value.hint and calls["n"] == 2
    await c.close()


def test_validation_rules():
    with pytest.raises(ValidationError, match="price is required"):
        validate("place_order", {"symbol": "BTCUSDT", "qty": "1", "side": "BUY", "orderType": "LIMIT"})
    with pytest.raises(ValidationError, match="tradeSide is required in HEDGE"):
        validate("place_order", {"symbol": "BTCUSDT", "qty": "1", "side": "BUY", "orderType": "MARKET"}, position_mode="HEDGE")
    with pytest.raises(ValidationError, match="positionId is required"):
        validate("place_order", {"symbol": "BTCUSDT", "qty": "1", "side": "BUY", "orderType": "MARKET", "tradeSide": "CLOSE"})
    with pytest.raises(ValidationError, match="maximum is 5"):
        validate("batch_order", {"symbol": "BTCUSDT", "orderList": [{"qty": "1", "side": "BUY", "orderType": "MARKET"}] * 6})
    with pytest.raises(ValidationError, match="orderList\\[0\\].price"):
        validate("batch_order", {"symbol": "BTCUSDT", "orderList": [{"qty": "1", "side": "BUY", "orderType": "LIMIT"}]})
    with pytest.raises(ValidationError, match="one of"):
        validate("cancel_orders", {"symbol": "BTCUSDT", "orderList": [{}]})
    with pytest.raises(ValidationError, match="at least one of"):
        validate("place_position_tpsl_order", {"symbol": "BTCUSDT", "positionId": "1"})
    with pytest.raises(ValidationError, match="not allowed"):
        validate("get_kline", {"symbol": "BTCUSDT", "interval": "2m"})
    with pytest.raises(ValidationError, match="unknown parameter"):
        validate("get_kline", {"symbol": "BTCUSDT", "interval": "1m", "startTime": 1})
    with pytest.raises(ValidationError, match="exceeds maximum 200"):
        validate("get_kline", {"symbol": "BTCUSDT", "interval": "1m", "limit": 500})
    assert validate("change_margin_mode", {"marginMode": "isolated", "symbol": "btcusdt", "marginCoin": "usdt"}) == \
        {"marginMode": "ISOLATION", "symbol": "BTCUSDT", "marginCoin": "USDT"}
    assert validate("adjust_position_margin", {"symbol": "BTCUSDT", "marginCoin": "USDT", "amount": -100, "side": "long"})["amount"] == "-100"


@pytest.mark.asyncio
async def test_dry_run_never_sends():
    rec = Recorder()
    c = BitunixREST("KEY", "SECRET", transport=httpx.MockTransport(rec), dry_run=True)
    r = await c.flash_close_position(positionId="1")
    assert r["dry_run"] and not rec.reqs
    await c.close()


@pytest.mark.asyncio
async def test_kline_normalized_oldest_first():
    rec = Recorder([{"time": "200", "open": "1", "high": "1", "low": "1", "close": "1"}, {"time": "100", "open": "1", "high": "1", "low": "1", "close": "1"}])
    c = BitunixREST(transport=httpx.MockTransport(rec))
    k = await c.get_kline(symbol="BTCUSDT", interval="1m")
    assert [x["time"] for x in k] == [100, 200]
    await c.close()


def test_kline_3m_allowed():
    # 3m is served live (2026-11-28) and listed in the WS kline docs as 3min; added at the user's request.
    validate("get_kline", {"symbol": "BTCUSDT", "interval": "3m"})
    from bitunix_agent.exchange.constants import ws_kline_channel
    assert ws_kline_channel("3m") == "market_kline_3min"
