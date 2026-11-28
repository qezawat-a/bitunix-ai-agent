"""The agent: services container, system prompt, tool-calling loop, per-chat memory."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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
from ..extensions.agent_tools import CustomTools, register as register_agent_tools
from ..extensions.mcp_client import MCPManager
from ..extensions.skills import Skills
from ..extensions.store import SettingsStore
from ..storage.filesync import FileSync
from .llm import LLM
from .tools import Tool, ToolContext, ToolExecutor, build_tools

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
- Prefer the smart tools (open_position, close_position, set_tpsl, ladder_orders, set_trailing_stop). They use HEDGE mode only (tradeSide OPEN/CLOSE + positionId, per the docs),
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

YOU ARE A FULL AGENT, NOT ONLY A TRADER
- You extend yourself from chat: set_setting (API keys, LLM, risk, tokens: no .env needed), add_skill / install_skill_from_url,
  add_mcp_server (remote url or local command), create_tool (new Python tools), restart_agent.
- You write and fix code: list_files, read_file, edit_file, write_file, search_code, run_shell, run_python, check_code,
  code_review, git_clone, git_commit_push. 'self' is your own code. Workflow for any code change: read the code -> edit ->
  check_code -> fix until it passes -> tell the user what changed. For your own code, then restart_agent (or git_commit_push
  when you run from a git repo, e.g. Railway deploys from GitHub).
- Other projects: git_clone them into the workspace (or use the path the user gives) and work there.
- When the user asks for something you have no tool for: build it (create_tool, an MCP server, a script), don't decline.

HONESTY
- Never claim you did something unless a tool result in this conversation shows it worked. If a step failed, say so and
  what you'll do next. Never invent numbers, prices, balances or file contents.
- Never show raw internals to the user: no JSON tool calls, no "tool_call", no "null"/"None", no stack dumps unless asked.
  Explain results in plain words.

SKILLS YOU HAVE (call read_skill to load one when it's relevant, and follow it):
{skills}

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
        # ---- self-extension: settings from chat, skills, MCP servers, custom tools, coding tools
        data_dir = os.path.dirname(os.path.abspath(settings.db_path)) if settings.db_path != ":memory:" else os.path.abspath("data")
        self.store = SettingsStore(settings.db_path)
        # With DATABASE_URL (Neon/Postgres), skills, MCP servers and custom tools are kept in the DB too.
        self.filesync = FileSync(settings.db_path, data_dir)
        try:
            self.filesync.restore()
        except Exception as e:
            log.warning("could not restore files from the database: %s", e)
        self.skills = Skills(os.path.join(data_dir, "skills"))
        self.mcp = MCPManager(os.path.join(data_dir, "mcp.json"))
        self._mcp_tool_names: set[str] = set()
        register_agent_tools(self.tools, self)
        self.custom_tools = CustomTools(os.path.join(data_dir, "custom_tools"), self.tools)
        self.custom_tools.load_all()
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
        asyncio.get_running_loop().create_task(self._start_mcp())
        if self.filesync.enabled:
            asyncio.get_running_loop().create_task(self.filesync.run())

    async def _start_mcp(self):
        try:
            await self.mcp.start_all()
        except Exception:
            log.exception("mcp start")
        self.refresh_mcp_tools()

    def refresh_mcp_tools(self):
        for n in self._mcp_tool_names:
            self.tools.pop(n, None)
        self._mcp_tool_names = set()
        for tname, server, mname, schema, desc in self.mcp.tool_specs():
            def make(srv, mn):
                async def fn(ctx, **args):
                    return await self.mcp.servers[srv].call_tool(mn, args)
                return fn
            self.tools[tname] = Tool(tname, desc, schema, make(server, mname), False)
            self._mcp_tool_names.add(tname)

    def reload_settings(self, changed: str | None = None):
        """Re-read settings (chat-set values are already in os.environ) and apply them live."""
        from ..config import Settings
        new = Settings()
        for f in new.__dataclass_fields__:
            setattr(self.settings, f, getattr(new, f))
        s = self.settings
        if changed in (None, "DRY_RUN"):
            self.rest.dry_run = s.dry_run
        self.rest.api_key, self.rest.secret_key = s.bitunix_api_key, s.bitunix_secret_key
        self.ws.api_key, self.ws.secret_key = s.bitunix_api_key, s.bitunix_secret_key
        if changed is None or (changed or "").startswith("LLM_"):
            try:
                self.llm = LLM(s.llm_provider, s.llm_api_key, s.llm_model, s.llm_base_url, s.llm_temperature) if s.llm_api_key else None
            except Exception as e:
                log.warning("LLM settings invalid: %s", e)
                self.llm = None
                return str(e)
        return None

    async def stop(self):
        try:
            self.filesync.sync_once()
        except Exception:
            pass
        try:
            await self.mcp.close()
        except Exception:
            pass
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
        try:
            skills = self.skills.prompt_block()
        except Exception as e:
            skills = f"(skills unavailable: {e})"
        return SYSTEM_PROMPT.format(name=self.settings.agent_name, n_tools=len(self.tools), dry_run=self.rest.dry_run,
                                    tz=self.settings.timezone, now=now, skills=skills,
                                    notes="\n".join(f"- {k}: {v}" for k, v in notes.items()) or "(none)")

    async def chat(self, chat_id: str, text: str, on_progress=None) -> str:
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            return await self._chat(chat_id, text, on_progress)

    async def _chat(self, chat_id: str, text: str, on_progress=None) -> str:
        if text.strip().lower() in ("/reset", "/new", "reset chat"):
            self.db.clear_history(chat_id)
            return "Fresh start. What are we trading?"
        local = await self._local_command(text)
        if local is not None:
            return local
        if not self.llm:
            return ("I need a brain first. Send these here (one per message):\n"
                    "set LLM_PROVIDER=deepseek   (or openai, anthropic, openrouter, dashscope, custom)\n"
                    "set LLM_API_KEY=your-key\n"
                    "set LLM_MODEL=deepseek-chat   (optional)\n"
                    "No .env editing needed.")
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
                    return _llm_error_text(e)
            if not out["tool_calls"]:
                textual = _textual_tool_calls(out["content"])
                if textual:  # some models print tool calls as text instead of calling them: run them anyway
                    out["tool_calls"] = textual
                    out["raw_assistant"] = {"role": "assistant", "content": "", "tool_calls": [
                        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                        for c in textual]}
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
        return clean_reply(reply) or "Done."

    async def _local_command(self, text: str) -> str | None:
        """Commands that work without the LLM (so even the LLM key can be set from chat)."""
        t = text.strip()
        m = re.match(r"^/?set\s+([A-Za-z][A-Za-z0-9_]*)\s*(?:=|:|\s)\s*(.*)$", t, re.S)
        if m and (m.group(1).upper() in KNOWN_SETTINGS or "_" in m.group(1)):
            name, value = m.group(1).upper(), m.group(2).strip().strip("\"'`")
            self.store.set(name, value)
            err = self.reload_settings(name)
            from ..extensions.store import NEEDS_RESTART, mask
            note = " Restart needed: send 'restart'." if name in NEEDS_RESTART else ""
            if err:
                note += f" Warning: {err}"
            if name == "LLM_API_KEY" and self.llm:
                note += f" Brain online: {self.llm.provider} / {self.llm.model}."
            return f"Saved {name} = {mask(name, value) if value else '(removed)'}.{note}"
        low = t.lower()
        if low in ("/settings", "settings", "show settings"):
            from ..extensions.store import KNOWN, mask
            return "\n".join(f"{k} = {mask(k, os.getenv(k))}" for k in KNOWN if os.getenv(k))  or "No settings yet."
        if low in ("/restart", "restart", "restart agent"):
            await self.tools["restart_agent"].fn(None)
            return "Restarting in 3 seconds."
        return None


def _llm_error_text(e: Exception) -> str:
    msg = str(e)
    if "insufficient_quota" in msg or "quota" in msg.lower() and "429" in msg:
        return ("The language model's credit/quota is used up (HTTP 429). Your settings are fine. "
                "Top it up, or switch with: set LLM_API_KEY=... (and LLM_BASE_URL / LLM_MODEL if the provider changes).")
    if "LLM HTTP 429" in msg:
        return ("The language model provider is rate-limiting (HTTP 429: too many requests per minute). Your key and settings are fine. "
                "I waited and retried, but it was still busy. Fewer/slower scheduled tasks or a paid plan fixes it.")
    if "LLM HTTP 401" in msg or "LLM HTTP 403" in msg:
        return f"The language model rejected the key ({msg[:200]}). Check LLM_API_KEY."
    return f"The language model returned an error ({type(e).__name__}: {msg[:300]}). Check LLM_PROVIDER / LLM_MODEL / LLM_API_KEY."


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


from ..extensions.store import KNOWN as KNOWN_SETTINGS  # noqa: E402

_TC_PATTERNS = [
    re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S),
    re.compile(r"```(?:json|tool_call)?\s*(\{\s*\"name\"\s*:.*?\})\s*```", re.S),
]


def _textual_tool_calls(content: str | None) -> list[dict]:
    """Parse tool calls a model wrote as text (<tool_call>{...}</tool_call>, DeepSeek/Qwen markup, ```json {name, arguments}```)."""
    if not content or ("tool_call" not in content and '"arguments"' not in content and "tool▁call" not in content):
        return []
    calls = []
    for rx in _TC_PATTERNS:
        for m in rx.finditer(content):
            try:
                d = json.loads(m.group(1))
            except ValueError:
                continue
            name = d.get("name") or (d.get("function") or {}).get("name")
            args = d.get("arguments", d.get("parameters", (d.get("function") or {}).get("arguments", {})))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if name:
                calls.append({"id": f"txt_{len(calls)}_{int(time.time() * 1000)}", "name": name, "arguments": args or {}})
    # DeepSeek raw markup: <｜tool▁call▁begin｜>function<｜tool▁sep｜>NAME\n```json\n{...}\n```<｜tool▁call▁end｜>
    for m in re.finditer(r"tool▁sep｜>([\w\-]+)\s*```(?:json)?\s*(\{.*?\})\s*```", content, re.S):
        try:
            calls.append({"id": f"txt_{len(calls)}_{int(time.time() * 1000)}", "name": m.group(1), "arguments": json.loads(m.group(2))})
        except ValueError:
            pass
    return calls


def clean_reply(text: str | None) -> str:
    """Remove model/runtime artifacts so the user never sees 'null', 'None' or tool-call markup."""
    if not text:
        return ""
    t = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.S)
    t = re.sub(r"<｜tool▁calls?▁begin｜>.*?(<｜tool▁calls?▁end｜>|$)", "", t, flags=re.S)
    t = re.sub(r"</?(tool_call|tool_response|function_calls?|invoke|parameter)[^>]*>", "", t)
    t = re.sub(r"^\s*(null|None|undefined|NaN)\s*$", "", t, flags=re.M | re.I)
    t = re.sub(r"\bnull\b(?=\s*$)", "", t.strip())
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
