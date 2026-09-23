"""Event bus + SSE (filled in by Task 3)."""
from __future__ import annotations


class EventBus:
    def __init__(self, store):
        self.store = store

    def publish(self, kind: str, ref_id: str | None, payload: dict) -> int:
        return self.store.append_event(kind, ref_id, payload)
