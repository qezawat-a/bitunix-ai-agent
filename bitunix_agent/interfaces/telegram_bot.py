"""Telegram interface (python-telegram-bot v21+). Only whitelisted user ids can talk to the agent."""
from __future__ import annotations

import asyncio
import logging
import os

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

log = logging.getLogger("bitunix.telegram")

HELP = ("Talk to me in plain language, e.g.\n"
        "• long BTC 100 USDT margin 10x, SL 2%, TP 4%\n"
        "• analyze ETH on 4h\n• close half my SOL\n• alert me if BTC hits 70000\n"
        "• every hour, scan funding and tell me extremes\n\n"
        "/status  account overview\n/positions  open positions\n/dryrun on|off\n/kill  pause new trades\n/reset  new conversation")


def chunks(text: str, n: int = 4000):
    for i in range(0, len(text), n):
        yield text[i:i + n]


class TelegramInterface:
    def __init__(self, agent, token: str, allowed: list[str], proxy: str | None = None, api_url: str | None = None):
        self.agent = agent
        self.allowed = set(allowed)
        self.ready = False
        self._task: asyncio.Task | None = None
        b = Application.builder().token(token).connect_timeout(20).read_timeout(30).write_timeout(30).pool_timeout(20)
        b = b.get_updates_connect_timeout(20).get_updates_read_timeout(40)
        # Telegram is filtered in some countries: route it through a proxy (http://, https:// or socks5://)
        proxy = proxy or os.getenv("TELEGRAM_PROXY") or os.getenv("ALL_PROXY") or os.getenv("all_proxy") \
            or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
        if proxy:
            b = b.proxy(proxy).get_updates_proxy(proxy)
        # ...or through a Telegram API mirror (e.g. your own Cloudflare Worker): TELEGRAM_API_URL=https://my-mirror.example
        api_url = (api_url or os.getenv("TELEGRAM_API_URL") or "").rstrip("/")
        if api_url:
            b = b.base_url(f"{api_url}/bot").base_file_url(f"{api_url}/file/bot")
        self.proxy, self.api_url = proxy, api_url
        self.app = b.build()
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.start))
        self.app.add_handler(CommandHandler("id", self.show_id))
        self.app.add_handler(CommandHandler("status", self.cmd("Give me a short account overview: balance, positions with PnL, open orders.")))
        self.app.add_handler(CommandHandler("positions", self.cmd("List my open positions with entry, mark, PnL, liq price and TP/SL.")))
        self.app.add_handler(CommandHandler("kill", self.cmd("Turn the kill switch ON (pause new positions) and show risk status.")))
        self.app.add_handler(CommandHandler("dryrun", self.dryrun))
        self.app.add_handler(CommandHandler("reset", self.cmd("/reset")))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        agent.add_notifier(self.notify)

    def ok(self, update: Update) -> bool:
        uid = str(update.effective_user.id)
        if self.allowed and uid not in self.allowed:
            log.warning("blocked telegram user %s", uid)
            return False
        return True

    async def start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self.ok(update):
            await update.message.reply_text(f"Not authorized. Your id: {update.effective_user.id}")
            return
        self.agent.db.register_chat(f"tg:{update.effective_chat.id}", "telegram", str(update.effective_chat.id))
        await update.message.reply_text(f"{self.agent.settings.agent_name} online.\n\n{HELP}")

    async def show_id(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        await update.message.reply_text(f"Your Telegram user ID is {uid}\nPut it in .env as TELEGRAM_ALLOWED_USER_IDS={uid}")

    def cmd(self, prompt: str):
        async def h(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            await self._run(update, prompt)
        return h

    async def dryrun(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        arg = (ctx.args[0].lower() if ctx.args else "")
        await self._run(update, f"Set dry run {'on' if arg in ('on', '1', 'true') else 'off' if arg in ('off', '0', 'false') else 'status'} and tell me the state.")

    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await self._run(update, update.message.text)

    async def _run(self, update: Update, text: str):
        if not self.ok(update):
            return
        chat_id = f"tg:{update.effective_chat.id}"
        self.agent.db.register_chat(chat_id, "telegram", str(update.effective_chat.id))
        await update.effective_chat.send_action(ChatAction.TYPING)

        async def progress(_names):
            await update.effective_chat.send_action(ChatAction.TYPING)
        reply = await self.agent.chat(chat_id, text, progress)
        for part in chunks(reply):
            await update.message.reply_text(part)

    async def notify(self, chat_id: str, text: str):
        if not chat_id.startswith("tg:") or not self.ready:
            return
        for part in chunks(text):
            try:
                await self.app.bot.send_message(chat_id=int(chat_id[3:]), text=part)
            except Exception as e:
                log.warning("telegram notify failed: %s", e)

    async def _connect_forever(self):
        delay = 5
        while not self.ready:
            try:
                await self.app.initialize()
                await self.app.start()
                await self.app.updater.start_polling(drop_pending_updates=True)
                self.ready = True
                me = self.app.bot.username
                print(f"Telegram: connected as @{me}")
                log.info("telegram polling as @%s", me)
            except Exception as e:
                name = type(e).__name__
                if name == "InvalidToken":
                    print("Telegram: TELEGRAM_BOT_TOKEN is wrong (check it with @BotFather). Other interfaces keep running.")
                    return
                print(f"Telegram: can't reach api.telegram.org ({name}: {e}). Retrying in {delay}s. "
                      "If Telegram is filtered on your network, set TELEGRAM_PROXY=socks5://127.0.0.1:10808 "
                      "(your VPN app's local proxy) or TELEGRAM_API_URL in .env.")
                try:
                    await self.app.shutdown()
                except Exception:
                    pass
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)

    async def run(self):
        """Never blocks or crashes the agent: connects in the background and keeps retrying."""
        self._task = asyncio.create_task(self._connect_forever())

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
        if not self.ready:
            return
        for step in (self.app.updater.stop, self.app.stop, self.app.shutdown):
            try:
                await step()
            except Exception:
                pass
