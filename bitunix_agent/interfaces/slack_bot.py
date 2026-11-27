"""Slack interface (Bolt, Socket Mode: no public URL needed). DMs and @mentions."""
from __future__ import annotations

import asyncio
import logging
import os
import re

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

log = logging.getLogger("bitunix.slack")


class SlackInterface:
    def __init__(self, agent, bot_token: str, app_token: str, allowed: list[str], proxy: str | None = None):
        self.agent = agent
        self.allowed = set(allowed)
        self.ready = False
        self._task: asyncio.Task | None = None
        # Slack blocks some countries: SLACK_PROXY must be an HTTP proxy (aiohttp), e.g. v2rayNG http://127.0.0.1:10809
        proxy = proxy or os.getenv("SLACK_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or None
        if proxy and proxy.startswith("socks"):
            print("Slack: SLACK_PROXY must be http:// (Slack's client doesn't support socks5). "
                  "Use your VPN app's HTTP port, e.g. v2rayNG http://127.0.0.1:10809")
            proxy = None
        self.proxy = proxy
        self.app = AsyncApp(client=AsyncWebClient(token=bot_token, proxy=proxy))
        self.handler = AsyncSocketModeHandler(self.app, app_token, proxy=proxy)
        self.app.event("app_mention")(self.on_event)
        self.app.event("message")(self.on_message)
        agent.add_notifier(self.notify)

    async def on_message(self, event, say):
        if event.get("channel_type") == "im" and not event.get("bot_id") and not event.get("subtype"):
            await self._handle(event, say)

    async def on_event(self, event, say):
        await self._handle(event, say)

    async def _handle(self, event, say):
        user = event.get("user", "")
        allowed = set(self.agent.settings.slack_allowed_users) or self.allowed
        owner_note = ""
        if not allowed and user:
            # First person to talk to J-Rock becomes the owner (saved; no .env needed).
            self.agent.store.set("SLACK_ALLOWED_USER_IDS", user)
            self.agent.reload_settings("SLACK_ALLOWED_USER_IDS")
            self.allowed = {user}
            owner_note = f"You're my owner now (Slack member ID {user}). Only you can use me.\n\n"
        elif allowed and user not in allowed:
            await say(f"Not authorized (user {user}).")
            return
        text = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()
        if _asks_for_id(text):
            await say(f"Your Slack member ID is {user}\nPut it in .env as SLACK_ALLOWED_USER_IDS={user}")
            return
        chat_id = f"slack:{event['channel']}"
        self.agent.db.register_chat(chat_id, "slack", event["channel"])
        thread = event.get("thread_ts") or event.get("ts")
        reply = owner_note + to_slack_markdown(await self.agent.chat(chat_id, text))
        await say(text=reply, thread_ts=thread if event.get("channel_type") != "im" else None)

    async def notify(self, chat_id: str, text: str):
        if chat_id.startswith("slack:"):
            await self.app.client.chat_postMessage(channel=chat_id[6:], text=to_slack_markdown(text))

    async def _connect_forever(self):
        delay = 5
        while not self.ready:
            try:
                await self.handler.connect_async()
                self.ready = True
                print("Slack: connected")
                log.info("slack socket mode connected")
            except Exception as e:
                msg = str(e)
                if "invalid_auth" in msg or "not_authed" in msg or "token" in msg.lower() and "invalid" in msg.lower():
                    print(f"Slack: token problem ({msg}). SLACK_BOT_TOKEN must start with xoxb-, SLACK_APP_TOKEN with xapp- "
                          "(Socket Mode on, scope connections:write). Other interfaces keep running.")
                    return
                print(f"Slack: can't connect ({type(e).__name__}: {msg[:200]}). Retrying in {delay}s. "
                      "If Slack is blocked on your network, set SLACK_PROXY=http://127.0.0.1:10809 in .env.")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)

    async def run(self):
        """Never blocks or crashes the agent: connects in the background and keeps retrying."""
        self._task = asyncio.create_task(self._connect_forever())

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
        try:
            await self.handler.close_async()
        except Exception:
            pass


_ID_WORDS = re.compile(r"^\s*/?(id|my ?id|member ?id|user ?id|whoami|who am i|i need my member id|what(?:'s| is) my (?:member |user )?id\??)\s*[?!.]*\s*$", re.I)


def _asks_for_id(text: str) -> bool:
    t = text.strip()
    return bool(_ID_WORDS.match(t)) or ("member id" in t.lower() and len(t) < 40) or t in ("آیدی", "آیدی من", "ایدی")


def to_slack_markdown(text: str) -> str:
    """Slack's mrkdwn uses *bold*, not **bold**, and has no # headings."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text or "")
    text = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.M)
    return text
