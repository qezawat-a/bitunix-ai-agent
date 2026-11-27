import json
from types import SimpleNamespace as NS

import pytest

from bitunix_agent.agent.llm import LLM

TOOLS = [{"name": "market_snapshot", "description": "d", "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}}}]
HIST = [
    {"role": "user", "content": "btc?"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "a1", "type": "function", "function": {"name": "market_snapshot", "arguments": "{\"symbol\":\"BTC\"}"}}]},
    {"role": "tool", "tool_call_id": "a1", "content": "{\"last\":\"1\"}"},
]


@pytest.mark.asyncio
async def test_openai_adapter():
    llm = LLM("deepseek", "x")
    seen = {}
    async def create(**kw):
        seen.update(kw)
        tc = NS(id="c2", function=NS(name="market_snapshot", arguments="{\"symbol\":\"ETH\"}"))
        return NS(choices=[NS(message=NS(content=None, tool_calls=[tc]))])
    llm.client = NS(chat=NS(completions=NS(create=create)))
    out = await llm.chat("sys", HIST, TOOLS)
    assert seen["messages"][0] == {"role": "system", "content": "sys"} and seen["tools"][0]["type"] == "function"
    assert out["tool_calls"] == [{"id": "c2", "name": "market_snapshot", "arguments": {"symbol": "ETH"}}]
    assert llm.model == "deepseek-chat"


@pytest.mark.asyncio
async def test_anthropic_adapter():
    llm = LLM("anthropic", "x", "claude-x")
    seen = {}
    async def create(**kw):
        seen.update(kw)
        return NS(content=[NS(type="text", text="ok"), NS(type="tool_use", id="t9", name="market_snapshot", input={"symbol": "SOL"})])
    llm.client = NS(messages=NS(create=create))
    out = await llm.chat("sys", HIST, TOOLS)
    m = seen["messages"]
    assert m[1]["content"][0]["type"] == "tool_use" and m[2]["content"][0]["type"] == "tool_result"
    assert seen["tools"][0]["input_schema"]["type"] == "object" and seen["system"] == "sys"
    assert out["tool_calls"][0]["arguments"] == {"symbol": "SOL"} and json.loads(out["raw_assistant"]["tool_calls"][0]["function"]["arguments"])


@pytest.mark.asyncio
async def test_http_transport_openai_no_sdk():
    llm = LLM("openrouter", "k")
    seen = {}
    async def post(url, headers, body):
        seen.update(url=url, headers=headers, body=body)
        return {"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "z1", "type": "function", "function": {"name": "market_snapshot", "arguments": "{\"symbol\":\"BTC\"}"}}]}}]}
    llm._post = post
    out = await llm.chat("sys", HIST, TOOLS)
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions" and seen["headers"]["Authorization"] == "Bearer k"
    assert out["tool_calls"][0]["arguments"] == {"symbol": "BTC"}


@pytest.mark.asyncio
async def test_http_transport_anthropic_no_sdk():
    llm = LLM("anthropic", "k", "claude-x")
    seen = {}
    async def post(url, headers, body):
        seen.update(url=url, headers=headers, body=body)
        return {"content": [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "t1", "name": "market_snapshot", "input": {"symbol": "ETH"}}]}
    llm._post = post
    out = await llm.chat("sys", HIST, TOOLS)
    assert seen["url"] == "https://api.anthropic.com/v1/messages" and seen["headers"]["x-api-key"] == "k"
    assert out["content"] == "hi" and out["tool_calls"][0]["arguments"] == {"symbol": "ETH"}


def test_config_forgiving(monkeypatch):
    import importlib, bitunix_agent.config as cfg
    monkeypatch.setenv("CONFIRM_ABOVE_NOTIONAL", "never")
    monkeypatch.setenv("RISK_MAX_LEVERAGE", "20x")
    monkeypatch.setenv("RISK_DAILY_LOSS_LIMIT", "1,000 # usdt")
    monkeypatch.setenv("LLM_TEMPERATURE", "abc")
    s = cfg.Settings()
    assert s.confirm_above_notional == float("inf") and s.max_leverage == 20 and s.daily_loss_limit == 1000 and s.llm_temperature == 0.2
