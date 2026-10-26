"""Minimal in-process sliding-window rate limiter for the login endpoint.

Single-process scope: the Synology deployment runs one uvicorn worker (`UVICORN_WORKERS=1`).
With several workers each process keeps its own window, so the effective limit is
workers x limit; a shared store (Redis) is deliberately not introduced (CLAUDE.md §3).
Memory is bounded: buckets are pruned on every call and never exceed MAX_KEYS.
"""

from __future__ import annotations

import threading
import time
from collections import deque

MAX_KEYS = 10_000


class SlidingWindowLimiter:
    def __init__(self, *, limit: int, window_seconds: float) -> None:
        self.limit = max(1, limit)
        self.window = max(1.0, window_seconds)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, bucket: deque[float], now: float) -> None:
        while bucket and now - bucket[0] >= self.window:
            bucket.popleft()

    def check(self, key: str) -> float | None:
        """Return seconds to wait when `key` is over the limit, else None (no hit recorded)."""
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return None
            self._prune(bucket, now)
            if len(bucket) >= self.limit:
                return max(1.0, self.window - (now - bucket[0]))
            return None

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if key not in self._hits and len(self._hits) >= MAX_KEYS:
                # Drop the oldest-looking buckets so an attacker cannot grow memory unboundedly.
                for stale in [
                    k for k, b in self._hits.items() if not b or now - b[-1] >= self.window
                ][:1000]:
                    self._hits.pop(stale, None)
                if len(self._hits) >= MAX_KEYS:
                    self._hits.pop(next(iter(self._hits)))
            bucket = self._hits.setdefault(key, deque())
            self._prune(bucket, now)
            bucket.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()
