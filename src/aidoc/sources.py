"""Source staging (spec §8.5): the engine only ever reads a sha-verified work copy under data/work/<task_id>/."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from aidoc import fsops
from aidoc.names import file_sha256


class SourceError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code                      # "source_missing" | "source_changed"


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def stage_source(source_path: Path, expected_sha: str, work_dir: Path) -> Path:
    """Copy the source to work_dir/src<ext> and verify the copy's sha256.

    Idempotent: an already staged copy with the expected sha is reused even when the original has moved.
    Raises SourceError(source_missing | source_changed | source_unreadable)."""
    source_path, work_dir = Path(source_path), Path(work_dir)
    try:
        return _stage(source_path, expected_sha, work_dir)
    except SourceError:
        raise
    except FileNotFoundError as e:
        raise SourceError("source_missing", f"{source_path}: {e}") from e
    except OSError as e:                      # locked by another program, no permission, ...
        tmp = work_dir / f".src{source_path.suffix.lower()}.tmp"
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise SourceError("source_unreadable", f"{source_path}: {e}") from e


def _stage(source_path: Path, expected_sha: str, work_dir: Path) -> Path:
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
    if dst.exists():                          # stale copy (wrong sha); a copied read-only source stays read-only
        if os.stat(dst).st_nlink == 1:
            os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
        dst.unlink()
    # Always a real copy, never a hardlink (P2 verifier I1): a hardlink shares the data, so an in-place rewrite of
    # the original during conversion would reach segments that are split later. The copy is verified below.
    tmp = dst.with_name(f".{dst.name}.tmp")
    shutil.copy2(source_path, tmp)
    fsops.retry_fs(lambda: os.replace(tmp, dst))
    if file_sha256(dst) != expected_sha:
        os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)          # a copy of a read-only source is read-only too
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
