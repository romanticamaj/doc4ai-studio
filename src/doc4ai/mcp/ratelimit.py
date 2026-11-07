"""Per-token token bucket for tools/call (MCP spec §4.3 step 3, index A-M13)."""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable


class RateLimiter:
    def __init__(self, default_per_min: Callable[[], int]):
        self._default = default_per_min
        self._buckets: dict[str, tuple[float, float]] = {}      # key -> (tokens, updated_at)
        self._lock = threading.Lock()

    def acquire(self, key: str, per_min: int | None = None, now: float | None = None) -> tuple[bool, int]:
        now = time.time() if now is None else now
        limit = max(1, int(per_min if per_min is not None else self._default()))
        rate = limit / 60.0
        with self._lock:
            tokens, updated = self._buckets.get(key, (float(limit), now))
            tokens = min(float(limit), tokens + (now - updated) * rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True, 0
            self._buckets[key] = (tokens, now)
            wait = (1.0 - tokens) / rate
            return False, max(1, math.ceil(wait))

    def forget(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)
