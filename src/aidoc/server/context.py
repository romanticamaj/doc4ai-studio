from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aidoc.config import AidocConfig
from aidoc.store import Store

if TYPE_CHECKING:
    from aidoc.server.jobs import JobQueue
    from aidoc.server.sse import EventBus
    from aidoc.server.uploads import UploadManager


@dataclass
class ServerContext:
    config: AidocConfig
    store: Store
    bus: EventBus
    queue: JobQueue
    uploads: UploadManager
    token: str | None
    started_at: float
    extras: dict[str, Any] = field(default_factory=dict)     # setup runner, maintenance thread, ...

    def close(self) -> None:
        for name in ("maintenance", "setup"):
            obj = self.extras.get(name)
            if obj is not None and hasattr(obj, "stop"):
                obj.stop()
        if self.queue is not None:
            self.queue.stop()
        self.store.close()
