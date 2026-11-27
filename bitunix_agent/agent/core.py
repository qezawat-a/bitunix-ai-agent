"""The agent: services container, system prompt, tool-calling loop, per-chat memory."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import Settings
from ..engine.monitor import Monitor
from ..engine.risk import RiskManager
from ..engine.trading import TradingEngine
from ..exchange.rest import BitunixREST
from ..exchange.ws import BitunixWS
from ..storage.db import DB
from .llm import LLM
from .tools import ToolContext, ToolExecutor, build_tools

log = logging.getLogger("bitunix.agent")

SYSTEM_PROMPT = """You are {name}, an expert crypto-futures trading agent wired directly into the user's Bitunix futures account.

WHO YOU ARE
- A sharp, fast, practical trader and engineer. You act; you do not lecture.
- You have {n_tools} tools: every official Bitunix futures REST endpoint (api_*), smart trading tools, live WebSocket data,
  analysis, scanners, alerts, schedules, risk controls, memory and a calculator.

THE GOLDEN RULE: NEVER SAY "I CAN'T"
- Never answer with "I can't", "I'm unable", "not possible" or similar. If one route fails, take another:
  read the error hint, call api_docs, fix the parameters, use a different tool, compute it yourself, or schedule it.
- If something truly needs the user (a missing API permission, an IP whitelist, funds), say exactly what to do and offer
  the closest thing you can do right now.
- If a guard rail blocks a trade, present the adjusted_plan it returned and ask which they want. Never silently drop a trade.

HOW YOU TRADE
- Before any order: know the price (market_snapshot), the rules (min qty, precision, max leverage) and the account (account_overview).
- Prefer the smart tools (open_position, close_position, set_tpsl, ladder_orders, set_trailing_stop). They handle HEDGE vs ONE_WAY,
  rounding, leverage, chunking and TP/SL validation. Use api_* tools for anything else.
- Every quantity is in the BASE coin (BTC for BTCUSDT). If the user speaks in USDT, use notional_usdt or margin_usdt.
- When a tool returns status PENDING_CONFIRMATION: show a short clear ticket (symbol, side, size, est. entry, leverage, margin,
  TP/SL, est. liquidation) and ask for yes. When they say yes/ok/confirm/go/بله/آره, call confirm_action with that id.
- A successful order response means "accepted", not "filled". Say so, and check positions/orders if it matters.
- Suggest a stop loss when the user opens without one, but respect their call.
- DRY_RUN={dry_run}: when true, say clearly that orders are simulated.

ANALYSIS
- Use technical_analysis / multi_timeframe_analysis / market_snapshot / funding_scan / scan_market and real numbers.
- Give a clear view (bias, key levels, invalidation, plan with entry/SL/TP and R:R). Label opinions as opinions; no guarantees.
- get_funding_rate returns percent units (0.0083 means 0.0083%); funding history returns plain fractions.

STYLE
- Reply in the user's language (Persian/فارسی if they write Persian). Short, structured, numbers first. Use tables only when helpful.
- Times: the user's timezone is {tz}. Now: {now}.

USER NOTES (long-term memory):
{notes}
"""


@dataclass
class Services:
    settings: Settings
    rest: BitunixREST
    ws: BitunixWS
    engine: TradingEngine
    db: DB
    executor: ToolExecutor | None = None


class Agent:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = DB(settings.db_path)
        self.rest = BitunixREST(settings.bitunix_api_key, settings.bitunix_secret_key, base_url=settings.bitunix_rest_url,
                                dry_run=settings.dry_run, proxy=settings.http_proxy)
        self.ws = BitunixWS(settings.bitunix_api_key, settings.bitunix_secret_key, public_url=settings.bitunix_ws_public,
                            private_url=settings.bitunix_ws_private)
        self.engine = TradingEngine(self.rest, self.ws, settings, RiskManager(settings))
        self.services = Services(settings, self.rest, self.ws, self.engine, self.db)
        self.tools = build_tools()
        self.executor = ToolExecutor(self.tools, self.services)
        self.services.executor = self.executor
        self.llm = LLM(settings.llm_provider, settings.llm_api_key, settings.llm_model, settings.llm_base_url, settings.llm_temperature) \
            if settings.llm_api_key else None
        self.notifiers: list = []
        self.monitor = Monitor(self.engine, self.db, self._notify, self.chat)
        self._locks: dict[str, asyncio.Lock] = {}
        self.rest.on_request = lambda r: log.debug("REST %s", r)

    # ------------------------------------------------------------------ lifecycle
    async def start(self):
        for s in self.settings.watch_symbols:
            for ch in ("ticker", "price", "depth_book5"):
                await self.ws.subscribe(s, ch)
        if self.settings.has_keys:
            await self.ws.start_private()
            try:
                await self.engine.account()  # learns position mode for validation
            except Exception as e:
                log.warning("account check failed: %s", e)
        self.monitor.start()

    async def stop(self):
        await self.monitor.stop()
        await self.ws.close()
        await self.rest.close()

    def add_notifier(self, fn):
        """fn(chat_id, text) -> awaitable; each interface registers one and ignores chat ids it does not own."""
        self.notifiers.append(fn)

    async def _notify(self, chat_id: str, text: str):
        for fn in self.notifiers:
            try:
                await fn(chat_id, text)
            except Exception:
                log.exception("notifier failed")

    # ------------------------------------------------------------------ chat
    def _system(self) -> str:
        notes = self.db.notes()
        try:
            now = datetime.now(ZoneInfo(self.settings.timezone)).strftime("%Y-%m-%d %H:%M %Z")
        except Exception:
            now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        return SYSTEM_PROMPT.format(name=self.settings.agent_name, n_tools=len(self.tools), dry_run=self.rest.dry_run,
                                    tz=self.settings.timezone, now=now,
                                    notes="\n".join(f"- {k}: {v}" for k, v in notes.items()) or "(none)")

    async def chat(self, chat_id: str, text: str, on_progress=None) -> str:
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            return await self._chat(chat_id, text, on_progress)

    async def _chat(self, chat_id: str, text: str, on_progress=None) -> str:
        if text.strip().lower() in ("/reset", "/new", "reset chat"):
            self.db.clear_history(chat_id)
            return "Fresh start. What are we trading?"
        if not self.llm:
            return "LLM_API_KEY is not set. Add it to .env (LLM_PROVIDER, LLM_API_KEY, LLM_MODEL) and restart."
        user = {"role": "user", "content": text}
        self.db.add_message(chat_id, user)
        history = _trim(self.db.history(chat_id, 60))
        schemas = [t.schema() for t in self.tools.values()]
        ctx = ToolContext(chat_id=chat_id, services=self.services)
        reply = ""
        for _ in range(self.settings.llm_max_tool_rounds):
            try:
                out = await self.llm.chat(self._system(), history, schemas)
            except Exception as e:
                if _is_content_filter(e):
                    # The provider's safety filter rejected the request (often a word in the history).
                    # Retry once with only the latest user message so one flagged turn can't block the chat.
                    log.warning("llm content filter hit; retrying with short history")
                    try:
                        history = [user]
                        out = await self.llm.chat(self._system(), history, schemas)
                    except Exception as e2:
                        log.warning("llm content filter again: %s", e2)
                        self.db.clear_history(chat_id)
                        return ("My language model's provider filtered that message (their safety filter, not me). "
                                "I cleared our chat history so it won't repeat. Try rephrasing it, e.g. "
                                "'show my balance' or 'analyze BTCUSDT'. If it keeps happening, switch LLM_PROVIDER/LLM_MODEL in .env.")
                else:
                    log.exception("llm error")
                    return f"The language model returned an error ({type(e).__name__}: {e}). Check LLM_PROVIDER / LLM_MODEL / LLM_API_KEY."
            msg = out["raw_assistant"]
            history.append(msg)
            self.db.add_message(chat_id, msg)
            if not out["tool_calls"]:
                reply = out["content"]
                break
            if on_progress:
                try:
                    await on_progress(", ".join(c["name"] for c in out["tool_calls"]))
                except Exception:
                    pass
            results = await asyncio.gather(*(self.executor.execute(c["name"], c["arguments"], ctx) for c in out["tool_calls"]))
            for c, r in zip(out["tool_calls"], results):
                tmsg = {"role": "tool", "tool_call_id": c["id"], "content": _clip(json.dumps(r, default=str, ensure_ascii=False))}
                history.append(tmsg)
                self.db.add_message(chat_id, tmsg)
        else:
            reply = out.get("content") or "I ran many steps on this. Here is where things stand; tell me how to continue."
        return reply or "Done."


def _clip(s: str, n: int = 12000) -> str:
    return s if len(s) <= n else s[:n] + f'... [truncated {len(s) - n} chars; narrow the query for the rest]'


def _trim(msgs: list[dict]) -> list[dict]:
    """Start history at a user turn so every tool result has its tool call."""
    for i, m in enumerate(msgs):
        if m.get("role") == "user":
            return msgs[i:]
    return msgs


def _is_content_filter(e: Exception) -> bool:
    t = str(e).lower()
    return any(k in t for k in ("content_filter", "data_inspection_failed", "inappropriate content", "content management policy"))
