"""Async REST client for every Bitunix futures endpoint.

- every endpoint/parameter comes from spec_generated.SPEC (built from the official docs)
- local validation before signing (enums, types, conditional rules, limits)
- exact signing: sorted query key+value, compact JSON body that is byte-identical to what is sent
- per-endpoint rate limiting, safe retries, clock-drift correction from the server Date header
- response normalization for the documented-vs-live differences
- dry-run (paper) mode for every mutating call
"""
from __future__ import annotations

import asyncio
import os
import email.utils
import logging
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx

from .constants import REST_BASE_URL
from .endpoints_generated import GeneratedEndpoints
from .errors import BitunixAPIError, BitunixNetworkError, RETRYABLE_CODES
from .rate_limit import RateLimiter
from .signing import compact_json, rest_headers, sorted_query_for_sign, to_query_value
from .spec_generated import SPEC
from .validation import validate

log = logging.getLogger("bitunix.rest")


class BitunixREST(GeneratedEndpoints):
    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        *,
        base_url: str = REST_BASE_URL,
        timeout: float = 15.0,
        max_retries: int = 3,
        dry_run: bool = False,
        language: str = "en-US",
        transport: httpx.AsyncBaseTransport | None = None,
        proxy: str | None = None,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.dry_run = dry_run
        self.language = language
        self.clock_offset_ms = 0
        self.position_mode: str = "HEDGE"  # the agent runs HEDGE mode only (user decision 2026-11-27); validation applies HEDGE rules
        self.limiter = RateLimiter(SPEC)
        kw: dict[str, Any] = {"timeout": timeout, "transport": transport}
        if proxy:
            kw["proxy"] = proxy
        self._timeout, self._transport = timeout, transport
        # A proxy is in play if one is given or the environment sets one (httpx reads HTTPS_PROXY/ALL_PROXY).
        self._proxied = bool(proxy) or any(os.getenv(v) for v in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"))
        self._direct = False
        self._http = httpx.AsyncClient(**kw)
        self.on_request = None  # optional hook(dict) for journaling

    async def close(self) -> None:
        await self._http.aclose()

    async def _go_direct(self, why: str) -> bool:
        """The proxy is broken (wrong port, 404, refused): switch to a direct connection once and keep working."""
        if not self._proxied or self._direct:
            return False
        log.warning("Bitunix REST: proxy failed (%s); switching to a direct connection", why)
        print(f"Bitunix REST: proxy failed ({why}). Switched to a direct connection. "
              "Fix or remove BITUNIX_PROXY / HTTPS_PROXY in .env if you need the proxy.")
        old = self._http
        self._http = httpx.AsyncClient(timeout=self._timeout, transport=self._transport, trust_env=False)
        self._direct = True
        try:
            await old.aclose()
        except Exception:
            pass
        return True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ------------------------------------------------------------------ core
    async def call(self, key: str, params: dict | None = None) -> Any:
        ep = SPEC[key]
        clean = validate(key, params, position_mode=self.position_mode)
        auto_client_id = None
        if key == "place_order" and "clientId" not in clean:
            clean["clientId"] = auto_client_id = "ai" + uuid.uuid4().hex[:24]
        if key == "batch_order":
            for item in clean["orderList"]:
                item.setdefault("clientId", "ai" + uuid.uuid4().hex[:24])
        if ep["mutating"] and self.dry_run:
            return {"dry_run": True, "endpoint": key, "method": ep["method"], "path": ep["path"], "body": clean,
                    "note": "DRY_RUN is on: nothing was sent to Bitunix."}
        if ep["method"] == "GET":
            data = await self.request("GET", ep["path"], query=clean, signed=not ep["public"], key=key)
        else:
            data = await self.request("POST", ep["path"], body=clean, signed=True, key=key,
                                      client_id=clean.get("clientId") or auto_client_id)
        data = normalize(key, data)
        return data

    async def request(self, method: str, path: str, *, query: dict | None = None, body: dict | None = None,
                      signed: bool = True, key: str = "raw", client_id: str | None = None) -> Any:
        """Raw signed request; usable for endpoints Bitunix adds later."""
        if signed and not (self.api_key and self.secret_key):
            raise BitunixAPIError(10003, "BITUNIX_API_KEY / BITUNIX_SECRET_KEY not configured", key)
        query = {k: v for k, v in (query or {}).items() if v is not None}
        sorted_items = [(k, to_query_value(query[k])) for k in sorted(query)]
        qs = urlencode(sorted_items, safe=",")
        url = f"{self.base_url}{path}" + (f"?{qs}" if qs else "")
        body_str = compact_json(body) if method == "POST" else ""
        attempt = 0
        while True:
            attempt += 1
            await self.limiter.acquire(key)
            headers = (rest_headers(self.api_key, self.secret_key, sorted_query_for_sign(query), body_str,
                                    offset_ms=self.clock_offset_ms, language=self.language)
                       if signed else {"language": self.language, "Content-Type": "application/json"})
            t0 = time.time()
            try:
                if method == "GET":
                    resp = await self._http.get(url, headers=headers)
                else:
                    resp = await self._http.post(url, headers=headers, content=body_str.encode("utf-8"))
            except httpx.ProxyError as e:
                if await self._go_direct(f"{type(e).__name__}"):
                    attempt -= 1
                    continue
                raise BitunixNetworkError(key, repr(e), state_unknown=method == "POST", client_id=client_id) from e
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if isinstance(e, httpx.ConnectError) and await self._go_direct("cannot connect to proxy"):
                    attempt -= 1
                    continue
                # GETs are idempotent; a POST may have reached the exchange, so never blindly resend it
                if method == "GET" and attempt <= self.max_retries:
                    await asyncio.sleep(min(2 ** attempt * 0.25, 4))
                    continue
                raise BitunixNetworkError(key, repr(e), state_unknown=method == "POST", client_id=client_id) from e
            self._learn_clock(resp, t0)
            if self.on_request:
                try:
                    self.on_request({"endpoint": key, "method": method, "path": path, "query": query, "body": body,
                                     "status": resp.status_code, "ms": int((time.time() - t0) * 1000)})
                except Exception:  # pragma: no cover
                    pass
            try:
                payload = resp.json()
            except ValueError:
                # Bitunix always answers JSON; an HTML/plain 404/403/407/502 means the proxy, not Bitunix, answered.
                if resp.status_code in (403, 404, 405, 407, 502, 503) and await self._go_direct(f"proxy HTTP {resp.status_code}"):
                    attempt -= 1
                    continue
                if resp.status_code in (403, 404, 451) and not self._proxied:
                    raise BitunixAPIError(resp.status_code,
                        f"Bitunix's server refused this machine's IP (HTTP {resp.status_code}, non-JSON). This usually means a "
                        "region block: Bitunix doesn't serve some countries (e.g. the US). On Railway, set the service region to "
                        "Asia (Singapore) or Europe in Settings, then redeploy.", key, body or query, resp.status_code)
                if resp.status_code >= 500 and method == "GET" and attempt <= self.max_retries:
                    await asyncio.sleep(min(2 ** attempt * 0.25, 4))
                    continue
                raise BitunixAPIError(resp.status_code, f"non-JSON HTTP {resp.status_code}: {resp.text[:200]}", key,
                                      body or query, resp.status_code)
            code = payload.get("code", resp.status_code)
            try:
                code = int(code)
            except (TypeError, ValueError):
                pass
            if code == 0:
                return payload.get("data")
            err = BitunixAPIError(code, payload.get("msg", ""), key, body or query, resp.status_code)
            if code in RETRYABLE_CODES and attempt <= self.max_retries:
                log.warning("retrying %s after %s", key, err)
                await asyncio.sleep(0.35 * attempt if code != 10011 else 0)
                continue
            raise err

    def _learn_clock(self, resp: httpx.Response, t0: float) -> None:
        """Keep the request timestamp inside the 60,000 ms window by tracking the server Date header."""
        d = resp.headers.get("date")
        if not d:
            return
        try:
            server = email.utils.parsedate_to_datetime(d).timestamp() * 1000
        except Exception:
            return
        local = (t0 + time.time()) / 2 * 1000
        drift = server - local
        # Date header has 1 s resolution; only correct meaningful drift
        self.clock_offset_ms = int(drift) if abs(drift) > 2000 else 0


# ---------------------------------------------------------------------- normalization
def _num_time(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def normalize(key: str, data: Any) -> Any:
    if key == "get_kline" and isinstance(data, list):
        for c in data:
            c["time"] = _num_time(c.get("time"))
        data.sort(key=lambda c: c["time"] if isinstance(c["time"], int) else 0)  # oldest -> newest
    elif key in ("get_funding_rate",) and isinstance(data, list):
        data = data[0] if len(data) == 1 else data
    elif key in ("get_single_account", "get_leverage_margin_mode") and isinstance(data, list) and len(data) == 1:
        data = data[0]
    elif key == "get_depth" and isinstance(data, dict):
        for side in ("asks", "bids"):
            data[side] = [[str(p), str(q)] for p, q in data.get(side, [])]
    elif key in ("cancel_all_orders", "cancel_orders", "batch_order") and isinstance(data, dict):
        for lst in ("successList", "failureList"):
            for it in data.get(lst) or []:
                if "orderId" in it and "id" not in it:
                    it["id"] = it["orderId"]
                if "id" in it and "orderId" not in it:
                    it["orderId"] = it["id"]
    return data
