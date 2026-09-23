"""Job queue worker (filled in by Task 4)."""
from __future__ import annotations


class JobQueue:
    def __init__(self, ctx):
        self.ctx = ctx

    def start(self) -> None:
        pass

    def stop(self, timeout: float = 10) -> None:
        pass
