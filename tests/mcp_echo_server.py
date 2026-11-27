"""Tiny MCP server over stdio for tests (newline-delimited JSON-RPC)."""
import json
import sys

TOOLS = [{"name": "add", "description": "add two numbers",
          "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}}]

for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": m["params"]["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "echo", "version": "1"}}
    elif m["method"] == "tools/list":
        r = {"tools": TOOLS}
    elif m["method"] == "tools/call":
        a = m["params"]["arguments"]
        r = {"content": [{"type": "text", "text": str(a["a"] + a["b"])}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "no method"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
