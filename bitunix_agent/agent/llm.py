"""LLM adapters with tool calling. Internal format = OpenAI chat messages.

No vendor SDK needed: talks to the APIs over plain httpx (works on Termux/Android, no Rust packages).
Providers: openai, deepseek, openrouter, dashscope (Alibaba Model Studio / Qwen), anthropic, custom (any OpenAI-compatible URL).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from types import SimpleNamespace as _NS
from typing import Any

import httpx

log = logging.getLogger("bitunix.llm")

PROVIDERS = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
    "dashscope": {"base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "anthropic": {"base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-5"},
    "custom": {"base_url": None, "model": ""},
}


class LLM:
    def __init__(self, provider: str, api_key: str, model: str = "", base_url: str | None = None, temperature: float = 0.2):
        provider = provider.lower()
        if provider not in PROVIDERS:
            raise ValueError(f"LLM_PROVIDER must be one of {list(PROVIDERS)}")
        self.provider = provider
        self.model = model or PROVIDERS[provider]["model"]
        self.temperature = temperature
        base_url = (base_url or PROVIDERS[provider]["base_url"] or "").rstrip("/")
        if not base_url:
            raise ValueError("LLM_BASE_URL is required for provider 'custom' (any OpenAI-compatible URL)")
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = 120.0
        self.client = _HttpAnthropic(self) if provider == "anthropic" else _HttpOpenAI(self)

    # One LLM request at a time across chats and scheduled tasks (LLM_MAX_CONCURRENCY, default 1),
    # so parallel tasks don't burst past a provider's per-minute limit.
    _sem: asyncio.Semaphore | None = None

    @classmethod
    def _semaphore(cls) -> asyncio.Semaphore:
        if cls._sem is None:
            try:
                n = max(1, int(float(os.getenv("LLM_MAX_CONCURRENCY", "1"))))
            except ValueError:
                n = 1
            cls._sem = asyncio.Semaphore(n)
        return cls._sem

    async def _post(self, url: str, headers: dict, body: dict) -> dict:
        """POST with retry: a 429 rate limit or 5xx waits (Retry-After, else 5s, 10s, 20s) and tries again.
        A used-up quota (insufficient_quota) is not retried: waiting won't fix it."""
        try:
            retries = max(0, int(float(os.getenv("LLM_RETRIES", "3"))))
        except ValueError:
            retries = 3
        for attempt in range(retries + 1):
            async with self._semaphore():
                async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as c:
                    r = await c.post(url, headers=headers, json=body)
            if r.status_code < 400:
                return r.json()
            retryable = r.status_code == 429 or r.status_code >= 500
            if not retryable or attempt == retries or "insufficient_quota" in r.text:
                raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:500]}")
            try:
                wait = min(60.0, float(r.headers.get("retry-after", "")))
            except ValueError:
                wait = 5.0 * (2 ** attempt)
            log.warning("LLM HTTP %s, retry %d/%d in %.0fs", r.status_code, attempt + 1, retries, wait)
            await asyncio.sleep(wait)
        raise RuntimeError("LLM request failed")

    async def chat(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        """Returns {'content': str, 'tool_calls': [{'id','name','arguments'}], 'raw_assistant': openai-format message}"""
        if self.provider == "anthropic":
            return await self._anthropic(system, messages, tools)
        return await self._openai(system, messages, tools)

    # ------------------------------------------------------------------ OpenAI-compatible
    async def _openai(self, system, messages, tools):
        kw: dict[str, Any] = {"model": self.model, "messages": [{"role": "system", "content": system}] + messages,
                              "temperature": self.temperature}
        if tools:
            kw["tools"] = [{"type": "function", "function": t} for t in tools]
            kw["tool_choice"] = "auto"
        r = await self.client.chat.completions.create(**kw)
        m = r.choices[0].message
        calls = []
        for tc in m.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
        raw = {"role": "assistant", "content": m.content or ""}
        if m.tool_calls:
            raw["tool_calls"] = [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                                 for tc in m.tool_calls]
        return {"content": m.content or "", "tool_calls": calls, "raw_assistant": raw}

    # ------------------------------------------------------------------ Anthropic
    async def _anthropic(self, system, messages, tools):
        conv: list[dict] = []
        for m in messages:
            role = m["role"]
            if role == "user":
                conv.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"],
                                   "input": json.loads(tc["function"]["arguments"] or "{}")})
                conv.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif role == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if conv and conv[-1]["role"] == "user" and isinstance(conv[-1]["content"], list):
                    conv[-1]["content"].append(block)
                else:
                    conv.append({"role": "user", "content": [block]})
        r = await self.client.messages.create(
            model=self.model, system=system, messages=conv, max_tokens=4096, temperature=self.temperature,
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools])
        text, calls, raw_calls = "", [], []
        for b in r.content:
            if b.type == "text":
                text += b.text
            elif b.type == "tool_use":
                calls.append({"id": b.id, "name": b.name, "arguments": b.input or {}})
                raw_calls.append({"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input or {})}})
        raw = {"role": "assistant", "content": text}
        if raw_calls:
            raw["tool_calls"] = raw_calls
        return {"content": text, "tool_calls": calls, "raw_assistant": raw}


def _ns(o):
    """dict -> attribute object (so both HTTP and SDK-shaped responses parse the same way)."""
    if isinstance(o, dict):
        return _NS(**{k: _ns(v) for k, v in o.items()})
    if isinstance(o, list):
        return [_ns(x) for x in o]
    return o


class _HttpOpenAI:
    def __init__(self, llm: LLM):
        self.chat = _NS(completions=_NS(create=self._create))
        self._llm = llm

    async def _create(self, **kw):
        llm = self._llm
        url = llm.base_url if llm.base_url.endswith("/chat/completions") else llm.base_url + "/chat/completions"
        headers = {"Authorization": f"Bearer {llm.api_key}", "Content-Type": "application/json"}
        if llm.provider == "openrouter":
            headers.update({"HTTP-Referer": "https://github.com/bitunix-ai-agent", "X-Title": "Bitunix AI Agent"})
        d = await llm._post(url, headers, kw)
        for ch in d.get("choices", []):
            ch.setdefault("message", {}).setdefault("tool_calls", None)
            ch["message"].setdefault("content", None)
            for tc in ch["message"].get("tool_calls") or []:
                tc.setdefault("function", {}).setdefault("arguments", "{}")
        return _ns(d)


class _HttpAnthropic:
    def __init__(self, llm: LLM):
        self.messages = _NS(create=self._create)
        self._llm = llm

    async def _create(self, **kw):
        llm = self._llm
        url = llm.base_url + ("" if llm.base_url.endswith("/v1/messages") else "/v1/messages")
        headers = {"x-api-key": llm.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
        if not kw.get("tools"):
            kw.pop("tools", None)
        d = await llm._post(url, headers, kw)
        for b in d.get("content", []):
            b.setdefault("text", ""); b.setdefault("input", {})
        return _NS(content=[_NS(**b) for b in d.get("content", [])])
