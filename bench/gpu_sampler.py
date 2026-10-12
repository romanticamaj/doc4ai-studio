"""Peak GPU memory during a conversion: `nvidia-smi --query-gpu=memory.used` every 0.5 s in a thread (spec 2026-10-05
verify §3.4). The figure is the whole GPU's memory (other processes included); the baseline before the conversion is
kept so the report can show the increase. No nvidia-smi -> None (the report prints —)."""
from __future__ import annotations

import shutil
import subprocess
import threading


def query_mb() -> int | None:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=5, check=False).stdout
        vals = [int(x.strip()) for x in out.splitlines() if x.strip().isdigit()]
        return max(vals) if vals else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


class Sampler:
    def __init__(self, interval: float = 0.5, query=query_mb):
        self.interval, self._query = interval, query
        self.baseline: int | None = None
        self.peak: int | None = None
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def __enter__(self):
        self.baseline = self._query()
        self.peak = self.baseline
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            v = self._query()
            if v is not None:
                self.peak = v if self.peak is None else max(self.peak, v)

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=10)

    @property
    def delta(self) -> int | None:
        if self.peak is None or self.baseline is None:
            return None
        return max(0, self.peak - self.baseline)
