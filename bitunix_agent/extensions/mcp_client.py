"""Minimal MCP (Model Context Protocol) client in pure Python (no SDK, nothing to compile: works on Termux).

Transports:
- Streamable HTTP  {"url": "https://host/mcp", "headers": {"Authorization": "Bearer ..."}}
- Legacy HTTP+SSE  {"url": "https://host/sse"}            (auto-detected)
- stdio            {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/sdcard"], "env": {...}}

Servers live in data/mcp.json and are added/removed from chat. Their tools appear to the model as mcp_<server>_<tool>.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
from typing import Any

import httpx

log = logging.getLogger("bitunix.mcp")
PROTOCOL = "2025-06-18"
CLIENT_INFO = {"name": "j-rock", "version": "1.0"}


class MCPError(Exception):
    pass


def _tool_name(server: str, tool: str) -> str:
    n = re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp_{server}_{tool}")
    return n[:64]


class MCPServer:
    def __init__(self, name: str, cfg: dict):
        self.name, self.cfg = name, cfg
        self.tools: list[dict] = []
        self.error: str | None = None
        self.connected = False
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()
        # http
        self._http: httpx.AsyncClient | None = None
        self._session: str | None = None
        self._legacy_post: str | None = None
        self._sse_task: asyncio.Task | None = None
        # stdio / legacy sse
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None

    @property
    def kind(self) -> str:
        return "stdio" if self.cfg.get("command") else "http"

    # ------------------------------------------------------------------ connect
    async def connect(self, timeout: float = 60):
        try:
            await asyncio.wait_for(self._connect(), timeout)
            self.connected, self.error = True, None
        except Exception as e:
            self.connected = False
            self.error = f"{type(e).__name__}: {e}"
            await self.close()
            raise

    async def _connect(self):
        if self.kind == "stdio":
            env = {**os.environ, **{k: str(v) for k, v in (self.cfg.get("env") or {}).items()}}
            self._proc = await asyncio.create_subprocess_exec(
                self.cfg["command"], *[str(a) for a in self.cfg.get("args") or []],
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env, cwd=self.cfg.get("cwd") or None, limit=16 * 1024 * 1024)
            self._reader = asyncio.create_task(self._read_stdio())
        else:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=20), follow_redirects=True,
                                           headers={k: str(v) for k, v in (self.cfg.get("headers") or {}).items()})
        init = {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT_INFO}
        try:
            await self.request("initialize", init)
        except MCPError as e:
            if self.kind == "http" and ("405" in str(e) or "404" in str(e) or "400" in str(e)) and not self._legacy_post:
                await self._start_legacy_sse()
                await self.request("initialize", init)
            else:
                raise
        await self.notify("notifications/initialized")
        self.tools, cursor = [], None
        while True:
            res = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            self.tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                break

    # ------------------------------------------------------------------ JSON-RPC
    async def request(self, method: str, params: dict | None = None, timeout: float = 120) -> Any:
        rid = next(self._ids)
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        if self.kind == "stdio" or self._legacy_post:
            fut = asyncio.get_running_loop().create_future()
            self._pending[rid] = fut
            await self._send(msg)
            try:
                resp = await asyncio.wait_for(fut, timeout)
            finally:
                self._pending.pop(rid, None)
        else:
            resp = await self._post_streamable(msg, rid)
        if "error" in resp:
            e = resp["error"]
            raise MCPError(f"{e.get('code')}: {e.get('message')} {json.dumps(e.get('data'))[:300] if e.get('data') else ''}")
        return resp.get("result", {})

    async def notify(self, method: str, params: dict | None = None):
        msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})}
        if self.kind == "stdio" or self._legacy_post:
            await self._send(msg)
        else:
            await self._http.post(self.cfg["url"], json=msg, headers=self._headers())

    def _headers(self) -> dict:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "MCP-Protocol-Version": PROTOCOL}
        if self._session:
            h["Mcp-Session-Id"] = self._session
        return h

    async def _post_streamable(self, msg: dict, rid: int) -> dict:
        async with self._http.stream("POST", self.cfg["url"], json=msg, headers=self._headers()) as r:
            if r.headers.get("mcp-session-id"):
                self._session = r.headers["mcp-session-id"]
            if r.status_code >= 400:
                body = (await r.aread()).decode(errors="replace")[:300]
                raise MCPError(f"HTTP {r.status_code}: {body}")
            ctype = r.headers.get("content-type", "")
            if "text/event-stream" in ctype:
                data_lines: list[str] = []
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif line == "" and data_lines:
                        try:
                            m = json.loads("\n".join(data_lines))
                        except ValueError:
                            m = None
                        data_lines = []
                        if isinstance(m, dict) and m.get("id") == rid:
                            return m
                raise MCPError("stream ended without a response")
            return json.loads(await r.aread())

    async def _send(self, msg: dict):
        if self._legacy_post:
            r = await self._http.post(self._legacy_post, json=msg, headers={"Content-Type": "application/json"})
            if r.status_code >= 400:
                raise MCPError(f"HTTP {r.status_code}: {r.text[:300]}")
            return
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()

    def _dispatch(self, m: Any):
        if isinstance(m, dict) and "id" in m and ("result" in m or "error" in m):
            fut = self._pending.get(m["id"])
            if fut and not fut.done():
                fut.set_result(m)
        elif isinstance(m, dict) and m.get("method") == "ping" and "id" in m:
            asyncio.create_task(self._send({"jsonrpc": "2.0", "id": m["id"], "result": {}}))

    async def _read_stdio(self):
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    self._dispatch(json.loads(line))
                except ValueError:
                    log.debug("mcp %s non-json: %s", self.name, line[:200])
        finally:
            err = b""
            try:
                err = await asyncio.wait_for(self._proc.stderr.read(2000), 1)
            except Exception:
                pass
            for f in self._pending.values():
                if not f.done():
                    f.set_exception(MCPError(f"server exited. {err.decode(errors='replace')[-500:]}"))
            self.connected = False

    async def _start_legacy_sse(self):
        """Old HTTP+SSE transport: GET the SSE url, wait for the 'endpoint' event, POST messages there."""
        got = asyncio.get_running_loop().create_future()

        async def reader():
            try:
                async with self._http.stream("GET", self.cfg["url"], headers={"Accept": "text/event-stream"}) as r:
                    event, data = "message", []
                    async for line in r.aiter_lines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        elif line == "":
                            payload = "\n".join(data)
                            data = []
                            if event == "endpoint" and not got.done():
                                got.set_result(str(httpx.URL(self.cfg["url"]).join(payload)))
                            elif payload:
                                try:
                                    self._dispatch(json.loads(payload))
                                except ValueError:
                                    pass
                            event = "message"
            except Exception as e:
                if not got.done():
                    got.set_exception(e)
        self._sse_task = asyncio.create_task(reader())
        self._legacy_post = await asyncio.wait_for(got, 30)

    # ------------------------------------------------------------------ tools
    async def call_tool(self, tool: str, args: dict) -> Any:
        if not self.connected:
            await self.connect()
        res = await self.request("tools/call", {"name": tool, "arguments": args or {}})
        parts = []
        for c in res.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "resource":
                parts.append(json.dumps(c.get("resource"))[:4000])
            else:
                parts.append(f"[{c.get('type')} content]")
        out: dict[str, Any] = {"result": "\n".join(parts)}
        if res.get("structuredContent") is not None:
            out["structured"] = res["structuredContent"]
        if res.get("isError"):
            out["is_error"] = True
            out["next_step"] = "The MCP tool reported an error. Fix the arguments or try another approach."
        return out

    async def close(self):
        for t in (self._reader, self._sse_task):
            if t:
                t.cancel()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        if self._http:
            await self._http.aclose()
        self._proc = self._http = self._reader = self._sse_task = None
        self._legacy_post = self._session = None
        self.connected = False


class MCPManager:
    def __init__(self, config_path: str):
        self.path = config_path
        self.servers: dict[str, MCPServer] = {}
        os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f).get("servers", {})
        except (OSError, ValueError):
            return {}

    def _save(self, servers: dict):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"servers": servers}, f, indent=2)

    async def start_all(self):
        for name, cfg in self._load().items():
            if cfg.get("disabled"):
                continue
            s = MCPServer(name, cfg)
            self.servers[name] = s
            try:
                await s.connect()
                log.info("MCP %s: %d tools", name, len(s.tools))
            except Exception as e:
                log.warning("MCP %s failed: %s", name, e)

    async def add(self, name: str, cfg: dict) -> dict:
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", name.strip().lower())[:24] or "server"
        if name in self.servers:
            await self.servers[name].close()
        s = MCPServer(name, cfg)
        self.servers[name] = s
        all_cfg = self._load()
        all_cfg[name] = cfg
        self._save(all_cfg)
        try:
            await s.connect()
        except Exception as e:
            return {"ok": False, "server": name, "saved": True, "error": s.error or str(e),
                    "next_step": "Check the url/command/headers. For stdio servers the command must exist on this machine "
                                 "(e.g. pkg install nodejs for npx, pip install for python servers). Fix it and call add_mcp_server again."}
        return {"ok": True, "server": name, "tools": [t["name"] for t in s.tools]}

    async def remove(self, name: str) -> dict:
        s = self.servers.pop(name, None)
        if s:
            await s.close()
        all_cfg = self._load()
        existed = all_cfg.pop(name, None) is not None
        self._save(all_cfg)
        return {"ok": existed or bool(s)}

    def status(self) -> list[dict]:
        cfg = self._load()
        out = []
        for name, c in cfg.items():
            s = self.servers.get(name)
            out.append({"name": name, "transport": "stdio" if c.get("command") else "http",
                        "target": c.get("url") or " ".join([c.get("command", "")] + [str(a) for a in c.get("args", [])]),
                        "connected": bool(s and s.connected), "tools": [t["name"] for t in (s.tools if s else [])],
                        "error": s.error if s else None})
        return out

    def tool_specs(self) -> list[tuple[str, str, str, dict]]:
        """(agent_tool_name, server, mcp_tool_name, schema) for every connected tool."""
        out = []
        for name, s in self.servers.items():
            for t in s.tools:
                schema = t.get("inputSchema") or {"type": "object", "properties": {}}
                schema.setdefault("type", "object")
                schema.setdefault("properties", {})
                out.append((_tool_name(name, t["name"]), name, t["name"], schema,
                            f"[MCP {name}] {t.get('description') or t['name']}"))
        return out

    async def close(self):
        for s in self.servers.values():
            await s.close()
