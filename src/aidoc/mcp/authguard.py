"""Failed-authentication flood control for /mcp (spec §1 成功標準 5: every request is logged — but a client
hammering a bad token must not be able to flood `mcp_calls` and the live event stream).

Per client IP: the first `limit` failures inside `window_s` are logged one row each, as before. The next failure
blocks that IP for `cooldown_s` (every further failure extends it): failures from it get `429` + `Retry-After`, and
instead of a row each they are counted into ONE aggregate row (`status=rate_limited`, `error_code=auth_failures`,
`args_summary={"suppressed", "reasons", "prefixes", "first", "last"}`) that is updated at most every `flush_s`
seconds (and re-published as the same `mcp.call` id). A valid token from a blocked IP is still served: the block
only meters failures, it is not a denial of service for a shared address (NAT, loopback)."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

AUTH_FAIL_LIMIT = 10           # failures per window before an IP is blocked
AUTH_FAIL_WINDOW_S = 60
AUTH_COOLDOWN_S = 60
FLUSH_S = 5
MAX_IPS = 10_000
MAX_PREFIXES = 5


@dataclass
class _IpState:
    fails: deque = field(default_factory=deque)        # timestamps of logged failures inside the window
    blocked_until: float = 0.0
    row_id: int | None = None
    suppressed: int = 0
    reasons: dict = field(default_factory=dict)
    prefixes: list = field(default_factory=list)
    first: float = 0.0
    last: float = 0.0
    flushed_at: float = 0.0
    dirty: bool = False


class AuthFailureGuard:
    def __init__(self, recorder, *, limit: int = AUTH_FAIL_LIMIT, window_s: float = AUTH_FAIL_WINDOW_S,
                 cooldown_s: float = AUTH_COOLDOWN_S, flush_s: float = FLUSH_S, max_ips: int = MAX_IPS, clock=time.time):
        self.recorder = recorder
        self.limit, self.window_s, self.cooldown_s, self.flush_s, self.max_ips = limit, window_s, cooldown_s, flush_s, max_ips
        self.clock = clock
        self._ips: dict[str, _IpState] = {}
        self._lock = threading.Lock()

    def blocked(self, ip: str | None, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        with self._lock:
            st = self._ips.get(ip or "?")
            return st is not None and st.blocked_until > now

    def on_failure(self, ip: str | None, reason: str, prefix_seen: str | None, protocol_version: str | None = None) -> int | None:
        """None: log this failure as its own row (401). An int: suppressed — answer 429 with this Retry-After."""
        now = self.clock()
        key = ip or "?"
        with self._lock:
            st = self._ips.get(key)
            if st is None:
                if len(self._ips) >= self.max_ips:
                    self._evict(now)
                st = self._ips[key] = _IpState()
            if st.blocked_until <= now:
                if st.row_id is not None:            # a finished episode: final count, then start over
                    self._flush(key, st, now)
                    st.row_id, st.suppressed, st.reasons, st.prefixes = None, 0, {}, []
                while st.fails and now - st.fails[0] > self.window_s:
                    st.fails.popleft()
                if len(st.fails) < self.limit:
                    st.fails.append(now)
                    return None
            # blocked (or just crossed the limit): count, extend, aggregate
            st.blocked_until = now + self.cooldown_s
            st.suppressed += 1
            st.reasons[reason] = st.reasons.get(reason, 0) + 1
            if prefix_seen and prefix_seen not in st.prefixes and len(st.prefixes) < MAX_PREFIXES:
                st.prefixes.append(prefix_seen)
            st.last = now
            st.dirty = True
            if st.row_id is None:
                st.first = now
                st.row_id = self.recorder.record(status="rate_limited", error_code="auth_failures", http_status=429,
                                                 ip=ip, token_prefix_seen=prefix_seen, protocol_version=protocol_version,
                                                 args=self._summary(st), ts=now)
                st.flushed_at, st.dirty = now, False
            elif now - st.flushed_at >= self.flush_s:
                self._flush(key, st, now)
            return max(1, int(st.blocked_until - now + 0.999))

    def sweep(self, now: float | None = None) -> None:
        """Flush pending counts; forget IPs that are quiet and unblocked (called by the recorder's watcher)."""
        now = self.clock() if now is None else now
        with self._lock:
            for key, st in list(self._ips.items()):
                if st.dirty:
                    self._flush(key, st, now)
                if st.blocked_until <= now and (not st.fails or now - st.fails[-1] > self.window_s):
                    del self._ips[key]

    # ---- internals (lock held)
    def _summary(self, st: _IpState) -> dict:
        return {"suppressed": st.suppressed, "reasons": dict(st.reasons), "prefixes": list(st.prefixes),
                "first": round(st.first, 3), "last": round(st.last, 3)}

    def _flush(self, key: str, st: _IpState, now: float) -> None:
        if st.row_id is not None and st.dirty:
            self.recorder.update_call(st.row_id, args=self._summary(st))
        st.flushed_at, st.dirty = now, False

    def _evict(self, now: float) -> None:
        for key, st in list(self._ips.items()):
            if st.blocked_until <= now:
                if st.dirty:
                    self._flush(key, st, now)
                del self._ips[key]
            if len(self._ips) < self.max_ips:
                return
        oldest = min(self._ips, key=lambda k: self._ips[k].last or (self._ips[k].fails[-1] if self._ips[k].fails else 0))
        self._flush(oldest, self._ips[oldest], now)
        del self._ips[oldest]
