"""One small connection layer for SQLite (default) and Postgres (Neon, Supabase, Railway Postgres...).

Set DATABASE_URL=postgresql://user:pass@host/db?sslmode=require and everything J-Rock stores (chat memory, journal,
alerts, tasks, settings set from chat, skills, MCP servers, custom tools) lives in Postgres, so it survives redeploys
without a disk. Without DATABASE_URL it uses the SQLite file at DB_PATH, exactly as before.

Postgres goes through pg8000 (pure Python, installs on Termux). The SQL in this project is written for SQLite;
the few differences are translated here: ? placeholders, AUTOINCREMENT, REAL, INSERT OR REPLACE.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from urllib.parse import parse_qs, unquote, urlparse

log = logging.getLogger("bitunix.db")

# Primary key of every table that uses INSERT OR REPLACE (the key is always the first value inserted).
REPLACE_KEYS = {"chats": "chat_id", "trailing": "position_id", "notes": "key", "kv_settings": "name", "kv_files": "path"}


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def use_postgres(path: str) -> bool:
    """Tests and :memory: always stay on SQLite."""
    return path != ":memory:" and database_url().startswith(("postgres://", "postgresql://"))


def pg_active(path: str) -> bool:
    return use_postgres(path) and database_url() in _PG


def describe(path: str) -> str:
    if use_postgres(path):
        u = urlparse(database_url())
        return f"postgres {u.hostname}/{(u.path or '/').lstrip('/')}"
    return f"sqlite {path}"


_PG: dict[str, "PgConn"] = {}
_PG_LOCK = threading.Lock()
STATUS = {"backend": "sqlite", "error": None}


def connect(path: str) -> "Conn":
    """Postgres connections are shared (one per URL); SQLite gets its own connection per call, as before."""
    if use_postgres(path):
        url = database_url()
        with _PG_LOCK:
            if url not in _PG:
                try:
                    _PG[url] = PgConn(url)
                    STATUS.update(backend=describe(path), error=None)
                except Exception as e:
                    # Never crash the agent over the database: keep running on the local SQLite file and say why.
                    STATUS.update(backend=f"sqlite {path} (fallback)", error=f"{type(e).__name__}: {e}"[:300])
                    log.error("DATABASE_URL set but Postgres is unreachable (%s). Using SQLite %s for now.", STATUS["error"], path)
                    return SqliteConn(path)
            return _PG[url]
    return SqliteConn(path)


class Conn:
    lock: threading.RLock

    def execute(self, sql: str, args: tuple = ()) -> list:
        raise NotImplementedError

    def script(self, sql: str):
        for stmt in [s.strip() for s in sql.split(";")]:
            if stmt:
                self.execute(stmt)


class SqliteConn(Conn):
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    def execute(self, sql: str, args: tuple = ()) -> list:
        with self.lock:
            rows = self.conn.execute(sql, args).fetchall()
            self.conn.commit()
            return rows


def _pg_params(url: str) -> dict:
    u = urlparse(url)
    q = {k: v[-1] for k, v in parse_qs(u.query).items()}
    p = {"user": unquote(u.username or ""), "password": unquote(u.password or "") or None,
         "host": u.hostname or "localhost", "port": u.port or 5432, "database": unquote((u.path or "/").lstrip("/")) or None,
         "timeout": 30, "application_name": "j-rock"}
    local = p["host"] in ("localhost", "127.0.0.1", "::1") or p["host"].endswith(".internal")
    mode = q.get("sslmode", "disable" if local else "require")
    if mode != "disable":
        import ssl
        ctx = ssl.create_default_context()
        if mode in ("require", "prefer", "allow") and "neon.tech" not in p["host"]:
            # libpq "require" = encrypted, certificate not checked (self-signed hosts). Neon has real certs: verify them.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        p["ssl_context"] = ctx
    return p


def translate(sql: str) -> list[str]:
    """SQLite SQL -> Postgres SQL. Returns one or more statements."""
    s = sql.replace("?", "%s")
    s = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", s, flags=re.I)
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    s = re.sub(r'(?<![\w"])(trailing)(?![\w"])', r'"\1"', s)  # reserved word in Postgres
    m = re.match(r'\s*INSERT OR REPLACE INTO ("?\w+"?)', s, flags=re.I)
    if m:
        table = m.group(1).strip('"')
        key = REPLACE_KEYS[table]
        return [f'DELETE FROM "{table}" WHERE {key}=%s', re.sub(r"INSERT OR REPLACE", "INSERT", s, count=1, flags=re.I)]
    return [s]


class PgConn(Conn):
    def __init__(self, url: str):
        self.url = url
        self.lock = threading.RLock()
        self.conn = None
        self._connect()

    def _connect(self):
        import pg8000.dbapi
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = pg8000.dbapi.connect(**_pg_params(self.url))

    def _run(self, sql: str, args: tuple) -> list:
        cur = self.conn.cursor()
        stmts = translate(sql)
        try:
            rows: list = []
            for i, stmt in enumerate(stmts):
                a = (args[0],) if (len(stmts) > 1 and i == 0) else args
                cur.execute(stmt, tuple(a))
                if cur.description:
                    cols = [d[0] for d in cur.description]
                    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            self.conn.commit()
            return rows
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise

    def execute(self, sql: str, args: tuple = ()) -> list:
        with self.lock:
            try:
                return self._run(sql, args)
            except Exception as e:
                # Neon suspends idle computes and closes idle connections: reconnect once and retry.
                if not _is_connection_error(e):
                    raise
                log.warning("postgres connection lost (%s), reconnecting", type(e).__name__)
                self._connect()
                return self._run(sql, args)


def _is_connection_error(e: Exception) -> bool:
    name = type(e).__name__
    text = str(e).lower()
    return (isinstance(e, (OSError, ConnectionError)) or name in ("InterfaceError", "OperationalError")
            or "network error" in text or "connection" in text or "broken pipe" in text or "timed out" in text)
