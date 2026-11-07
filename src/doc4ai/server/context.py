from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from doc4ai.config import Doc4aiConfig
from doc4ai.store import Store

if TYPE_CHECKING:
    from doc4ai.server.jobs import JobQueue
    from doc4ai.server.sse import EventBus
    from doc4ai.server.uploads import UploadManager


@dataclass
class ServerContext:
    config: Doc4aiConfig
    store: Store
    bus: EventBus
    queue: JobQueue
    uploads: UploadManager
    token: str | None
    started_at: float
    extras: dict[str, Any] = field(default_factory=dict)     # setup runner, maintenance thread, ...

    def close(self) -> None:
        for name in ("maintenance", "setup", "preview"):
            obj = self.extras.get(name)
            if obj is not None and hasattr(obj, "stop"):
                obj.stop()
        if self.queue is not None:
            self.queue.stop()
        self.store.close()
