"""J-Rock as a general agent: settings from chat, skills, MCP (stdio + streamable HTTP), custom tools, coding tools."""
import asyncio
import json
import os
import socket
import sys

import pytest

from bitunix_agent.agent.core import Agent, _textual_tool_calls, clean_reply
from bitunix_agent.agent.tools import ToolContext
from bitunix_agent.config import Settings
from tests.test_engine_and_agent import ScriptedLLM

HERE = os.path.dirname(__file__)


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "ws"))
    for k in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    saved = dict(os.environ)
    a = Agent(Settings(db_path=str(tmp_path / "data" / "agent.db"), llm_api_key="", dry_run=True))
    yield a
    os.environ.clear()
    os.environ.update(saved)


async def call(agent, tool_name, **args):
    return await agent.executor.execute(tool_name, args, ToolContext(chat_id="t", services=agent.services))


@pytest.mark.asyncio
async def test_set_llm_from_chat_without_llm(agent, monkeypatch):
    r = await agent.chat("t", "hi")
    assert "set LLM_API_KEY" in r
    assert "Saved LLM_PROVIDER" in await agent.chat("t", "set LLM_PROVIDER=deepseek")
    r = await agent.chat("t", "set LLM_API_KEY = sk-1234567890abcdef")
    assert "Brain online" in r and "sk-1234567890abcdef" not in r
    assert agent.llm and agent.llm.provider == "deepseek"
    assert agent.store.get("LLM_API_KEY") == "sk-1234567890abcdef"
    # natural language "set leverage 10" must go to the LLM, not the settings parser
    assert await agent._local_command("set leverage 10") is None
    await agent.stop()


@pytest.mark.asyncio
async def test_settings_tool_applies_live(agent):
    r = await call(agent, "set_setting", name="dry_run", value="false")
    assert r["ok"] and agent.rest.dry_run is False and agent.settings.dry_run is False
    r = await call(agent, "set_setting", name="RISK_MAX_LEVERAGE", value="20x")
    assert agent.settings.max_leverage == 20
    g = await call(agent, "get_settings")
    assert g["settings"]["RISK_MAX_LEVERAGE"]["set_in_chat"]
    await agent.stop()


@pytest.mark.asyncio
async def test_skills(agent):
    r = await call(agent, "add_skill", name="Scalp Rules", content="Only trade 5m breakouts.\nAlways SL 0.5%.", always=True)
    assert r["ok"]
    assert (await call(agent, "list_skills"))[0]["name"] == "scalp-rules"
    assert "breakouts" in (await call(agent, "read_skill", name="scalp-rules"))["content"]
    assert "Always SL 0.5%" in agent._system()
    assert (await call(agent, "delete_skill", name="scalp-rules"))["ok"]
    await agent.stop()


@pytest.mark.asyncio
async def test_mcp_stdio_server(agent):
    r = await call(agent, "add_mcp_server", name="echo", command=sys.executable, args=[os.path.join(HERE, "mcp_echo_server.py")])
    assert r["ok"], r
    assert "mcp_echo_add" in agent.tools
    out = await call(agent, "mcp_echo_add", a=2, b=40)
    assert out["result"] == "42"
    st = await call(agent, "list_mcp_servers")
    assert st[0]["connected"] and st[0]["tools"] == ["add"]
    assert (await call(agent, "remove_mcp_server", name="echo"))["ok"] and "mcp_echo_add" not in agent.tools
    await agent.stop()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.mark.asyncio
async def test_mcp_streamable_http_server(agent, monkeypatch):
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response, StreamingResponse
    from starlette.routing import Route
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    async def mcp(req):
        m = await req.json()
        assert req.headers.get("authorization") == "Bearer x"
        if "id" not in m:
            return Response(status_code=202)
        if m["method"] == "initialize":
            return JSONResponse({"jsonrpc": "2.0", "id": m["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {}}},
                                headers={"Mcp-Session-Id": "s1"})
        assert req.headers.get("mcp-session-id") == "s1"
        if m["method"] == "tools/list":
            res = {"tools": [{"name": "ping", "description": "pong", "inputSchema": {"type": "object"}}]}
        else:
            res = {"content": [{"type": "text", "text": "pong"}]}
        body = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': res})}\n\n"
        return StreamingResponse(iter([body]), media_type="text/event-stream")

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(Starlette(routes=[Route("/mcp", mcp, methods=["POST"])]), host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        r = await call(agent, "add_mcp_server", name="remote", url=f"http://127.0.0.1:{port}/mcp", headers={"Authorization": "Bearer x"})
        assert r["ok"], r
        assert (await call(agent, "mcp_remote_ping"))["result"] == "pong"
    finally:
        await agent.stop()
        server.should_exit = True
        await task


@pytest.mark.asyncio
async def test_custom_tool(agent):
    code = "async def run(x: float = 1):\n    return {'double': x * 2}\n"
    r = await call(agent, "create_tool", name="doubler", description="double a number",
                   parameters={"type": "object", "properties": {"x": {"type": "number"}}}, code=code)
    assert r["ok"], r
    assert (await call(agent, "doubler", x=21))["double"] == 42
    bad = await call(agent, "create_tool", name="broken", description="x", code="def nope(): pass")
    assert bad["ok"] is False and "run" in bad["error"]
    await agent.stop()


@pytest.mark.asyncio
async def test_coding_tools_fix_a_project(agent):
    await call(agent, "write_file", path="proj/calc.py", content="def add(a, b):\n    return a - b\n")
    await call(agent, "write_file", path="proj/tests/test_calc.py",
               content="import sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\nfrom calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    bad = await call(agent, "check_code", path="proj")
    assert bad["ok"] is False
    hits = await call(agent, "search_code", pattern=r"return a - b", path="proj")
    assert hits["matches"] and "calc.py:2" in hits["matches"][0]
    assert (await call(agent, "edit_file", path="proj/calc.py", old_text="a - b", new_text="a + b"))["ok"]
    good = await call(agent, "check_code", path="proj")
    assert good["ok"], good
    assert "    2  " in (await call(agent, "read_file", path="proj/calc.py"))["content"]
    sh = await call(agent, "run_shell", command="echo hello && exit 3", cwd="proj")
    assert sh["exit_code"] == 3 and "hello" in sh["output"]
    py = await call(agent, "run_python", code="print(6*7)")
    assert py["output"].strip() == "42"
    ls = await call(agent, "list_files", path="proj")
    assert any("calc.py" in f for f in ls["files"])
    paths = await call(agent, "project_paths")
    assert os.path.isdir(os.path.join(paths["self_code"], "bitunix_agent"))
    await agent.stop()


@pytest.mark.asyncio
async def test_git_commit_push_to_local_remote(agent, tmp_path):
    import subprocess
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    r = await call(agent, "git_clone", url=str(remote), folder="repo")
    assert r["exit_code"] == 0, r
    await call(agent, "write_file", path="repo/a.txt", content="hi")
    p = await call(agent, "git_commit_push", path="repo", message="add a", branch="HEAD:main")
    assert p["ok"], p
    log = subprocess.run(["git", "--git-dir", str(remote), "log", "--oneline", "main"], capture_output=True, text=True).stdout
    assert "add a" in log
    await agent.stop()


@pytest.mark.asyncio
async def test_textual_tool_calls_are_executed_and_replies_clean(agent):
    text = '<tool_call>{"name": "calculator", "arguments": {"expression": "2+2"}}</tool_call>'
    assert _textual_tool_calls(text)[0]["name"] == "calculator"
    seen = {}

    def grab(messages):
        seen["tool"] = json.loads(messages[-1]["content"])
        return {"expression": "1"}

    class LLM(ScriptedLLM):
        pass
    agent.llm = LLM([text, ("calculator", grab), "The answer is 4.\nnull"])
    reply = await agent.chat("t", "2+2?")
    assert seen["tool"]["result"] == 4
    assert reply == "The answer is 4."
    assert clean_reply("null") == "" and clean_reply("ok <tool_call>{}</tool_call>") == "ok"
    await agent.stop()
