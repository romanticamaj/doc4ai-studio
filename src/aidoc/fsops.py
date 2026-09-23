from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")
_RETRY_ERRNOS = {errno.EACCES, errno.EBUSY, errno.EPERM}


class FsBusyError(OSError):
    """Rename/delete kept failing because another process holds a handle."""


def retry_fs(fn: Callable[[], T], *, attempts: int = 5, base_delay: float = 0.2) -> T:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except OSError as e:
            if isinstance(e, PermissionError) or getattr(e, "errno", None) in _RETRY_ERRNOS:
                last = e
                if i < attempts - 1:
                    time.sleep(base_delay * (2 ** i))
                continue
            raise
    raise FsBusyError(errno.EBUSY, f"filesystem busy after {attempts} attempts: {last}")


def _tmp_name(path: Path) -> Path:
    return path.with_name(f".{path.name}.tmp")


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_name(path)
    with open(tmp, "w", encoding=encoding, newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    retry_fs(lambda: os.replace(tmp, path))


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def atomic_write_lines(path: Path, lines: Iterable[str]) -> None:
    atomic_write_text(path, "".join(line.rstrip("\n") + "\n" for line in lines))


def _on_rm_error(func, p, exc_info):
    os.chmod(p, stat.S_IWRITE)
    func(p)


def remove_tree(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        return
    retry_fs(lambda: shutil.rmtree(path, onerror=_on_rm_error))


def replace_dir_three_step(tmp_dir: Path, final_dir: Path, trash_dir: Path) -> None:
    """Spec §8.6. Any failure raises FsBusyError; final_dir is never half-replaced."""
    tmp_dir, final_dir, trash_dir = Path(tmp_dir), Path(final_dir), Path(trash_dir)
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_dir.exists():
        trash_dir.parent.mkdir(parents=True, exist_ok=True)
        if trash_dir.exists():
            remove_tree(trash_dir)
        retry_fs(lambda: os.replace(final_dir, trash_dir))          # step 1
    try:
        retry_fs(lambda: os.replace(tmp_dir, final_dir))            # step 2
    except FsBusyError:
        if trash_dir.exists() and not final_dir.exists():           # roll back step 1
            retry_fs(lambda: os.replace(trash_dir, final_dir))
        raise
    try:
        remove_tree(trash_dir)                                      # step 3 (recovery cleans if this fails)
    except FsBusyError:
        pass
