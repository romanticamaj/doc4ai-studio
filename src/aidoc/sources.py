"""Source staging (spec §8.5): the engine only ever reads a sha-verified work copy under data/work/<task_id>/."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from aidoc import fsops
from aidoc.names import file_sha256


class SourceError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code                      # "source_missing" | "source_changed"


def _same_volume(a: Path, b: Path) -> bool:
    if sys.platform == "win32":
        return a.resolve().drive.lower() == b.resolve().drive.lower()
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def stage_source(source_path: Path, expected_sha: str, work_dir: Path) -> Path:
    """Hardlink (same volume) or copy the source to work_dir/src<ext> and verify its sha256.

    Idempotent: an already staged copy with the expected sha is reused even when the original has moved.
    Raises SourceError(source_missing | source_changed)."""
    source_path, work_dir = Path(source_path), Path(work_dir)
    dst = work_dir / f"src{source_path.suffix.lower()}"
    if dst.is_file() and not _same_file(dst, source_path) and file_sha256(dst) == expected_sha:
        return dst
    if not source_path.is_file():
        raise SourceError("source_missing", str(source_path))
    work_dir.mkdir(parents=True, exist_ok=True)
    if _same_file(dst, source_path):          # uploads already live in the work dir: verify only
        if file_sha256(dst) != expected_sha:
            raise SourceError("source_changed", str(source_path))
        return dst
    if dst.exists():
        dst.unlink()
    linked = False
    if _same_volume(source_path, work_dir):
        try:
            os.link(source_path, dst)
            linked = True
        except OSError:
            linked = False
    if not linked:
        tmp = dst.with_name(f".{dst.name}.tmp")
        shutil.copy2(source_path, tmp)
        fsops.retry_fs(lambda: os.replace(tmp, dst))
    if file_sha256(dst) != expected_sha:
        dst.unlink(missing_ok=True)
        raise SourceError("source_changed", str(source_path))
    return dst


def purge_expired_work_copies(store, now: float) -> int:
    """Delete work copies whose retention ended (spec §8.5, work_retention_days); returns how many."""
    n = 0
    for doc in store.list_documents():
        path, exp = doc.get("work_copy_path"), doc.get("work_copy_expires_at")
        if not path or exp is None or exp >= now:
            continue
        p = Path(path)
        try:
            if p.is_dir():
                fsops.remove_tree(p)
            elif p.exists():
                p.unlink()
        except OSError:
            continue                          # still in use: try again next time
        store.update_document(doc["id"], work_copy_path=None, work_copy_expires_at=None)
        n += 1
    return n
