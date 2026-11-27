"""Run the agent.

python -m bitunix_agent                 # every interface that has credentials in .env (Telegram, Slack, HTTP API)
python -m bitunix_agent --cli           # chat in the terminal
python -m bitunix_agent --check         # verify keys, signature, permissions and market data, then exit
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from .agent.core import Agent
from .config import settings


async def check(agent: Agent):
    print("Public market data ...", end=" ")
    snap = await agent.engine.market_snapshot("BTCUSDT")
    print("OK  BTCUSDT last", snap["last"], "mark", snap["mark"])
    if not settings.has_keys:
        print("No BITUNIX_API_KEY/SECRET: private endpoints skipped.")
        return
    print("Signed request (account) ...", end=" ")
    acct = await agent.engine.account()
    print("OK  available", acct.get("available"), "positionMode", acct.get("positionMode"))
    print("Positions ...", len(await agent.engine.positions()))
    print("Pending orders ...", (await agent.rest.get_pending_orders(limit=10) or {}).get("total"))
    print("Trading settings ...", (await agent.rest.get_trading_settings(symbols="BTCUSDT")))
    print("DRY_RUN =", agent.rest.dry_run, "| CONFIRM_TRADES =", settings.confirm_trades)
    print("LLM =", settings.llm_provider, agent.llm.model if agent.llm else "(no LLM_API_KEY)")
    print("All checks passed.")


async def cli(agent: Agent):
    print(f"{settings.agent_name} ready (DRY_RUN={agent.rest.dry_run}). Type 'exit' to quit.")
    loop = asyncio.get_running_loop()
    async def show(chat_id, text):
        print(f"\n[notify] {text}\n> ", end="")
    agent.add_notifier(show)
    agent.db.register_chat("cli", "cli", "cli")
    while True:
        text = await loop.run_in_executor(None, input, "> ")
        if text.strip().lower() in ("exit", "quit"):
            break
        print(await agent.chat("cli", text))


async def serve(agent: Agent):
    runners = []
    if settings.telegram_token:
        try:
            from .interfaces.telegram_bot import TelegramInterface
            tg = TelegramInterface(agent, settings.telegram_token, settings.telegram_allowed_users)
            await tg.run(); runners.append(tg)
            print("Telegram: connecting in the background" + (f" via proxy {tg.proxy}" if tg.proxy else ""))
        except Exception as e:  # keep the other interfaces alive
            logging.exception("Telegram failed to start: %s", e)
            print("Telegram NOT started:", e, "-> check TELEGRAM_BOT_TOKEN / pip install python-telegram-bot")
    if settings.slack_bot_token and settings.slack_app_token:
        try:
            from .interfaces.slack_bot import SlackInterface
            sl = SlackInterface(agent, settings.slack_bot_token, settings.slack_app_token, settings.slack_allowed_users)
            await sl.run(); runners.append(sl)
            print("Slack: connecting in the background" + (f" via proxy {sl.proxy}" if sl.proxy else ""))
        except ImportError:
            print("Slack NOT started: run  pip install -r requirements-slack.txt")
        except Exception as e:
            logging.exception("Slack failed to start: %s", e)
            print("Slack NOT started:", e)
    import uvicorn
    from .interfaces.http_api import build_app
    server = uvicorn.Server(uvicorn.Config(build_app(agent), host=settings.api_host, port=settings.api_port, log_level="info"))
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    api = asyncio.create_task(server.serve())
    await asyncio.wait([api, asyncio.create_task(stop.wait())], return_when=asyncio.FIRST_COMPLETED)
    server.should_exit = True
    for r in runners:
        await r.stop()


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cli", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--log", default="INFO")
    a = p.parse_args()
    logging.basicConfig(level=a.log, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    agent = Agent(settings)
    try:
        if a.check:
            await check(agent)
            return
        await agent.start()
        await (cli(agent) if a.cli else serve(agent))
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
