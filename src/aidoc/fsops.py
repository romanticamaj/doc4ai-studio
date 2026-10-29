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
    # a read-only entry blocks deletion on Windows. Clear the flag only when the file has a single link: NTFS
    # attributes are shared by hardlinks, so chmod on a hardlinked work copy would change the user's source.
    try:
        if os.stat(p).st_nlink > 1:
            raise exc_info[1]
    except FileNotFoundError:
        return
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


# ---- long paths (spec 2026-10-29 §4.6): source copies can pass 260 characters; every copy I/O goes through these
LONG_PATH_AT = 240
_LONG_PREFIX = "\\" * 2 + "?\\"                  # \\?\
_UNC_LONG_PREFIX = _LONG_PREFIX + "UNC\\"         # \\?\UNC\


def long_path(p: Path) -> Path:
    r"""Windows: an absolute path of LONG_PATH_AT characters or more gets the \\?\ prefix (no reliance on the
    LongPathsEnabled registry switch); anything else is returned unchanged."""
    s = str(p)
    if os.name != "nt" or len(s) < LONG_PATH_AT or s.startswith(_LONG_PREFIX) or not os.path.isabs(s):
        return Path(p)
    s = os.path.normpath(s)
    if s.startswith("\\\\"):
        return Path(_UNC_LONG_PREFIX + s[2:])
    return Path(_LONG_PREFIX + s)


def plain_path(p: Path) -> Path:
    r"""Strip a \\?\ prefix (the database and the sidecar store plain paths)."""
    s = str(p)
    if s.startswith(_UNC_LONG_PREFIX):
        return Path("\\\\" + s[len(_UNC_LONG_PREFIX):])
    if s.startswith(_LONG_PREFIX):
        return Path(s[len(_LONG_PREFIX):])
    return Path(p)


def _existing_ancestor(p: Path) -> Path | None:
    p = Path(p)
    while True:
        try:
            if p.exists():
                return p
        except OSError:
            pass
        if p.parent == p:
            return None
        p = p.parent


def same_volume(a: Path, b: Path) -> bool:
    """True when `a` and `b` live on the same volume: st_dev of their nearest existing ancestors; when that cannot
    be read, the drive letters are compared."""
    ea, eb = _existing_ancestor(Path(a)), _existing_ancestor(Path(b))
    if ea is not None and eb is not None:
        try:
            return os.stat(ea).st_dev == os.stat(eb).st_dev
        except OSError:
            pass
    return os.path.splitdrive(os.path.abspath(a))[0].lower() == os.path.splitdrive(os.path.abspath(b))[0].lower()
