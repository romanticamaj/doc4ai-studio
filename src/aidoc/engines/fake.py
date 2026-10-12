"""FakeEngine (index A9/§6): a real subprocess running fake_runner.py with the host's Python."""
from __future__ import annotations

import sys
from pathlib import Path

from aidoc import paths
from aidoc.engines.base import RunnerEngine
from aidoc.models import ConvertOptions, ProbeResult

_VLM_DELEGATED = frozenset({"is_remote", "remote_allowed", "timeout_factor", "endpoint_host", "local_endpoint",
                            "model_name", "local_supported", "key_available"})


class FakeEngine(RunnerEngine):
    def __init__(self, name: str, config=None):
        super().__init__(config)
        self.name = name

    def python(self) -> Path:
        return Path(sys.executable)

    def script(self) -> Path:
        return paths.runner_script("fake")

    def available(self) -> bool:
        # the fake vlm follows the configuration like the real one (backend "none" = not there: zero impact)
        return self._real().available() if self.name == "vlm" else True

    def label(self) -> str:
        return self._real().label()

    def __getattr__(self, attr):
        # VLM-specific helpers (is_remote, remote_allowed, timeout_factor, endpoint_host, ...) come from the real one
        if self.__dict__.get("name") == "vlm" and attr in _VLM_DELEGATED:
            return getattr(self._real(), attr)
        raise AttributeError(attr)

    def _real(self) -> RunnerEngine:
        from aidoc.engines.registry import _CLASSES
        return _CLASSES[self.name](self.config)

    def engine_opts(self, opts: ConvertOptions, probe: ProbeResult, full_page_ocr: bool = False) -> dict:
        """The real engine's options (so retry/OOM rules behave as in production) plus the fake's name."""
        return {**self._real().engine_opts(opts, probe, full_page_ocr), "fake_engine": self.name}

    def oom_downgrade(self, engine_opts: dict) -> dict | None:
        return self._real().oom_downgrade(engine_opts)
