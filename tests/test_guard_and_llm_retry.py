import asyncio
from decimal import Decimal

import httpx
import pytest

from bitunix_agent.agent import llm as llm_mod
from bitunix_agent.agent.core import _llm_error_text
from bitunix_agent.engine.monitor import position_roi
from bitunix_agent.engine.trading import TradingEngine


def test_position_roi_matches_app():
    assert round(position_roi({"margin": "0.53", "unrealizedPNL": "0.221"}), 1) == 41.7
    assert position_roi({"margin": "0"}) is None


def test_error_texts():
    assert "rate-limiting" in _llm_error_text(RuntimeError('LLM HTTP 429: {"message":"rate limit for free users"}'))
    assert "quota is used up" in _llm_error_text(RuntimeError('LLM HTTP 429: {"code":"insufficient_quota"}'))


@pytest.mark.asyncio
async def test_llm_retries_429(monkeypatch):
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429, text="slow down", headers={"retry-after": "0"})
            return httpx.Response(200, json={"ok": 1})

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", FakeClient)
    llm_mod.LLM._sem = None
    m = llm_mod.LLM("custom", "k", "m", "https://x/v1")
    assert await m._post("https://x/v1/chat/completions", {}, {}) == {"ok": 1}
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_llm_no_retry_on_quota(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            return httpx.Response(429, text='{"code":"insufficient_quota"}')

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", FakeClient)
    llm_mod.LLM._sem = None
    m = llm_mod.LLM("custom", "k", "m", "https://x/v1")
    with pytest.raises(RuntimeError):
        await m._post("u", {}, {})


@pytest.mark.asyncio
async def test_breakeven_never_loosens():
    eng = TradingEngine.__new__(TradingEngine)
    pos = {"positionId": "1", "symbol": "PORTALUSDT", "side": "BUY", "qty": "402.5", "avgOpenPrice": "0.03895"}
    called = {}

    async def find_position(symbol, side=None, position_id=None): return pos
    async def current_stop(symbol, pid): return Decimal("0.03909")
    async def set_tpsl(**kw): called.update(kw); return {"ok": True}

    eng.find_position, eng.current_stop, eng.set_tpsl = find_position, current_stop, set_tpsl
    res = await eng.move_stop_to_breakeven("PORTALUSDT")
    assert res.get("unchanged") and not called
