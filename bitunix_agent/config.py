"""All settings come from environment variables (or a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    return v.split("#", 1)[0].strip().strip("\"'").lower() in ("1", "true", "yes", "on", "y")


_WORDS = {"never": float("inf"), "off": 0.0, "none": 0.0, "no": 0.0, "false": 0.0, "disabled": 0.0,
          "always": 0.0, "inf": float("inf"), "infinity": float("inf"), "unlimited": float("inf")}


def _f(name: str, default: float) -> float:
    """Forgiving number parser: accepts '500', '1,000', '500usdt', '2%', 'never', 'off'. Bad values fall back to the
    default with a warning instead of crashing the agent."""
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    t = v.split("#", 1)[0].strip().strip("\"'").lower().replace(",", "").replace("_", "")
    if t in _WORDS:
        return _WORDS[t]
    import re
    m = re.match(r"^[-+]?\d*\.?\d+(e[-+]?\d+)?", t)
    if m:
        return float(m.group(0))
    import warnings
    warnings.warn(f"{name}={v!r} is not a number; using default {default}")
    return default


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


@dataclass
class Settings:
    # ---------------- Bitunix
    bitunix_api_key: str = field(default_factory=lambda: os.getenv("BITUNIX_API_KEY", ""))
    bitunix_secret_key: str = field(default_factory=lambda: os.getenv("BITUNIX_SECRET_KEY", ""))
    bitunix_rest_url: str = field(default_factory=lambda: os.getenv("BITUNIX_REST_URL", "https://fapi.bitunix.com"))
    bitunix_ws_public: str = field(default_factory=lambda: os.getenv("BITUNIX_WS_PUBLIC", "wss://fapi.bitunix.com/public/"))
    bitunix_ws_private: str = field(default_factory=lambda: os.getenv("BITUNIX_WS_PRIVATE", "wss://fapi.bitunix.com/private/"))
    margin_coin: str = field(default_factory=lambda: os.getenv("MARGIN_COIN", "USDT"))
    http_proxy: str | None = field(default_factory=lambda: os.getenv("BITUNIX_PROXY") or None)
    dry_run: bool = field(default_factory=lambda: _b("DRY_RUN", True))

    # ---------------- LLM
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openai"))   # openai|anthropic|deepseek|openrouter|dashscope|custom
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_base_url: str | None = field(default_factory=lambda: os.getenv("LLM_BASE_URL") or None)
    llm_temperature: float = field(default_factory=lambda: _f("LLM_TEMPERATURE", 0.2))
    llm_max_tool_rounds: int = field(default_factory=lambda: int(_f("LLM_MAX_TOOL_ROUNDS", 16)))

    # ---------------- Behaviour
    agent_name: str = field(default_factory=lambda: os.getenv("AGENT_NAME", "J-Rock"))
    language: str = field(default_factory=lambda: os.getenv("AGENT_LANGUAGE", "auto"))
    confirm_trades: str = field(default_factory=lambda: (os.getenv("CONFIRM_TRADES") or "always").split("#", 1)[0].strip().lower())  # always|never|above
    confirm_above_notional: float = field(default_factory=lambda: _f("CONFIRM_ABOVE_NOTIONAL", 500))

    # ---------------- Risk guard rails (the agent works around them, it never just refuses)
    max_leverage: int = field(default_factory=lambda: int(_f("RISK_MAX_LEVERAGE", 50)))
    max_position_notional: float = field(default_factory=lambda: _f("RISK_MAX_POSITION_NOTIONAL", 0))   # 0 = off
    max_risk_per_trade_pct: float = field(default_factory=lambda: _f("RISK_MAX_RISK_PER_TRADE_PCT", 0))  # 0 = off
    daily_loss_limit: float = field(default_factory=lambda: _f("RISK_DAILY_LOSS_LIMIT", 0))             # 0 = off
    require_stop_loss: bool = field(default_factory=lambda: _b("RISK_REQUIRE_STOP_LOSS", False))
    symbol_allowlist: list[str] = field(default_factory=lambda: _list("RISK_SYMBOL_ALLOWLIST"))

    # ---------------- Interfaces
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_allowed_users: list[str] = field(default_factory=lambda: _list("TELEGRAM_ALLOWED_USER_IDS"))
    slack_bot_token: str = field(default_factory=lambda: os.getenv("SLACK_BOT_TOKEN", ""))
    slack_app_token: str = field(default_factory=lambda: os.getenv("SLACK_APP_TOKEN", ""))
    slack_allowed_users: list[str] = field(default_factory=lambda: _list("SLACK_ALLOWED_USER_IDS"))
    api_host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    api_port: int = field(default_factory=lambda: int(_f("API_PORT", 8080)) if _f("API_PORT", 8080) != float("inf") else 8080)
    api_token: str = field(default_factory=lambda: os.getenv("API_TOKEN", ""))

    # ---------------- Storage
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/agent.db"))
    timezone: str = field(default_factory=lambda: os.getenv("TIMEZONE", "Asia/Tehran"))
    watch_symbols: list[str] = field(default_factory=lambda: _list("WATCH_SYMBOLS") or ["BTCUSDT", "ETHUSDT"])

    @property
    def has_keys(self) -> bool:
        return bool(self.bitunix_api_key and self.bitunix_secret_key)


settings = Settings()
