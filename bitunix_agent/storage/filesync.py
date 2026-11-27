"""Keep J-Rock's files (skills, MCP servers, custom tools) in the database when DATABASE_URL is set.

Hosts like Railway wipe the disk on every redeploy. With Postgres configured, these files are restored from the
kv_files table at startup and every change is copied back every few seconds, so nothing J-Rock learns is lost.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from .backend import connect, pg_active

log = logging.getLogger("bitunix.filesync")

SYNCED = ("skills", "custom_tools", "mcp.json")  # inside the data folder
MAX_BYTES = 2_000_000


def _walk(data_dir: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in SYNCED:
        p = os.path.join(data_dir, name)
        if os.path.isfile(p):
            out[name] = p
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    full = os.path.join(root, f)
                    out[os.path.relpath(full, data_dir).replace(os.sep, "/")] = full
    return out


def _wants_pg(db_path: str) -> bool:
    from .backend import use_postgres
    return use_postgres(db_path)


class FileSync:
    def __init__(self, db_path: str, data_dir: str):
        self.data_dir = data_dir
        self.conn = connect(db_path) if pg_active(db_path) or _wants_pg(db_path) else None
        self.enabled = pg_active(db_path)
        self._seen: dict[str, float] = {}
        if self.enabled:
            self.conn.execute("CREATE TABLE IF NOT EXISTS kv_files (path TEXT PRIMARY KEY, content TEXT, ts REAL)")

    def restore(self) -> int:
        """Write every stored file to disk (database wins over whatever the image shipped)."""
        if not self.enabled:
            return 0
        n = 0
        restored: set[str] = set()
        for r in self.conn.execute("SELECT path, content FROM kv_files"):
            rel = r["path"]
            if rel.startswith("/") or ".." in rel.split("/"):
                continue
            full = os.path.join(self.data_dir, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(r["content"])
            restored.add(rel)
            n += 1
        # Files already on disk but not in the database are uploaded by the next sync.
        self._seen = {rel: os.path.getmtime(p) for rel, p in _walk(self.data_dir).items() if rel in restored}
        if n:
            log.info("restored %d files from the database", n)
        return n

    def sync_once(self) -> int:
        """Copy new/changed files to the database and drop deleted ones. Returns how many changed."""
        if not self.enabled:
            return 0
        now = _walk(self.data_dir)
        changed = 0
        for rel, full in now.items():
            try:
                m = os.path.getmtime(full)
                if self._seen.get(rel) == m or os.path.getsize(full) > MAX_BYTES:
                    continue
                with open(full, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            self.conn.execute("INSERT OR REPLACE INTO kv_files VALUES (?,?,?)", (rel, content, time.time()))
            self._seen[rel] = m
            changed += 1
        for rel in [r for r in self._seen if r not in now]:
            self.conn.execute("DELETE FROM kv_files WHERE path=?", (rel,))
            self._seen.pop(rel, None)
            changed += 1
        return changed

    async def run(self, every: float = 5.0):
        while self.enabled:
            try:
                await asyncio.to_thread(self.sync_once)
            except Exception as e:  # never crash the agent over a sync hiccup
                log.warning("file sync failed: %s", e)
            await asyncio.sleep(every)
