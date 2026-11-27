"""Tools that make J-Rock a general agent, all driven from chat:
settings (no .env needed), skills, MCP servers, custom Python tools, and coding (files, shell, git, tests, self-fix)."""
from __future__ import annotations

import asyncio
import base64
import fnmatch
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from ..agent.tools import Tool, _obj
from .store import KNOWN, NEEDS_RESTART, mask

S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}
O = {"type": "object"}
A = {"type": "array", "items": {"type": "string"}}

SELF_ROOT = str(Path(__file__).resolve().parents[2])      # the J-Rock repo (contains bitunix_agent/)
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "dist", "build", ".idea"}
MAX_OUT = 12000


def workspace_dir() -> str:
    d = os.path.expanduser(os.getenv("WORKSPACE_DIR") or "~/jrock-projects")
    os.makedirs(d, exist_ok=True)
    return d


def resolve(path: str | None) -> str:
    """Absolute paths as-is; 'self' or 'self/...' = J-Rock's own code; anything else is relative to the workspace."""
    p = (path or ".").strip()
    if p in ("self", "self/", "jrock", "j-rock") or p.startswith("self/"):
        return os.path.normpath(os.path.join(SELF_ROOT, p[5:] if p.startswith("self/") else ""))
    p = os.path.expanduser(p)
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(workspace_dir(), p))


def _tail(s: str, n: int = MAX_OUT) -> str:
    return s if len(s) <= n else f"[... {len(s) - n} chars cut ...]\n" + s[-n:]


def _git_env() -> dict:
    env = dict(os.environ)
    tok = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    env["GIT_TERMINAL_PROMPT"] = "0"
    if tok:
        env["GH_TOKEN"] = env["GITHUB_TOKEN"] = tok
    return env


def _git_auth_args() -> list[str]:
    tok = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not tok:
        return []
    basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
    return ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}"]


async def sh(cmd: str | list[str], cwd: str | None = None, timeout: float | None = None, env: dict | None = None) -> dict:
    timeout = timeout or float(os.getenv("SHELL_TIMEOUT") or 300)
    cwd = cwd or workspace_dir()
    os.makedirs(cwd, exist_ok=True)
    t0 = time.time()
    if isinstance(cmd, str):
        proc = await asyncio.create_subprocess_shell(cmd, cwd=cwd, env=env or _git_env(), stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.STDOUT)
    else:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, env=env or _git_env(), stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        code = proc.returncode
    except asyncio.TimeoutError:
        proc.kill()
        out, code = b"", None
        return {"exit_code": None, "timed_out": True, "seconds": round(time.time() - t0, 1),
                "next_step": f"Command ran longer than {timeout:.0f}s. Run it in the background (append ' > log.txt 2>&1 &') or raise SHELL_TIMEOUT."}
    text = out.decode(errors="replace")
    tok = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if tok:
        text = text.replace(tok, "***")
    return {"exit_code": code, "ok": code == 0, "output": _tail(text), "seconds": round(time.time() - t0, 1), "cwd": cwd}


def register(tools: dict[str, Tool], agent) -> None:
    def add(name, desc, params):
        def deco(fn):
            tools[name] = Tool(name, desc, params, fn, False)
            return fn
        return deco

    # ================================================================== settings (instead of .env)
    @add("get_settings", "Show every setting (secrets masked) and where it comes from. Settings changed in chat override .env.", _obj({}))
    async def _get(ctx):
        chat = agent.store.all()
        return {"settings": {k: {"value": mask(k, os.getenv(k)), "about": v, "set_in_chat": k in chat} for k, v in KNOWN.items()},
                "other_chat_settings": {k: mask(k, v) for k, v in chat.items() if k not in KNOWN}}

    @add("set_setting", "Change a setting from chat (no .env needed): API keys, LLM provider/model/key, DRY_RUN, risk limits, "
         "tokens, allowed users, GITHUB_TOKEN, WORKSPACE_DIR, any env var. Empty value removes it. Applied immediately; "
         "a few (bot tokens, proxies) need restart_agent.", _obj({"name": S, "value": S}, ["name"]))
    async def _set(ctx, name, value=""):
        name = name.strip().upper().replace(" ", "_")
        agent.store.set(name, value)
        err = agent.reload_settings(name)
        if err:
            return {"ok": False, "name": name, "saved": True, "error": err}
        restart = name in NEEDS_RESTART
        return {"ok": True, "name": name, "value": mask(name, value), "applied": not restart,
                "restart_needed": restart, **({"next_step": "Call restart_agent to apply it."} if restart else {})}

    @add("restart_agent", "Restart J-Rock (to apply new tokens or code changes). It comes back in a few seconds.", _obj({}))
    async def _restart(ctx):
        async def later():
            await asyncio.sleep(3)
            try:
                await agent.stop()
            except Exception:
                pass
            os.execv(sys.executable, [sys.executable, "-m", "bitunix_agent", *sys.argv[1:]])
        asyncio.get_running_loop().create_task(later())
        return {"ok": True, "message": "Restarting in 3 seconds. I'll be back online right after."}

    # ================================================================== skills
    @add("add_skill", "Teach J-Rock a new skill: a markdown playbook (strategy, workflow, rules, how-to). always=true puts it in "
         "every conversation; otherwise it's loaded with read_skill when relevant.",
         _obj({"name": S, "content": S, "description": S, "always": B}, ["name", "content"]))
    async def _add_skill(ctx, name, content, description="", always=False):
        return agent.skills.save(name, content, description, always)

    @add("install_skill_from_url", "Install a skill from a URL (raw markdown, a GitHub file link, a SKILL.md).", _obj({"url": S, "name": S}, ["url"]))
    async def _inst_skill(ctx, url, name=None):
        return await agent.skills.install_url(url, name)

    @add("list_skills", "List installed skills.", _obj({}))
    async def _list_skills(ctx):
        return agent.skills.list()

    @add("read_skill", "Load a skill's full text and follow it.", _obj({"name": S}, ["name"]))
    async def _read_skill(ctx, name):
        return agent.skills.read(name)

    @add("delete_skill", "Remove a skill.", _obj({"name": S}, ["name"]))
    async def _del_skill(ctx, name):
        return agent.skills.delete(name)

    # ================================================================== MCP
    @add("add_mcp_server", "Connect an MCP server so its tools become J-Rock's tools. Remote: give url (+ headers, e.g. "
         "{\"Authorization\": \"Bearer ...\"}). Local: give command + args (+ env), e.g. command='npx', "
         "args=['-y','@modelcontextprotocol/server-filesystem','/sdcard']. Saved and reconnected on every start.",
         _obj({"name": S, "url": S, "headers": O, "command": S, "args": A, "env": O, "cwd": S}, ["name"]))
    async def _add_mcp(ctx, name, url=None, headers=None, command=None, args=None, env=None, cwd=None):
        if not url and not command:
            return {"error": "give url (remote server) or command (local server)"}
        cfg = {"url": url, "headers": headers or {}} if url else {"command": command, "args": args or [], "env": env or {}, **({"cwd": cwd} if cwd else {})}
        res = await agent.mcp.add(name, cfg)
        agent.refresh_mcp_tools()
        return res

    @add("list_mcp_servers", "MCP servers, their connection state and tools.", _obj({}))
    async def _list_mcp(ctx):
        return agent.mcp.status()

    @add("remove_mcp_server", "Disconnect and delete an MCP server.", _obj({"name": S}, ["name"]))
    async def _rm_mcp(ctx, name):
        res = await agent.mcp.remove(name)
        agent.refresh_mcp_tools()
        return res

    @add("reconnect_mcp_server", "Reconnect an MCP server (after fixing it or if it dropped).", _obj({"name": S}, ["name"]))
    async def _re_mcp(ctx, name):
        s = agent.mcp.servers.get(name)
        if not s:
            return {"error": f"no server {name}", "servers": [x["name"] for x in agent.mcp.status()]}
        await s.close()
        try:
            await s.connect()
        except Exception as e:
            return {"ok": False, "error": s.error or str(e)}
        agent.refresh_mcp_tools()
        return {"ok": True, "tools": [t["name"] for t in s.tools]}

    # ================================================================== custom Python tools
    @add("create_tool", "Create a new permanent tool in Python. code must define `async def run(**kwargs)` (or a plain def) "
         "returning JSON-able data; parameters is a JSON schema object. It is saved, loaded now and on every start. "
         "Test it right after by calling it.",
         _obj({"name": S, "description": S, "parameters": O, "code": S}, ["name", "description", "code"]))
    async def _create_tool(ctx, name, description, code, parameters=None):
        return agent.custom_tools.save(name, description, parameters or {"type": "object", "properties": {}}, code)

    @add("list_custom_tools", "Custom tools created from chat.", _obj({}))
    async def _list_ct(ctx):
        return agent.custom_tools.list()

    @add("delete_tool", "Delete a custom tool.", _obj({"name": S}, ["name"]))
    async def _del_ct(ctx, name):
        return agent.custom_tools.delete(name)

    # ================================================================== coding
    @add("project_paths", "Where things are: J-Rock's own code ('self'), the workspace for other projects, Python version.", _obj({}))
    async def _paths(ctx):
        return {"self_code": SELF_ROOT, "workspace": workspace_dir(), "python": sys.version.split()[0], "platform": sys.platform,
                "is_git_repo": os.path.isdir(os.path.join(SELF_ROOT, ".git")), "github_token_set": bool(os.getenv("GITHUB_TOKEN")),
                "path_rules": "absolute paths as-is; 'self/...' = J-Rock's code; other relative paths = workspace",
                **({} if os.path.isdir(os.path.join(SELF_ROOT, ".git")) else {
                    "own_code_changes": "This copy is not a git repo (e.g. Railway). For permanent fixes to yourself: git_clone the "
                                        "GitHub repo you're deployed from, edit it there, check_code, git_commit_push (Railway redeploys). "
                                        "Ask the user for the repo URL if you don't know it, and remember it."})}

    @add("list_files", "List files in a folder (recursive). path: 'self' for J-Rock's code, a project folder, or absolute.",
         _obj({"path": S, "pattern": S, "max_files": I}))
    async def _ls(ctx, path=".", pattern="*", max_files=400):
        root = resolve(path)
        if os.path.isfile(root):
            return {"file": root, "size": os.path.getsize(root)}
        if not os.path.isdir(root):
            return {"error": f"not found: {root}", "workspace": workspace_dir()}
        out = []
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for f in sorted(files):
                if fnmatch.fnmatch(f, pattern):
                    p = os.path.join(d, f)
                    out.append(f"{os.path.relpath(p, root)}  ({os.path.getsize(p)} B)")
                    if len(out) >= max_files:
                        return {"root": root, "files": out, "truncated": True}
        return {"root": root, "files": out}

    @add("read_file", "Read a text file with line numbers (use start_line/end_line for big files).",
         _obj({"path": S, "start_line": I, "end_line": I}, ["path"]))
    async def _read(ctx, path, start_line=1, end_line=None):
        p = resolve(path)
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as e:
            return {"error": str(e)}
        s, e = max(1, int(start_line)), int(end_line) if end_line else len(lines)
        body = "".join(f"{i:>5}  {lines[i - 1]}" for i in range(s, min(e, len(lines)) + 1))
        if len(body) > 60000:
            body = body[:60000] + "\n[... cut: read a smaller line range ...]"
        return {"path": p, "total_lines": len(lines), "content": body}

    @add("write_file", "Create or overwrite a file (folders are created).", _obj({"path": S, "content": S}, ["path", "content"]))
    async def _write(ctx, path, content):
        p = resolve(path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "path": p, "bytes": len(content.encode())}

    @add("edit_file", "Replace exact text in a file. old_text must match exactly (copy it from read_file without the line numbers). "
         "Set replace_all for every occurrence.", _obj({"path": S, "old_text": S, "new_text": S, "replace_all": B}, ["path", "old_text", "new_text"]))
    async def _edit(ctx, path, old_text, new_text, replace_all=False):
        p = resolve(path)
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            return {"error": str(e)}
        n = text.count(old_text)
        if n == 0:
            near = [ln.strip() for ln in text.splitlines() if old_text.strip().splitlines()[0].strip()[:30] in ln][:5] if old_text.strip() else []
            return {"error": "old_text not found", "similar_lines": near, "next_step": "read_file the exact lines and retry."}
        if n > 1 and not replace_all:
            return {"error": f"old_text found {n} times", "next_step": "add more surrounding lines to make it unique, or set replace_all."}
        with open(p, "w", encoding="utf-8") as f:
            f.write(text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1))
        return {"ok": True, "path": p, "replacements": n if replace_all else 1}

    @add("search_code", "Search files for a regex (like grep -rn).", _obj({"pattern": S, "path": S, "file_glob": S}, ["pattern"]))
    async def _grep(ctx, pattern, path=".", file_glob="*"):
        root = resolve(path)
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        hits = []
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in files:
                if not fnmatch.fnmatch(f, file_glob):
                    continue
                p = os.path.join(d, f)
                try:
                    with open(p, encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh, 1):
                            if rx.search(line):
                                hits.append(f"{os.path.relpath(p, root)}:{i}: {line.rstrip()[:200]}")
                                if len(hits) >= 300:
                                    return {"root": root, "matches": hits, "truncated": True}
                except OSError:
                    continue
        return {"root": root, "matches": hits}

    @add("run_shell", "Run a shell command (bash) and get its output: install packages, run scripts/tests/linters, git, curl, "
         "build. cwd: 'self', a project folder or absolute path.", _obj({"command": S, "cwd": S, "timeout_seconds": N}, ["command"]))
    async def _shell(ctx, command, cwd=".", timeout_seconds=None):
        return await sh(command, resolve(cwd), timeout_seconds)

    @add("run_python", "Run a Python snippet in a fresh process (for analysis, quick scripts, data work). Print what you need.",
         _obj({"code": S, "cwd": S, "timeout_seconds": N}, ["code"]))
    async def _py(ctx, code, cwd=".", timeout_seconds=None):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            fn = f.name
        try:
            return await sh([sys.executable, fn], resolve(cwd), timeout_seconds)
        finally:
            os.unlink(fn)

    @add("git_clone", "Clone a git repo into the workspace (uses GITHUB_TOKEN for private GitHub repos).",
         _obj({"url": S, "folder": S}, ["url"]))
    async def _clone(ctx, url, folder=None):
        folder = folder or url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        dest = resolve(folder)
        if os.path.isdir(os.path.join(dest, ".git")):
            res = await sh(["git", *_git_auth_args(), "pull", "--ff-only"], dest)
            return {**res, "path": dest, "note": "already cloned; pulled latest"}
        res = await sh(["git", *_git_auth_args(), "clone", url, dest], workspace_dir())
        return {**res, "path": dest}

    @add("git_commit_push", "Commit all changes in a repo and push (GITHUB_TOKEN used for GitHub). On Railway this redeploys J-Rock.",
         _obj({"path": S, "message": S, "branch": S}, ["path", "message"]))
    async def _push(ctx, path, message, branch=None):
        repo = resolve(path)
        steps = []
        for cmd in (["git", "config", "user.email"], ["git", "config", "user.name"]):
            r = await sh(cmd, repo)
            if not (r.get("output") or "").strip():
                await sh(cmd + (["j-rock@agent.local"] if cmd[-1] == "user.email" else ["J-Rock"]), repo)
        steps.append(await sh(["git", "add", "-A"], repo))
        c = await sh(["git", "commit", "-m", message], repo)
        steps.append(c)
        p = await sh(["git", *_git_auth_args(), "push", *(["origin", branch] if branch else [])], repo)
        steps.append(p)
        return {"ok": p.get("exit_code") == 0, "commit": c.get("output", "")[-500:], "push": p.get("output", "")[-1500:],
                **({} if p.get("exit_code") == 0 else {"next_step": "If auth failed: set_setting GITHUB_TOKEN. If rejected: git pull --rebase first."})}

    @add("check_code", "Check a Python project for errors: compile every file, import-check, and run pytest if it exists. "
         "Use after every code change (path 'self' = J-Rock).", _obj({"path": S}))
    async def _check(ctx, path="self"):
        root = resolve(path)
        out = {"compile": await sh([sys.executable, "-m", "compileall", "-q", "-f", "."], root, 300)}
        if root == SELF_ROOT:
            out["import"] = await sh([sys.executable, "-c", "import bitunix_agent.agent.core, bitunix_agent.__main__; print('imports OK')"], root, 120)
        has_tests = os.path.isdir(os.path.join(root, "tests")) or any(f.startswith("test_") for f in os.listdir(root) if f.endswith(".py"))
        if has_tests:
            out["pytest"] = await sh([sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"], root, 600,
                                  {**_git_env(), "PYTHONDONTWRITEBYTECODE": "1"})
            if "No module named pytest" in (out["pytest"].get("output") or ""):
                out["pytest"] = {"skipped": "pytest not installed (pip install pytest pytest-asyncio)"}
        out["ok"] = all(v.get("exit_code") in (0, None) and not v.get("timed_out") for v in out.values() if isinstance(v, dict) and "exit_code" in v)
        return out

    @add("code_review", "Collect what's needed to review a project: file list, git status/diff and recent log. Then read the key "
         "files and give a review (bugs, security, clarity, tests) with concrete fixes.", _obj({"path": S}))
    async def _review(ctx, path="self"):
        root = resolve(path)
        files = await tools["list_files"].fn(ctx, path=root, pattern="*", max_files=200)
        git = {}
        if os.path.isdir(os.path.join(root, ".git")):
            git = {"status": (await sh(["git", "status", "--short"], root))["output"],
                   "diff": (await sh(["git", "diff", "--stat"], root))["output"],
                   "log": (await sh(["git", "log", "--oneline", "-15"], root))["output"]}
        return {"root": root, "files": files.get("files"), "git": git,
                "next_step": "read_file the important files (entry points, the code the user asked about), then review."}


class CustomTools:
    """Python tools the agent writes for itself. Saved in data/custom_tools/<name>.py with a JSON header."""

    def __init__(self, folder: str, tools: dict[str, Tool]):
        self.dir, self.tools = folder, tools
        os.makedirs(folder, exist_ok=True)

    def _path(self, name):
        return os.path.join(self.dir, f"{name}.py")

    def save(self, name: str, description: str, parameters: dict, code: str) -> dict:
        name = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip().lower())[:48]
        header = "# JROCK_TOOL " + json.dumps({"name": name, "description": description, "parameters": parameters}) + "\n"
        with open(self._path(name), "w", encoding="utf-8") as f:
            f.write(header + code)
        try:
            self._load(self._path(name))
        except Exception as e:
            return {"ok": False, "saved": True, "error": f"{type(e).__name__}: {e}", "next_step": "Fix the code and call create_tool again."}
        return {"ok": True, "tool": name, "next_step": f"Call {name} once to test it."}

    def _load(self, path: str):
        with open(path, encoding="utf-8") as f:
            first = f.readline()
        meta = json.loads(first.removeprefix("# JROCK_TOOL ").strip())
        spec = importlib.util.spec_from_file_location(f"jrock_tool_{meta['name']}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, "run", None)
        if not callable(fn):
            raise ValueError("code must define run(**kwargs)")

        async def call(ctx, **kw):
            r = fn(**kw)
            if asyncio.iscoroutine(r):
                r = await r
            return r if isinstance(r, (dict, list)) else {"result": r}
        params = meta.get("parameters") or {"type": "object", "properties": {}}
        params.setdefault("type", "object")
        params.setdefault("properties", {})
        self.tools[meta["name"]] = Tool(meta["name"], "[custom] " + meta.get("description", ""), params, call, False)

    def load_all(self):
        for fn in sorted(os.listdir(self.dir)):
            if fn.endswith(".py"):
                try:
                    self._load(os.path.join(self.dir, fn))
                except Exception as e:  # a broken custom tool never stops the agent
                    print(f"Custom tool {fn} failed to load: {e}")

    def list(self) -> list[dict]:
        out = []
        for fn in sorted(os.listdir(self.dir)):
            if fn.endswith(".py"):
                n = fn[:-3]
                out.append({"name": n, "loaded": n in self.tools, "file": os.path.join(self.dir, fn)})
        return out

    def delete(self, name: str) -> dict:
        p = self._path(name)
        if os.path.exists(p):
            os.remove(p)
        self.tools.pop(name, None)
        return {"ok": True}
