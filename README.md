# Bitunix AI Futures Agent

An AI trading agent wired into your Bitunix futures account. Talk to it on **Telegram**, **Slack**, the **terminal**, or from your own **Android app** over HTTP/WebSocket.

Built straight from the official Bitunix futures docs (snapshot of all 50 pages in `docs_snapshot/`):

| Area | Covered |
|---|---|
| REST endpoints | **35 / 35** (TP/SL 7, Account 7, Market 7, Position 3, Trade 11) |
| Request params | every one, with type, requirement, enums and conditional rules |
| Response fields | every one (documented in the spec, normalized where the live API differs) |
| WebSocket | ping, login, subscribe, unsubscribe + all 10 channels (depth, tickers, ticker, price, kline ×30, trade, balance, position, order, tpsl) |
| Error codes | all 79, each with a fix hint the agent uses to correct itself |
| Changelog | 2026-10-27 kline `startTime` removal, 2026-10-15 `MARK_PRICE` klines, 2026-10-08 open-time semantics, 2026-06-15 WS 5 msg/s limit |

`PARAMETERS.md` lists every endpoint, parameter, enum, rule, response field and push field. `tests/test_spec_vs_docs.py` fails if a single one is missing or differs from the docs.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                    # fill in keys
pytest                                                  # 111 tests, offline + live public API
python -m bitunix_agent --check                         # verifies keys, signature, permissions
python -m bitunix_agent --cli                           # chat in the terminal
python -m bitunix_agent                                 # Telegram + Slack + HTTP API together
```

Docker: `docker compose up -d --build`

**Android / Termux:** `bash install_termux.sh`. Every dependency is pure Python (Starlette instead of FastAPI, plain-HTTP LLM calls), so nothing needs Rust or a C compiler. Slack is optional (`requirements-slack.txt`). Run with `termux-wake-lock` so Android doesn't kill it.

**Start with `DRY_RUN=true`.** Every order is validated and built exactly as it would be sent, but nothing reaches Bitunix. Say "turn dry run off" (or set `DRY_RUN=false`) when you are happy.

## What you can say

- `long BTC with 100 USDT margin at 20x, SL 58500, TP 63000`
- `short ETH risking 1% of my balance, stop at 3420`
- `ladder 5 limit buys on SOL between 150 and 140, total 2000 USDT`
- `close half my BTC long` · `close everything` · `reverse my ETH position`
- `move my stop to breakeven` · `trail my BTC stop 1.5% after 64000`
- `analyze BTC on 15m, 1h, 4h, 1d and give me a plan`
- `top gainers with volume over 10M` · `most negative funding right now`
- `alert me if ETH mark drops below 3000` · `every hour check my positions and report`
- `what's my win rate this month?` · `show the raw history of TP/SL orders`
- Persian works too: `یک لانگ بیت‌کوین با ۵۰ دلار و لوریج ۱۰ باز کن`

## How it thinks

```
chat (Telegram/Slack/Android/CLI)
   └─ Agent (LLM tool loop, memory per chat, "never say I can't" policy)
        ├─ 35 api_* tools  ← generated from the official spec (every parameter + enum)
        ├─ smart tools     ← open/close/TP-SL/trailing/ladder/sizing/analysis/scanners/alerts/schedules
        ├─ ToolExecutor    ← confirmations, journal, error → hint → retry
        └─ TradingEngine   ← HEDGE vs ONE_WAY, rounding, leverage, chunking, TP/SL checks, risk rails
             ├─ BitunixREST  (validation → exact signing → rate limits → safe retries → normalization)
             └─ BitunixWS    (login, ping, 5 msg/s throttle, 300-channel sharding, reconnect, live state)
```

### Never "I can't"
Every failure comes back to the model as a structured error with the official message and a concrete fix (`hint`). The system prompt forbids "I can't"; the agent fixes parameters, switches tools, reads `api_docs`, or offers the closest alternative. Guard-rail violations return an **adjusted plan** (e.g. "20x instead of 50x", "qty 0.012 to keep risk at 1%"), never a flat refusal. If the user explicitly overrides, the agent can pass `skip_risk=true`.

### Safety you control
- `CONFIRM_TRADES=always|never|above` — the agent shows a ticket and waits for "yes" (`confirm_action`).
- Kill switch, max leverage, max notional, max risk per trade, daily loss limit, stop-loss requirement, symbol allow list.
- Every exchange action is journaled in SQLite (`trade_journal` tool, `/v1/journal`).
- Telegram/Slack allow lists by user id; HTTP API protected by a bearer token.
- POST requests are never blindly re-sent after a network error; every order carries a clientId so state can be checked.

## Doc vs live-API differences handled for you
Verified against the live API on 2026-11-26:
- `get_funding_rate` returns an object (doc example shows a list) and the rate in **percent** units; history returns plain fractions.
- `get_kline` returns newest-first with string times → normalized to oldest-first integer ms.
- `defaultMarginMode` comes back as `"1"`/`"2"`.
- TP/SL stop types: table says `MARK_PRICE`/`LAST_PRICE`, some examples say `MARK`/`LAST` → both accepted, full names sent.
- `cancel_orders` table lists `orderId/clientId` at top level; they belong inside `orderList` items.
- `cancel_all_orders` returns `orderId` while the table says `id` → both keys provided.
- WS `unsubscribe` example uses `channel`, table uses `ch` → both sent.
- HEDGE close follows the official table (close long = `BUY`+`CLOSE`+`positionId`); ONE_WAY close uses the opposite side + `reduceOnly`. If Bitunix rejects the shape with a parameter error, the engine retries with the other documented form (a rejected request executes nothing).

## When Bitunix updates the docs
```bash
python tools/fetch_docs.py     # re-download all 50 pages
python tools/gen_spec.py       # rebuild spec + typed methods
python tools/gen_parameters_md.py
pytest                         # tells you exactly which field changed
```
New endpoints are reachable immediately through the `raw_request` tool.

## Interfaces

**Telegram**: create a bot with @BotFather, set `TELEGRAM_BOT_TOKEN`, send `/start` once to see your user id, put it in `TELEGRAM_ALLOWED_USER_IDS`, restart. Commands: `/status /positions /kill /dryrun on|off /reset`.

**Slack**: create an app, enable Socket Mode (app token `xapp-…` with `connections:write`), bot scopes `app_mentions:read chat:write im:history im:read im:write`, subscribe to `app_mention` and `message.im`, install, set both tokens.

**Android**: see `ANDROID.md`. The server exposes `/v1/chat`, `/v1/tools/{name}`, `/v1/confirm/{id}`, `/v1/stream` (live pushes) and OpenAPI at `/health`.

## Files
```
bitunix_agent/
  exchange/   spec_generated.py (from docs) · endpoints_generated.py (35 typed methods) · rest.py · ws.py · ws_spec.py
              signing.py · validation.py · errors.py · constants.py · symbols.py · rate_limit.py
  engine/     trading.py · risk.py · indicators.py · monitor.py
  agent/      core.py · tools.py · llm.py
  interfaces/ telegram_bot.py · slack_bot.py · http_api.py
  storage/    db.py
docs_snapshot/  official pages as text + parsed tables
tools/          fetch_docs.py · gen_spec.py · gen_parameters_md.py
tests/          spec-vs-docs, signing, every endpoint request, validation, engine, agent loop, LLM adapters, HTTP API, live public
```

Trading futures with leverage can lose more than you expect quickly. The analysis tools give opinions from data, not guarantees.
