"""Persistence (SQLite file, or Postgres/Neon when DATABASE_URL is set): chat memory, trade journal, alerts, scheduled tasks, confirmations, trailing stops, notes."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .backend import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, content TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS ix_messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, chat_id TEXT, action TEXT, request TEXT, response TEXT, ok INTEGER);
CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, chat_id TEXT, symbol TEXT, kind TEXT, op TEXT, value REAL, note TEXT, repeat INTEGER, active INTEGER, created REAL, last_fired REAL);
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, chat_id TEXT, every_sec INTEGER, prompt TEXT, next_run REAL, active INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS confirmations (id TEXT PRIMARY KEY, chat_id TEXT, tool TEXT, args TEXT, summary TEXT, created REAL, used INTEGER);
CREATE TABLE IF NOT EXISTS trailing (position_id TEXT PRIMARY KEY, chat_id TEXT, symbol TEXT, side TEXT, callback_pct REAL, activation_price REAL, best_price REAL, active INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS notes (key TEXT PRIMARY KEY, value TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS chats (chat_id TEXT PRIMARY KEY, channel TEXT, address TEXT, ts REAL);
"""


class DB:
    def __init__(self, path: str):
        self.conn = connect(path)
        self.conn.script(SCHEMA)

    def _x(self, sql: str, args: tuple = ()) -> list:
        return self.conn.execute(sql, args)

    # ---------------------------------------------------------------- chat memory
    def add_message(self, chat_id: str, msg: dict):
        self._x("INSERT INTO messages(chat_id, role, content, ts) VALUES (?,?,?,?)",
                (chat_id, msg.get("role", ""), json.dumps(msg, default=str), time.time()))

    def history(self, chat_id: str, limit: int = 40) -> list[dict]:
        rows = self._x("SELECT content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit))
        msgs = [json.loads(r["content"]) for r in reversed(rows)]
        # never start with a dangling tool result / tool call
        while msgs and msgs[0].get("role") in ("tool",):
            msgs.pop(0)
        return msgs

    def clear_history(self, chat_id: str):
        self._x("DELETE FROM messages WHERE chat_id=?", (chat_id,))

    # ---------------------------------------------------------------- chats (where to push notifications)
    def register_chat(self, chat_id: str, channel: str, address: str):
        self._x("INSERT OR REPLACE INTO chats(chat_id, channel, address, ts) VALUES (?,?,?,?)", (chat_id, channel, address, time.time()))

    def chats(self) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM chats")]

    # ---------------------------------------------------------------- journal
    def journal(self, chat_id: str, action: str, request: Any, response: Any, ok: bool):
        self._x("INSERT INTO journal(ts, chat_id, action, request, response, ok) VALUES (?,?,?,?,?,?)",
                (time.time(), chat_id, action, json.dumps(request, default=str), json.dumps(response, default=str)[:20000], int(ok)))

    def journal_rows(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,))]

    # ---------------------------------------------------------------- alerts
    def add_alert(self, chat_id, symbol, kind, op, value, note="", repeat=False) -> str:
        aid = uuid.uuid4().hex[:8]
        self._x("INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?)", (aid, chat_id, symbol, kind, op, float(value), note, int(repeat), 1, time.time(), None))
        return aid

    def alerts(self, active_only=True, chat_id: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM alerts WHERE 1=1", []
        if active_only:
            q += " AND active=1"
        if chat_id:
            q += " AND chat_id=?"; a.append(chat_id)
        return [dict(r) for r in self._x(q, tuple(a))]

    def fire_alert(self, aid: str, repeat: bool):
        self._x("UPDATE alerts SET last_fired=?, active=? WHERE id=?", (time.time(), 1 if repeat else 0, aid))

    def delete_alert(self, aid: str):
        self._x("UPDATE alerts SET active=0 WHERE id=?", (aid,))

    # ---------------------------------------------------------------- scheduled tasks
    def add_task(self, chat_id, every_sec, prompt, first_run: float | None = None) -> str:
        tid = uuid.uuid4().hex[:8]
        self._x("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", (tid, chat_id, int(every_sec), prompt, first_run or time.time() + every_sec, 1, time.time()))
        return tid

    def due_tasks(self) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM tasks WHERE active=1 AND next_run<=?", (time.time(),))]

    def tasks(self, chat_id: str | None = None) -> list[dict]:
        if chat_id:
            return [dict(r) for r in self._x("SELECT * FROM tasks WHERE active=1 AND chat_id=?", (chat_id,))]
        return [dict(r) for r in self._x("SELECT * FROM tasks WHERE active=1")]

    def bump_task(self, tid: str, every_sec: int):
        self._x("UPDATE tasks SET next_run=? WHERE id=?", (time.time() + every_sec, tid))

    def delete_task(self, tid: str):
        self._x("UPDATE tasks SET active=0 WHERE id=?", (tid,))

    # ---------------------------------------------------------------- confirmations
    def add_confirmation(self, chat_id, tool, args, summary) -> str:
        cid = uuid.uuid4().hex[:6]
        self._x("INSERT INTO confirmations VALUES (?,?,?,?,?,?,?)", (cid, chat_id, tool, json.dumps(args, default=str), summary, time.time(), 0))
        return cid

    def take_confirmation(self, cid: str, chat_id: str, ttl: int = 600) -> dict | None:
        rows = self._x("SELECT * FROM confirmations WHERE id=? AND chat_id=? AND used=0", (cid, chat_id))
        if not rows or time.time() - rows[0]["created"] > ttl:
            return None
        self._x("UPDATE confirmations SET used=1 WHERE id=?", (cid,))
        r = dict(rows[0]); r["args"] = json.loads(r["args"])
        return r

    def pending_confirmations(self, chat_id: str, ttl: int = 600) -> list[dict]:
        rows = self._x("SELECT * FROM confirmations WHERE chat_id=? AND used=0 AND created>?", (chat_id, time.time() - ttl))
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- trailing stops
    def set_trailing(self, position_id, chat_id, symbol, side, callback_pct, activation_price=None):
        self._x("INSERT OR REPLACE INTO trailing VALUES (?,?,?,?,?,?,?,?,?)",
                (position_id, chat_id, symbol, side, float(callback_pct), activation_price, None, 1, time.time()))

    def trailing(self) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM trailing WHERE active=1")]

    def update_trailing_best(self, position_id, best):
        self._x("UPDATE trailing SET best_price=? WHERE position_id=?", (best, position_id))

    def stop_trailing(self, position_id):
        self._x("UPDATE trailing SET active=0 WHERE position_id=?", (position_id,))

    # ---------------------------------------------------------------- notes (agent long-term memory)
    def set_note(self, key: str, value: str):
        self._x("INSERT OR REPLACE INTO notes VALUES (?,?,?)", (key, value, time.time()))

    def notes(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._x("SELECT * FROM notes")}

    def delete_note(self, key: str):
        self._x("DELETE FROM notes WHERE key=?", (key,))
