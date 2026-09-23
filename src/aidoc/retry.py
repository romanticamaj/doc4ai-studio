"""Failure classification and retry policy (spec §8.2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aidoc.engines.base import EngineError
from aidoc.models import ErrorKind


@dataclass
class RetryDecision:
    action: Literal["retry", "fallback", "fail"]
    delay_s: float = 0.0
    engine_opts: dict | None = None
    note: str = ""


class RetryPolicy:
    """input -> fail; engine -> fallback; OOM -> downgrade options (free retry) or fallback;
    other transient (timeout / crash / kill) -> retry with backoff up to max_transient times, then fallback."""

    def __init__(self, max_transient: int = 2, backoff: tuple[float, ...] = (5.0, 30.0)):
        self.max_transient = max_transient
        self.backoff = tuple(backoff)

    def decide(self, err: EngineError, transient_retries_used: int, engine, engine_opts: dict) -> RetryDecision:
        if err.kind == ErrorKind.input:
            return RetryDecision("fail", note="input")
        if err.kind == ErrorKind.engine:
            return RetryDecision("fallback", note="engine")
        if err.oom:
            new = engine.oom_downgrade(engine_opts)
            if new is None:
                return RetryDecision("fallback", note="oom_no_downgrade")
            return RetryDecision("retry", 0.0, new, note="oom_downgrade")
        if transient_retries_used < self.max_transient:
            delay = self.backoff[min(transient_retries_used, len(self.backoff) - 1)] if self.backoff else 0.0
            return RetryDecision("retry", delay, dict(engine_opts), note="transient")
        return RetryDecision("fallback", note="transient_exhausted")
