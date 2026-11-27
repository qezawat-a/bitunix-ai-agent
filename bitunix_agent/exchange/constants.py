"""Every enum and constant from the official Bitunix futures docs."""
from __future__ import annotations

REST_BASE_URL = "https://fapi.bitunix.com"
WS_PUBLIC_URL = "wss://fapi.bitunix.com/public/"
WS_PRIVATE_URL = "wss://fapi.bitunix.com/private/"

# Signature / header rules (common/sign + common/introduction)
TIMESTAMP_WINDOW_MS = 60_000          # request timestamp must be within 60,000 ms of server time
NONCE_MAX_LEN = 64                    # error 10010: nonce length must be less than 64
DEFAULT_LANGUAGE = "en-US"

# WebSocket limits (websocket/prepare + changelog 2026-06-15)
WS_MAX_MESSAGES_PER_SEC = 5           # ping, pong and JSON messages all count; exceeding disconnects
WS_MAX_CHANNELS_PER_CONNECTION = 300

# ---------------------------------------------------------------- order enums
SIDES = ("BUY", "SELL")
TRADE_SIDES = ("OPEN", "CLOSE")
ORDER_TYPES = ("LIMIT", "MARKET")
EFFECTS = ("IOC", "FOK", "GTC", "POST_ONLY")
STOP_TYPES = ("MARK_PRICE", "LAST_PRICE")
STOP_TYPE_ALIASES = {"MARK": "MARK_PRICE", "LAST": "LAST_PRICE", "MARK_PRICE": "MARK_PRICE", "LAST_PRICE": "LAST_PRICE"}
MARGIN_MODES = ("ISOLATION", "CROSS")
MARGIN_MODE_ALIASES = {"ISOLATED": "ISOLATION", "ISOLATION": "ISOLATION", "CROSS": "CROSS", "CROSSED": "CROSS"}
POSITION_MODES = ("ONE_WAY", "HEDGE")
POSITION_MODE_ALIASES = {"ONE_WAY": "ONE_WAY", "ONEWAY": "ONE_WAY", "ONE-WAY": "ONE_WAY", "HEDGE": "HEDGE"}
POSITION_SIDES = ("LONG", "SHORT")

ORDER_STATUSES = ("INIT", "NEW", "PART_FILLED", "CANCELED", "FILLED", "PART_FILLED_CANCELED", "EXPIRED")
PENDING_ORDER_STATUSES = ("NEW", "PART_FILLED")
HISTORY_ORDER_STATUSES = ("FILLED", "CANCELED", "PART_FILLED_CANCELED", "EXPIRED")
SYMBOL_STATUSES = ("OPEN", "CANCEL_ONLY", "STOP")
ROLE_TYPES = ("TAKER", "MAKER")

# TP/SL websocket statuses and events (websocket/private/Tp Sl Channel)
TPSL_EVENTS = ("CREATE", "UPDATE", "CLOSE")
TPSL_STATUSES = ("NEW", "CANCELED", "SYSTEM_CANCELED", "FILLED", "FAILED")
TPSL_EVENT_STATUS_MEANING = {
    ("CREATE", "NEW"): "TP/SL order became effective",
    ("UPDATE", "NEW"): "TP/SL order was modified",
    ("CLOSE", "CANCELED"): "Canceled by the user",
    ("CLOSE", "SYSTEM_CANCELED"): "Canceled by the system",
    ("CLOSE", "FILLED"): "Trigger succeeded and the child order was placed (not child-order full fill)",
    ("CLOSE", "FAILED"): "Trigger failed",
}
ORDER_WS_EVENTS = ("CREATE", "UPDATE", "CLOSE")
POSITION_WS_EVENTS = ("OPEN", "UPDATE", "CLOSE")

# ---------------------------------------------------------------- market enums
KLINE_INTERVALS = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M")
KLINE_TYPES = ("LAST_PRICE", "MARK_PRICE")
KLINE_MAX_LIMIT = 200
DEPTH_LIMITS = ("1", "5", "15", "50", "max")
FUNDING_HISTORY_MAX_LIMIT = 200
HISTORY_MAX_LIMIT = 100
BATCH_ORDER_MAX = 5
TRADING_SETTINGS_MAX_SYMBOLS = 50

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
    "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
    "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000, "1M": 2_592_000_000,
}

# ---------------------------------------------------------------- websocket channels
WS_PUBLIC_CHANNELS = ("depth_books", "depth_book1", "depth_book5", "depth_book15", "tickers", "ticker", "price", "trade")
WS_DEPTH_CHANNELS = ("depth_books", "depth_book1", "depth_book5", "depth_book15")
WS_PRIVATE_CHANNELS = ("balance", "position", "order", "tpsl")   # tpsl is pushed automatically after login

# kline channel = <price type>_kline_<interval>
WS_KLINE_PRICE_TYPES = ("market", "mark")
WS_KLINE_INTERVALS = ("1min", "3min", "5min", "15min", "30min", "60min", "2h", "4h", "6h", "8h", "12h", "1day", "3day", "1week", "1month")
WS_KLINE_CHANNELS = tuple(f"{pt}_kline_{iv}" for iv in WS_KLINE_INTERVALS for pt in WS_KLINE_PRICE_TYPES)
REST_TO_WS_INTERVAL = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "2h": "2h", "4h": "4h",
    "6h": "6h", "8h": "8h", "12h": "12h", "1d": "1day", "3d": "3day", "1w": "1week", "1M": "1month",
}
WS_TO_REST_INTERVAL = {v: k for k, v in REST_TO_WS_INTERVAL.items()}
WS_TO_REST_INTERVAL["3min"] = "3m"  # WS-only interval


def ws_kline_channel(interval: str, price_type: str = "market") -> str:
    """REST-style ('15m') or WS-style ('15min') interval -> official channel name."""
    iv = REST_TO_WS_INTERVAL.get(interval, interval)
    if iv not in WS_KLINE_INTERVALS:
        raise ValueError(f"interval {interval!r} not supported by kline channel; use one of {WS_KLINE_INTERVALS}")
    pt = {"last": "market", "last_price": "market", "market": "market", "mark": "mark", "mark_price": "mark"}[price_type.lower()]
    return f"{pt}_kline_{iv}"
