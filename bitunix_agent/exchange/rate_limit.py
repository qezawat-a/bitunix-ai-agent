"""Async token buckets matching the official per-endpoint limits (req/sec per uid or per ip)."""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float | None = None, safety: float = 0.9):
        self.rate = max(rate_per_sec * safety, 0.5)
        self.capacity = capacity or max(1.0, self.rate)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class RateLimiter:
    """One bucket per endpoint, using SPEC['rate_limit']."""

    def __init__(self, spec: dict, safety: float = 0.9):
        self.buckets = {k: TokenBucket(v["rate_limit"]["per_sec"], safety=safety) for k, v in spec.items() if v.get("rate_limit")}
        self.default = TokenBucket(5, safety=safety)

    async def acquire(self, key: str) -> None:
        await self.buckets.get(key, self.default).acquire()
