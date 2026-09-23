"""In-process CLI runs (convert / batch without a server) hold `data/cli/<pid>.lock` while they convert, so
`aidoc serve` can refuse to become a second queue owner next to them (spec §8.1; final review I3). Several CLI
runs may coexist (each has its own file); the OS releases a lock when its process dies."""
from __future__ import annotations

import os
from pathlib import Path

from aidoc.tasklock import TaskLock, is_locked


def _dir(data_dir: Path) -> Path:
    return Path(data_dir) / "cli"


class CliRunLock(TaskLock):
    def __init__(self, data_dir: Path):
        super().__init__(_dir(data_dir) / f"{os.getpid()}.lock")

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def active_cli_runs(data_dir: Path) -> int:
    """How many live in-process CLI runs hold a lock; stale files are removed."""
    d = _dir(data_dir)
    if not d.is_dir():
        return 0
    n = 0
    for f in d.glob("*.lock"):
        if is_locked(f):
            n += 1
        else:
            try:
                f.unlink()
            except OSError:
                pass
    return n
