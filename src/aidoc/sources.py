"""Source staging (spec §8.5): the engine only ever reads a sha-verified work copy under data/work/<task_id>/."""
from __future__ import annotations

import os
import shutil
import stat
import threading
from dataclasses import dataclass
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


def stage_source(source_path: Path, expected_sha: str, work_dir: Path, preferred: Path | None = None) -> Path:
    """Copy the source to work_dir/src<ext> and verify the copy's sha256.

    Idempotent: an already staged copy with the expected sha is reused even when the original has moved.
    `preferred` = a file already in the work dir (an upload): it is only verified, never re-staged from
    `source_path` (which is then just a display name).
    Raises SourceError(source_missing | source_changed | source_unreadable)."""
    source_path, work_dir = Path(source_path), Path(work_dir)
    if preferred is not None:
        preferred = Path(preferred)
        if not preferred.is_file():
            raise SourceError("source_missing", str(preferred))
        if file_sha256(preferred) != expected_sha:
            raise SourceError("source_changed", str(preferred))
        return preferred
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
    # A staged copy with the expected sha stands in for an original that has gone away. While the original is
    # still there it is compared again (spec §8.5): an original rewritten since must fail source_changed, not be
    # silently converted from the old copy (P4 acceptance row 20).
    if dst.is_file() and not _same_file(dst, source_path) and not source_path.exists() \
            and file_sha256(dst) == expected_sha:
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


_UNFINISHED = {"queued", "probing", "converting", "checking"}


def purge_expired_work_copies(store, now: float) -> int:
    """Delete work copies whose retention ended (spec §8.5, work_retention_days); returns how many."""
    n = 0
    for doc in store.list_documents():
        path, exp = doc.get("work_copy_path"), doc.get("work_copy_expires_at")
        if not path or exp is None or exp >= now:
            continue
        p = Path(path)
        task = store.get_task(p.name)                 # data/work/<task_id>: a retried task reuses it
        if task is not None and (task["status"] in _UNFINISHED or store.task_is_live(task["id"])):
            continue                                  # queued / running again: the copy is its source
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


# ---------------------------------------------------------------- the one source lookup (spec 2026-10-29 §6, index A27)

@dataclass(frozen=True)
class ResolvedSource:
    path: Path          # long-path safe: open / FileResponse / PyMuPDF / soffice take it as it is
    kind: str           # "work_copy" | "source_copy" | "original"


_SHA_CACHE: dict[tuple[str, int, int], str] = {}
_SHA_LOCK = threading.Lock()
_SHA_CACHE_MAX = 4096


def sha_matches_cached(path: Path, sha256: str) -> bool:
    """sha256 of the file == `sha256`, remembered in-process per (normcase path, size, mtime_ns): an unchanged file
    is hashed once. Any OSError -> False."""
    lp = fsops.long_path(Path(path))
    try:
        st = os.stat(lp)
    except OSError:
        return False
    key = (os.path.normcase(str(fsops.plain_path(Path(path)))), st.st_size, st.st_mtime_ns)
    with _SHA_LOCK:
        known = _SHA_CACHE.get(key)
    if known is None:
        try:
            known = file_sha256(lp)
        except OSError:
            return False
        with _SHA_LOCK:
            if len(_SHA_CACHE) >= _SHA_CACHE_MAX:
                _SHA_CACHE.clear()
            _SHA_CACHE[key] = known
    return known == sha256


def _work_copy(doc: dict) -> Path | None:
    wc = doc.get("work_copy_path")
    if not wc:
        return None
    w = Path(wc)
    if w.is_file():
        return w
    if w.is_dir():
        for f in sorted(w.glob("src.*")):
            if f.is_file():
                return f
    return None


def _source_copy(doc: dict, store=None) -> Path | None:
    """The document's source copy when it still has the document's content: trusted while size and mtime_ns equal
    the DB record, else re-hashed (and, with `store`, the record refreshed when the content still matches)."""
    scp = doc.get("source_copy_path")
    if not scp:
        return None
    p = Path(scp)
    lp = fsops.long_path(p)
    try:
        st = os.stat(lp)
    except OSError:
        return None
    if not os.path.isfile(lp):
        return None
    if st.st_size == doc.get("source_copy_size") and st.st_mtime_ns == doc.get("source_copy_mtime_ns"):
        return p
    try:
        ok = file_sha256(lp) == doc.get("sha256")
    except OSError:
        return None
    if not ok:
        return None
    if store is not None and doc.get("id"):
        try:
            store.update_document(doc["id"], source_copy_size=st.st_size, source_copy_mtime_ns=st.st_mtime_ns)
        except Exception:  # noqa: BLE001, S110  a refresh is an optimisation; never fail a lookup over it
            pass
    return p


def resolve_source(doc: dict, *, pdf_only: bool = False, store=None) -> ResolvedSource | None:
    """THE source lookup of a document (Document view, Office preview, verify, reassess, reconvert): the retained
    work copy (ours; not re-hashed) -> the source copy (sha-verified, cached by size + mtime_ns) -> the original
    (an absolute path, always sha-verified). A modified original is not this document's source. None when nothing
    is left. `pdf_only`: only a .pdf counts (reassess, verify)."""
    def ok(p: Path) -> bool:
        return not pdf_only or p.suffix.lower() == ".pdf"

    w = _work_copy(doc)
    if w is not None and ok(w):
        return ResolvedSource(fsops.long_path(w), "work_copy")
    c = _source_copy(doc, store)
    if c is not None and ok(c):
        return ResolvedSource(fsops.long_path(c), "source_copy")
    src = Path(str(doc.get("source_path") or ""))
    if src.is_absolute() and ok(src) and os.path.isfile(fsops.long_path(src)) and doc.get("sha256") \
            and sha_matches_cached(src, doc["sha256"]):
        return ResolvedSource(fsops.long_path(src), "original")
    return None


def document_source(doc: dict) -> Path | None:
    """Thin wrapper: the path `resolve_source` finds, or None."""
    r = resolve_source(doc)
    return r.path if r is not None else None
