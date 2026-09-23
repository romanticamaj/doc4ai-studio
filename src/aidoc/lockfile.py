"""`data/aidoc.lock`: one queue owner (spec §8.1). A server writes {pid, started_at, host, port, token}; the CLI
reads it to forward work. A lock whose pid is dead, or reused by a process that is not aidoc, is stale."""
from __future__ import annotations

import json
import os
from pathlib import Path

import psutil


def is_aidoc_process(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        cmd = psutil.Process(pid).cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False
    for arg in cmd:
        a = arg.replace("\\", "/").lower()
        base = a.rsplit("/", 1)[-1]
        if base in ("aidoc", "aidoc.exe", "aidoc-script.py") or a in ("aidoc", "aidoc.cli") \
                or a.endswith(("/aidoc/cli.py", "/aidoc/__main__.py")):
            return True
    return False


def read_lock(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("pid"), int) else None


def is_live(info: dict | None) -> bool:
    if not info:
        return False
    pid = info.get("pid")
    if not isinstance(pid, int) or pid <= 0 or not psutil.pid_exists(pid):
        return False
    return is_aidoc_process(pid)


def _write_new(path: Path, info: dict) -> bool:
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f)
    return True


def acquire_lock(path: Path, info: dict) -> bool:
    """True when `info` now owns the lock (fresh or stale file); False when a live aidoc process holds it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _write_new(path, info):
        return True
    if is_live(read_lock(path)):
        return False
    try:                                   # stale: replace it (O_EXCL again so two starters cannot both win)
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return _write_new(path, info)


def release_lock(path: Path, pid: int | None = None) -> None:
    """Remove the lock only when it is ours."""
    pid = os.getpid() if pid is None else pid
    info = read_lock(path)
    if info is not None and info.get("pid") == pid:
        try:
            Path(path).unlink()
        except OSError:
            pass
