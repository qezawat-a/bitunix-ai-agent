"""Signature algorithms exactly as in common/sign and websocket/prepare (official docs + official GitHub demo)."""
from __future__ import annotations

import hashlib
import json
import secrets
import string
import time
from typing import Any, Mapping

_ALPHABET = string.ascii_letters + string.digits


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def make_nonce(length: int = 32) -> str:
    """Random string. Docs: must be shorter than 64 chars (error 10010)."""
    if not 0 < length < 64:
        raise ValueError("nonce length must be 1..63")
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def ms_timestamp(offset_ms: int = 0) -> str:
    return str(int(time.time() * 1000) + offset_ms)


def to_query_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def sorted_query_for_sign(params: Mapping[str, Any] | None) -> str:
    """'All queryParams are sorted in ascending ASCII order by Key' -> key1value1key2value2 (no separators)."""
    if not params:
        return ""
    return "".join(f"{k}{to_query_value(params[k])}" for k in sorted(params))


def compact_json(body: Any) -> str:
    """'Parameters in body, compressed into a string, remember to remove all spaces'. The exact same string is sent."""
    if body is None or body == "":
        return ""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def rest_sign(nonce: str, timestamp: str, api_key: str, query_params: str, body: str, secret_key: str) -> str:
    """digest = SHA256(nonce + timestamp + api-key + queryParams + body); sign = SHA256(digest + secretKey)."""
    digest = sha256_hex(nonce + timestamp + api_key + query_params + body)
    return sha256_hex(digest + secret_key)


def rest_headers(api_key: str, secret_key: str, query_params: str = "", body: str = "", *,
                 offset_ms: int = 0, language: str = "en-US") -> dict[str, str]:
    nonce = make_nonce()
    ts = ms_timestamp(offset_ms)
    return {
        "api-key": api_key,
        "nonce": nonce,
        "timestamp": ts,
        "sign": rest_sign(nonce, ts, api_key, query_params, body, secret_key),
        "language": language,
        "Content-Type": "application/json",
    }


def ws_login_args(api_key: str, secret_key: str, *, offset_ms: int = 0) -> dict[str, Any]:
    """WebSocket login (websocket/prepare): timestamp in SECONDS (int),
    digest = SHA256(nonce + timestamp + apiKey), sign = SHA256(digest + secretKey)."""
    nonce = make_nonce()
    ts = int((time.time() * 1000 + offset_ms) // 1000)
    digest = sha256_hex(nonce + str(ts) + api_key)
    return {"apiKey": api_key, "timestamp": ts, "nonce": nonce, "sign": sha256_hex(digest + secret_key)}


def ws_params_sign(params: Mapping[str, Any], secret_key: str) -> str:
    """Generic WS params signature (common/sign 'WebSocket API Signature'): sort every field except sign,
    concatenate key+value with no spaces; digest = SHA256(nonce + timestamp + apiKey + params)."""
    p = {k: v for k, v in params.items() if k != "sign"}
    joined = "".join(f"{k}{to_query_value(p[k])}" for k in sorted(p)).replace(" ", "")
    digest = sha256_hex(str(p["nonce"]) + str(p["timestamp"]) + str(p["apiKey"]) + joined)
    return sha256_hex(digest + secret_key)
