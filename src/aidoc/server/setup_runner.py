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

    def start(self, engine: str, action: str = "setup") -> dict:
        """action "setup" = aidoc setup <engine>; "check" = the VLM connection test (engine "vlm", no install)."""
        with self._lock:
            if self.running():
                raise SetupBusy()
            info = {"setup_id": uuid.uuid4().hex, "engine": engine}
            if action != "setup":
                info["action"] = action
            self.current = info
            self._thread = threading.Thread(target=self._run, args=(engine, action), name=f"aidoc-setup-{engine}",
                                            daemon=True)
            self._thread.start()
        return info

    def _run(self, engine: str, action: str = "setup") -> None:
        bus = self.ctx.bus
        failures: list[str] = []

        def log(line: str) -> None:
            line = str(line)
            if "failed" in line or "not supported" in line or "choose a backend" in line or "no API key" in line:
                failures.append(line)
            bus.publish("setup.log", engine, {"engine": engine, "line": line})
        try:
            if action == "check":
                ok = bool(setup_engines.setup_vlm(self.ctx.config, log))
            else:
                ok = bool(setup_engines.setup(engine, self.ctx.config, log))
            done = {"engine": engine, "ok": ok}
            if action != "setup":
                done["action"] = action
            if not ok:
                done["error"] = failures[-1] if failures else "setup failed; see setup.log lines"
        except Exception as e:  # noqa: BLE001  report, never kill the server
            done = {"engine": engine, "ok": False, "error": f"{type(e).__name__}: {e}"}
        bus.publish("setup.done", engine, done)
        bus.publish("system.updated", None, {"engines_changed": engine})
        with self._lock:
            self.current = None

    def stop(self, timeout: float = 0) -> None:
        pass                                           # uv/model downloads are not interruptible; daemon thread
