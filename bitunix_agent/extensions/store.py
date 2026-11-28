"""Settings changed from chat. Stored in the agent's SQLite DB and applied on top of .env / environment variables,
so the user never has to edit .env (it stays optional)."""
from __future__ import annotations

import os
import time

from ..storage.backend import connect

SECRET_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS")

# Every setting the agent understands, with a one-line description (shown by the get_settings tool).
KNOWN = {
    "BITUNIX_API_KEY": "Bitunix API key", "BITUNIX_SECRET_KEY": "Bitunix secret key", "MARGIN_COIN": "default USDT",
    "DRY_RUN": "true = simulate orders, false = real trading", "BITUNIX_PROXY": "proxy for Bitunix REST (optional)",
    "LLM_PROVIDER": "openai | anthropic | deepseek | openrouter | dashscope | custom", "LLM_MODEL": "model name",
    "LLM_API_KEY": "LLM key", "LLM_BASE_URL": "custom OpenAI-compatible URL", "LLM_TEMPERATURE": "0-1",
    "LLM_MAX_TOOL_ROUNDS": "max tool steps per message", "LLM_MAX_CONCURRENCY": "LLM requests at once (default 1)",
    "LLM_RETRIES": "retries on HTTP 429/5xx (default 3)", "AUTO_GUARD": "true/false: code-side breakeven + trailing (default true)",
    "AUTO_GUARD_BE_ROI": "ROI % for breakeven (default 15)", "AUTO_GUARD_TRAIL_ROI": "ROI % to start trailing (default 20)",
    "AUTO_GUARD_TRAIL_CALLBACK": "trailing callback % (default 1.0)", "AGENT_NAME": "agent name",
    "CONFIRM_TRADES": "always | never | above", "CONFIRM_ABOVE_NOTIONAL": "USDT threshold for 'above'",
    "RISK_MAX_LEVERAGE": "max leverage", "RISK_MAX_POSITION_NOTIONAL": "0 = off", "RISK_MAX_RISK_PER_TRADE_PCT": "0 = off",
    "RISK_DAILY_LOSS_LIMIT": "0 = off", "RISK_REQUIRE_STOP_LOSS": "true/false", "RISK_SYMBOL_ALLOWLIST": "comma list",
    "TELEGRAM_BOT_TOKEN": "Telegram bot token", "TELEGRAM_ALLOWED_USER_IDS": "comma list of Telegram user ids",
    "TELEGRAM_PROXY": "proxy for Telegram", "TELEGRAM_API_URL": "Telegram API mirror",
    "SLACK_BOT_TOKEN": "xoxb-...", "SLACK_APP_TOKEN": "xapp-...", "SLACK_ALLOWED_USER_IDS": "comma list of Slack member ids",
    "SLACK_PROXY": "http proxy for Slack", "API_TOKEN": "HTTP API bearer token", "TIMEZONE": "e.g. Asia/Tehran",
    "WATCH_SYMBOLS": "comma list", "GITHUB_TOKEN": "GitHub token for clone/push of private repos",
    "WORKSPACE_DIR": "folder for projects J-Rock works on", "SHELL_TIMEOUT": "seconds per shell command",
    "DATABASE_URL": "Postgres/Neon URL; memory, settings, skills and MCP survive redeploys (restart needed)",
    "API_HOST": "listen address, keep 0.0.0.0 (never a domain)", "API_PORT": "listen port, default 8080",
}
NEEDS_RESTART = {"TELEGRAM_BOT_TOKEN", "TELEGRAM_PROXY", "TELEGRAM_API_URL", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_PROXY",
                 "API_TOKEN", "BITUNIX_PROXY", "WATCH_SYMBOLS", "DATABASE_URL", "API_HOST", "API_PORT"}


def is_secret(name: str) -> bool:
    return any(h in name.upper() for h in SECRET_HINTS) and not name.upper().endswith("_IDS")


def mask(name: str, value: str | None) -> str:
    if value is None or value == "":
        return "(not set)"
    if not is_secret(name):
        return value
    return value[:4] + "…" + value[-4:] if len(value) > 12 else "••••"


class SettingsStore:
    def __init__(self, db_path: str):
        self.path = db_path
        self.conn = connect(db_path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS kv_settings (name TEXT PRIMARY KEY, value TEXT, ts REAL)")

    def all(self) -> dict[str, str]:
        return {r["name"]: r["value"] for r in self.conn.execute("SELECT name, value FROM kv_settings")}

    def set(self, name: str, value: str | None):
        name = name.strip().upper()
        if value is None or value == "":
            self.conn.execute("DELETE FROM kv_settings WHERE name=?", (name,))
            os.environ.pop(name, None)
        else:
            self.conn.execute("INSERT OR REPLACE INTO kv_settings VALUES (?,?,?)", (name, str(value), time.time()))
            os.environ[name] = str(value)

    def get(self, name: str) -> str | None:
        rows = self.conn.execute("SELECT value FROM kv_settings WHERE name=?", (name.upper(),))
        return rows[0]["value"] if rows else None


def apply_overrides(db_path: str | None = None) -> dict[str, str]:
    """Copy chat-set settings into os.environ (they win over .env). Call before building Settings()."""
    path = db_path or os.getenv("DB_PATH", "data/agent.db")
    try:
        vals = SettingsStore(path).all()
    except Exception:
        return {}
    os.environ.update(vals)
    return vals
