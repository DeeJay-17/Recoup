from __future__ import annotations

import contextlib
import time
from typing import Any

from recoup_common.logging import get_logger

log = get_logger(__name__)


class RateLimiter:
    """Fixed-window per-tenant limiter on Redis; degrades to in-process if Redis is unreachable."""

    def __init__(self, redis_url: str, per_minute: int) -> None:
        self.per_minute = per_minute
        self._local: dict[str, tuple[int, int]] = {}
        self._redis: Any = None
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        except Exception:
            self._redis = None
        self._redis_ok = self._redis is not None

    async def allow(self, key: str) -> bool:
        window = int(time.time() // 60)
        if self._redis_ok and self._redis is not None:
            try:
                k = f"rl:{key}:{window}"
                n = await self._redis.incr(k)
                if n == 1:
                    await self._redis.expire(k, 70)
                return int(n) <= self.per_minute
            except Exception as e:
                log.warning("ratelimit.redis_unavailable", error=str(e))
                self._redis_ok = False
        w, n = self._local.get(key, (window, 0))
        if w != window:
            w, n = window, 0
        n += 1
        self._local[key] = (w, n)
        return n <= self.per_minute

    async def aclose(self) -> None:
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
