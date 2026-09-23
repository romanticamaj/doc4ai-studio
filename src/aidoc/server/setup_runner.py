"""`aidoc setup <engine>` from the Web (spec §9 one-click): one setup at a time, in a thread, log lines on SSE."""
from __future__ import annotations

import threading
import uuid

from aidoc import setup_engines


class SetupBusy(Exception):
    pass


class SetupRunner:
    def __init__(self, ctx):
        self.ctx = ctx
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.current: dict | None = None             # {setup_id, engine}

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, engine: str) -> dict:
        with self._lock:
            if self.running():
                raise SetupBusy()
            info = {"setup_id": uuid.uuid4().hex, "engine": engine}
            self.current = info
            self._thread = threading.Thread(target=self._run, args=(engine,), name=f"aidoc-setup-{engine}",
                                            daemon=True)
            self._thread.start()
        return info

    def _run(self, engine: str) -> None:
        bus = self.ctx.bus

        def log(line: str) -> None:
            bus.publish("setup.log", engine, {"engine": engine, "line": str(line)})
        try:
            ok = bool(setup_engines.setup(engine, self.ctx.config, log))
            done = {"engine": engine, "ok": ok}
            if not ok:
                done["error"] = "setup failed; see setup.log lines"
        except Exception as e:  # noqa: BLE001  report, never kill the server
            done = {"engine": engine, "ok": False, "error": f"{type(e).__name__}: {e}"}
        bus.publish("setup.done", engine, done)
        bus.publish("system.updated", None, {"engines_changed": engine})
        with self._lock:
            self.current = None

    def stop(self, timeout: float = 0) -> None:
        pass                                           # uv/model downloads are not interruptible; daemon thread
