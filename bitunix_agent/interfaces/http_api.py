"""HTTP + WebSocket API for your Android app (or anything else). Bearer-token protected.

POST /v1/chat                 {"chat_id": "android-1", "message": "long BTC 50 USDT 10x"} -> {"reply": "..."}
GET  /v1/overview             account, positions, orders, TP/SL, risk
GET  /v1/market/{symbol}      market snapshot
GET  /v1/analysis/{symbol}?interval=1h
POST /v1/tools/{tool_name}    call any agent tool directly with JSON args (same confirmation rules)
GET  /v1/tools                list tools with JSON schemas
POST /v1/confirm/{id}         confirm a pending action
GET  /v1/alerts | /v1/tasks | /v1/journal
WS   /v1/stream?token=...     live push: notifications, order/position/tpsl/balance events, tickers
GET  /health                  status (no token)

Built on Starlette only (pure Python, no pydantic/Rust), so it installs on Termux/Android.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from ..agent.tools import ToolContext

log = logging.getLogger("bitunix.api")


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(data, default=str)), status_code=status)


async def _body(req: Request) -> dict:
    raw = await req.body()
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        return {"message": raw.decode("utf-8", "replace")}
    return d if isinstance(d, dict) else {"value": d}


def build_app(agent) -> Starlette:
    token = agent.settings.api_token
    clients: set[WebSocket] = set()

    async def broadcast(payload: dict):
        if not clients:
            return
        msg = json.dumps(payload, default=str)
        for ws in list(clients):
            try:
                await ws.send_text(msg)
            except Exception:
                clients.discard(ws)

    async def push(chat_id: str, text: str):
        await broadcast({"type": "notification", "chat_id": chat_id, "text": text})
    agent.add_notifier(push)

    async def on_ws_event(evt: dict):
        await broadcast({"type": "event", **evt})
    for ch in ("order", "position", "tpsl", "balance", "ticker", "price"):
        agent.ws.on(ch, on_ws_event)

    def guarded(fn):
        async def handler(req: Request):
            if token and req.headers.get("authorization", "") != f"Bearer {token}":
                return _json({"detail": "bad token"}, 401)
            try:
                return _json(await fn(req))
            except Exception as e:  # never a bare 500: return the reason so the app can show it
                log.exception("api error")
                return _json({"ok": False, "error": type(e).__name__, "detail": str(e)}, 500)
        return handler

    def chat_id_of(req: Request, default: str = "android") -> str:
        return req.query_params.get("chat_id", default)

    async def health(req: Request):
        return _json({"ok": True, "dry_run": agent.rest.dry_run, "ws": agent.ws.status(), "keys": agent.settings.has_keys})

    async def chat(req: Request):
        b = await _body(req)
        if not b.get("message"):
            return {"ok": False, "error": "missing_field", "detail": "send {\"chat_id\": \"android\", \"message\": \"...\"}"}
        cid_raw = str(b.get("chat_id") or "android")
        cid = f"app:{cid_raw}"
        agent.db.register_chat(cid, "app", cid_raw)
        return {"chat_id": cid_raw, "reply": await agent.chat(cid, b["message"])}

    async def overview(req):
        return await agent.engine.overview()

    async def market(req):
        return await agent.engine.market_snapshot(req.path_params["symbol"])

    async def analysis(req):
        return await agent.engine.technical_analysis(req.path_params["symbol"], req.query_params.get("interval", "1h"))

    async def tools(req):
        return [dict(t.schema(), mutating=t.mutating) for t in agent.tools.values()]

    async def call_tool(req):
        ctx = ToolContext(chat_id=f"app:{chat_id_of(req)}", services=agent.services)
        return await agent.executor.execute(req.path_params["name"], await _body(req), ctx)

    async def confirm(req):
        ctx = ToolContext(chat_id=f"app:{chat_id_of(req)}", services=agent.services)
        return await agent.executor.execute("confirm_action", {"confirmation_id": req.path_params["cid"]}, ctx)

    async def alerts(req):
        return agent.db.alerts()

    async def tasks(req):
        return agent.db.tasks()

    async def journal(req):
        try:
            limit = int(req.query_params.get("limit", 50))
        except ValueError:
            limit = 50
        return agent.db.journal_rows(limit)

    async def stream(ws: WebSocket):
        if token and ws.query_params.get("token", "") != token:
            await ws.close(code=4401)
            return
        await ws.accept()
        clients.add(ws)
        try:
            while True:
                msg = await ws.receive_text()  # the app can also chat over the socket
                try:
                    data = json.loads(msg)
                except ValueError:
                    data = {"message": msg}
                if isinstance(data, dict) and data.get("message"):
                    cid = f"app:{data.get('chat_id', 'android')}"
                    reply = await agent.chat(cid, data["message"])
                    await ws.send_text(json.dumps({"type": "reply", "chat_id": cid, "text": reply}))
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(ws)

    routes = [
        Route("/health", health),
        Route("/v1/chat", guarded(chat), methods=["POST"]),
        Route("/v1/overview", guarded(overview)),
        Route("/v1/market/{symbol}", guarded(market)),
        Route("/v1/analysis/{symbol}", guarded(analysis)),
        Route("/v1/tools", guarded(tools)),
        Route("/v1/tools/{name}", guarded(call_tool), methods=["POST"]),
        Route("/v1/confirm/{cid}", guarded(confirm), methods=["POST"]),
        Route("/v1/alerts", guarded(alerts)),
        Route("/v1/tasks", guarded(tasks)),
        Route("/v1/journal", guarded(journal)),
        WebSocketRoute("/v1/stream", stream),
    ]
    return Starlette(routes=routes)
