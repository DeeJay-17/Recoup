"""Tiny in-memory token bucket keyed by tenant (Redis-backed version comes with multi-replica)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    per_minute: int
    _buckets: dict[str, Bucket] = field(default_factory=dict)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        b = self._buckets.get(key)
        if b is None:
            b = Bucket(tokens=float(self.per_minute), updated=now)
            self._buckets[key] = b
        b.tokens = min(self.per_minute, b.tokens + (now - b.updated) * self.per_minute / 60.0)
        b.updated = now
        if b.tokens >= 1:
            b.tokens -= 1
            return True
        return False
