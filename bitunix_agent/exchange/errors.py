"""Official Bitunix futures error codes, each with a machine hint the agent uses to self-correct."""
from __future__ import annotations

from typing import Any

ERROR_CODES: dict[int, str] = {
    0: "Success",
    403: "This API key does not support this operation",
    10001: "Network Error",
    10002: "Parameter Error",
    10003: "api-key can't be empty",
    10004: "The current ip is not in the apikey ip whitelist",
    10005: "Too many requests, please try again later",
    10006: "Request too frequently",
    10007: "Sign signature error",
    10008: "{value} does not comply with the rule, optional [correctValue]",
    10009: "Another order operation is in progress, please try again shortly",
    10010: "Nonce's length must be less than 64",
    10011: "The request timestamp must be within 60,000 ms of the server time.",
    20001: "Market not exists",
    20002: "The current positions amount has exceeded the maximum open limit, please adjust the risk limit",
    20003: "Insufficient balance",
    20004: "Insufficient Trader",
    20005: "Invalid leverage",
    20006: "You can't change leverage or margin mode as there are open orders",
    20007: "Order not found, please try it later",
    20008: "Insufficient amount",
    20009: "Position exists, so positions mode cannot be updated",
    20010: "Activation failed, the available balance in the futures account does not meet the conditions for activation of the coupon",
    20011: "Account not allowed to trade",
    20012: "This futures does not allow trading",
    20013: "Function disabled due tp pending account deletion request",
    20014: "Account deleted",
    20015: "This futures is not supported",
    20016: "Batch order exceeded the max size",
    30001: "Failed to order. Please adjust the order price or the leverage as the order price dealt may immediately liquidate.",
    30002: "Price below liquidated price",
    30003: "Price above liquidated price",
    30004: "Position not exist",
    30005: "The trigger price is closer to the current price and may be triggered immediately",
    30006: "Please select TP or SL",
    30007: "TP trigger price is greater than average entry price",
    30008: "TP trigger price is less than average entry price",
    30009: "SL trigger price is less than average entry price",
    30010: "SL trigger price is greater than average entry price",
    30011: "Abnormal order status",
    30012: "Already added to favorite",
    30013: "Exceeded the maximum order quantity",
    30014: "Max Buy Order Price",
    30015: "Mini Sell Order Price",
    30016: "The qty should be larger than",
    30017: "The qty cannot be less than the minimum qty",
    30018: "Order failed. No position opened. Cancel [Reduce-only] settings and retry later",
    30019: "Order failed. A [Reduce-only] order can not be in the same direction as the open position",
    30020: "Trigger price for TP should be higher than mark price:",
    30021: "Trigger price for TP should be lower than mark price:",
    30022: "Trigger price for SL should be higher than mark price:",
    30023: "Trigger price fo SL should be lower than mark price:",
    30024: "Trigger price for SL should be lower than liq price:",
    30025: "Trigger price for SL should be higher than liq price:",
    30026: "TP price must be greater than last price:",
    30027: "TP price must be greater than mark price:",
    30028: "SL price must be less than last price:",
    30029: "SL price must be less than mark price:",
    30030: "SL price must be greater than last price:",
    30031: "SL price must be greater than mark price:",
    30032: "TP price must be less than last price:",
    30033: "TP price must be less than mark price:",
    30034: "TP price must be less than mark price:",
    30035: "SL price must be greater than trigger price:",
    30036: "TP price must be greater than trigger price:",
    30037: "TP price must be greater than trigger price:",
    30038: "TP/SL amount must be less than the size of the position.",
    30039: "The order qty can't be greater than the max order qty:",
    30040: "Futures trading is prohibited, please contact customer service.",
    30041: "Trigger price must be greater than 0",
    30042: "Client ID duplicate",
    40001: "Please cancel open orders and close all positions before canceling lead trading",
    40002: "Lead amount hast to be over the limits",
    40003: "Lead order amount exceeds the limits.",
    40004: "Please do not repeat the operation",
    40005: "Action is not available for the current user type.",
    40006: "Sub-account reaches the limit.",
    40007: "Share settlement is being processed,lease try again later",
    40008: "After the transfer, the account balance will be less than the order amount, please enter again.",
}

# Codes where the request was certainly NOT executed and an automatic retry is safe, even for POST.
RETRYABLE_CODES = {10001, 10005, 10006, 10009, 10011}
RATE_LIMIT_CODES = {10005, 10006}

# What the agent should do next for each code (fed back to the LLM so it fixes and retries instead of giving up).
FIX_HINTS: dict[int, str] = {
    403: "The API key lacks this permission. Enable Futures trading permission for the key on bitunix.com (API management).",
    10002: "A parameter is wrong. Re-read the endpoint spec (types, enums, conditional fields) and resend.",
    10003: "Set BITUNIX_API_KEY in the environment.",
    10004: "Add this server's public IP to the API key whitelist, or remove the whitelist.",
    10007: "Signature mismatch: check BITUNIX_SECRET_KEY; query params must be sorted key+value with no separators; body must be compact JSON identical to what is sent.",
    10008: "An enum value is wrong. The message names the allowed values; resend with one of them.",
    10010: "Nonce must be shorter than 64 chars (client uses 32).",
    10011: "Local clock drift. Client re-syncs using the server Date header and retries.",
    20001: "Symbol does not exist. Use get_trading_pairs to find the exact symbol (e.g. BTCUSDT).",
    20002: "Position exceeds the risk tier limit. Reduce size or lower leverage (see get_position_tiers).",
    20003: "Insufficient balance. Reduce qty, lower leverage need or add margin; check get_single_account.available.",
    20005: "Leverage outside the symbol's minLeverage..maxLeverage (get_trading_pairs) or the tier limit (get_position_tiers).",
    20006: "Cancel open orders on the symbol first (cancel_all_orders), then change leverage/margin mode.",
    20007: "Order not found yet; wait a moment or check history orders.",
    20008: "Amount too small/large. Check minTradeVolume and basePrecision in get_trading_pairs.",
    20009: "Close all positions and cancel orders before switching position mode.",
    20012: "Symbol not tradable now (symbolStatus not OPEN or isApiSupported false).",
    20015: "Symbol not supported via API.",
    20016: "batch_order accepts max 5 orders per request; split into chunks.",
    30001: "Price would liquidate immediately. Move the limit price closer to market or lower leverage.",
    30004: "Position not found. Refresh get_pending_positions and use the current positionId.",
    30005: "Trigger too close to current price. Move the trigger further from mark/last price.",
    30006: "Provide tpPrice and/or slPrice.",
    30007: "For SHORT the TP must be below entry.",
    30008: "For LONG the TP must be above entry.",
    30009: "For SHORT the SL must be above entry.",
    30010: "For LONG the SL must be below entry.",
    30013: "Qty exceeds maxLimitOrderVolume/maxMarketOrderVolume; split the order.",
    30014: "Buy price above priceProtectScope band; lower the price.",
    30015: "Sell price below priceProtectScope band; raise the price.",
    30016: "Increase qty.",
    30017: "Qty below minTradeVolume; increase qty.",
    30018: "No position to reduce. Remove reduceOnly or open a position first.",
    30019: "reduceOnly must be in the opposite direction to the position (ONE_WAY).",
    30038: "TP/SL qty must be <= position size.",
    30039: "Split the order into smaller chunks.",
    30041: "Trigger price must be > 0.",
    30042: "clientId already used; generate a new one.",
}


class BitunixError(Exception):
    """Base error."""


class ValidationError(BitunixError):
    """Request rejected locally before reaching the exchange."""

    def __init__(self, endpoint: str, problems: list[str]):
        self.endpoint = endpoint
        self.problems = problems
        super().__init__(f"{endpoint}: " + "; ".join(problems))

    def to_dict(self) -> dict[str, Any]:
        return {"error": "validation", "endpoint": self.endpoint, "problems": self.problems,
                "hint": "Fix the listed fields and call again."}


class BitunixAPIError(BitunixError):
    def __init__(self, code: int, msg: str, endpoint: str = "", request: dict | None = None, http_status: int = 200):
        self.code = int(code) if str(code).lstrip("-").isdigit() else code
        self.msg = msg
        self.endpoint = endpoint
        self.request = request or {}
        self.http_status = http_status
        self.official = ERROR_CODES.get(self.code, "Unknown error code")
        self.hint = FIX_HINTS.get(self.code, "")
        super().__init__(f"[{code}] {msg} ({self.official}) @ {endpoint}" + (f" | fix: {self.hint}" if self.hint else ""))

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES

    def to_dict(self) -> dict[str, Any]:
        return {"error": "exchange", "code": self.code, "msg": self.msg, "official": self.official,
                "hint": self.hint, "endpoint": self.endpoint, "request": self.request}


class BitunixNetworkError(BitunixError):
    def __init__(self, endpoint: str, detail: str, state_unknown: bool = False, client_id: str | None = None):
        self.endpoint = endpoint
        self.state_unknown = state_unknown
        self.client_id = client_id
        msg = f"network error on {endpoint}: {detail}"
        if state_unknown:
            msg += f". The order may or may not have been placed; check get_order_detail(clientId={client_id!r})."
        super().__init__(msg)

    def to_dict(self) -> dict[str, Any]:
        return {"error": "network", "endpoint": self.endpoint, "detail": str(self), "state_unknown": self.state_unknown, "clientId": self.client_id}
