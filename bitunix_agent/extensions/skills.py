"""Skills: markdown playbooks the user teaches J-Rock from chat. Stored as files in data/skills/<name>.md.
The system prompt lists every skill (name + description); the model loads the full text with read_skill when relevant.
A skill marked always=true is injected into every prompt."""
from __future__ import annotations

import os
import re
import time

import httpx


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return s or f"skill-{int(time.time())}"


class Skills:
    def __init__(self, folder: str):
        self.dir = folder
        os.makedirs(folder, exist_ok=True)

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, _slug(name) + ".md")

    def save(self, name: str, content: str, description: str = "", always: bool = False) -> dict:
        name = _slug(name)
        if not content.lstrip().startswith("---"):
            desc = (description or content.strip().splitlines()[0] if content.strip() else name)[:200].replace("\n", " ")
            content = f"---\nname: {name}\ndescription: {desc}\nalways: {str(bool(always)).lower()}\n---\n\n{content.strip()}\n"
        with open(self._path(name), "w", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "skill": name, "path": self._path(name)}

    def _meta(self, text: str) -> dict:
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
        meta = {}
        body = text
        if m:
            body = m.group(2)
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip().strip("\"'")
        return {"meta": meta, "body": body}

    def list(self) -> list[dict]:
        out = []
        for fn in sorted(os.listdir(self.dir)):
            if not fn.endswith(".md"):
                continue
            with open(os.path.join(self.dir, fn), encoding="utf-8") as f:
                p = self._meta(f.read())
            out.append({"name": fn[:-3], "description": p["meta"].get("description", ""),
                        "always": p["meta"].get("always", "false").lower() == "true"})
        return out

    def read(self, name: str) -> dict:
        path = self._path(name)
        if not os.path.exists(path):
            return {"error": f"no skill '{name}'", "skills": [s["name"] for s in self.list()]}
        with open(path, encoding="utf-8") as f:
            text = f.read()
        return {"skill": _slug(name), "content": text}

    def delete(self, name: str) -> dict:
        path = self._path(name)
        if os.path.exists(path):
            os.remove(path)
            return {"ok": True}
        return {"ok": False, "error": f"no skill '{name}'", "skills": [s["name"] for s in self.list()]}

    async def install_url(self, url: str, name: str | None = None) -> dict:
        # GitHub page links -> raw file
        m = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/(.+)", url)
        if m:
            url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
        text = r.text
        if not name:
            nm = re.search(r"^name:\s*(.+)$", text, re.M)
            name = nm.group(1).strip() if nm else url.rstrip("/").rsplit("/", 1)[-1].replace(".md", "")
        return self.save(name, text)

    def prompt_block(self) -> str:
        items = self.list()
        if not items:
            return "(no skills yet; the user can teach you with add_skill)"
        lines = [f"- {s['name']}: {s['description']}" for s in items]
        always = []
        for s in items:
            if s["always"]:
                always.append(f"\n### Skill: {s['name']}\n" + self._meta(self.read(s["name"])["content"])["body"][:6000])
        return "\n".join(lines) + ("\n\nALWAYS-ON SKILLS:" + "".join(always) if always else "")
