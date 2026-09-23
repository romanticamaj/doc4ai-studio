"""Per-task liveness lock: held by the process running a task, released by the OS when that process dies.

It answers "is somebody converting this task right now?" without trusting recorded PIDs (which Windows reuses):
Store.create_task refuses to re-parent a task another process is running, run_task never runs a task twice
concurrently, and startup recovery leaves live tasks alone.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def lock_path(data_dir: Path, task_id: str) -> Path:
    # outside data/work/<task_id>/ so the work dir can be removed while the lock is held
    return Path(data_dir) / "work" / ".locks" / f"{task_id}.lock"


class TaskLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Non-blocking; True when this object now holds the lock."""
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(fd)
        if sys.platform == "win32":                # an open handle blocks deletion on Windows, so this cannot
            try:                                   # pull the file from under a new holder; on POSIX it could
                self.path.unlink()
            except OSError:
                pass


def is_locked(path: Path) -> bool:
    """True when another holder (any process, or another TaskLock in this one) holds the lock."""
    if not Path(path).exists():
        return False
    probe = TaskLock(path)
    if probe.acquire():
        probe.release()
        return False
    return True
