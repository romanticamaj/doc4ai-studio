"""Source copies (spec 2026-10-29 §4-§5, §7-§8): a permanent, sha256-verified copy of every input file under
`<Doc4AI folder>/source/<output dir name>/<original file name>`.

Never fabricate a copy: a document only ever gets `source_copy_path` for a file whose sha256 was computed after
writing and equals `documents.sha256`. Every copy I/O goes through `fsops.long_path`; the DB and the sidecar store
plain paths.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from aidoc import fsops
from aidoc.names import sanitize_stem, sha8

SOURCE_COPY_RESERVE_BYTES = 256 * 1024 * 1024     # free space a copy must leave on the disk (spec §5.2 step 2)
COPY_CHUNK = 8 * 1024 * 1024
PART_MAX_AGE_S = 3600                             # interrupted copies (`.<file>.<hex>.part`) older than this are swept
NAME_SUFFIX_MAX = 16                              # a longer "extension" is part of the stem (spec §4.1)
CODES = ("no_space", "unreadable", "sha_mismatch", "collision", "io_error")
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class CopyError(Exception):
    """code: no_space | unreadable | sha_mismatch | collision | io_error."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message = code, message

    def text(self) -> str:
        """`<code>: <detail>` as stored in documents.source_copy_error."""
        return f"{self.code}: {self.message}" if self.message else self.code


@dataclass(frozen=True)
class Planned:
    path: Path               # plain absolute path
    present: bool            # the target already holds the expected content (spec §4.3 rule 1)


# ---------------------------------------------------------------- naming (spec §4.1)

def _base_name(display: str) -> str:
    """The last component of a local path or an upload display name (either separator)."""
    s = str(display or "")
    if "\\" in s or re.match(r"^[A-Za-z]:[\\/]", s):
        return PureWindowsPath(s).name
    return PurePosixPath(s).name if "/" in s else s


def copy_name(display: str) -> str:
    """The copy's file name: the original name with the stem made Windows-safe (names.sanitize_stem rules, at most
    150 characters; Chinese and full-width characters are kept verbatim) and the extension kept as it is (illegal
    characters -> "_"; an extension of more than NAME_SUFFIX_MAX characters counts as part of the stem)."""
    name = _base_name(display)
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem.strip(" .") or len(ext) > NAME_SUFFIX_MAX or not ext:
        stem, suffix = name, ""
    else:
        suffix = "." + _ILLEGAL.sub("_", ext)
    if not stem.strip():
        return "source" + suffix
    clean = sanitize_stem(stem)
    if clean == "untitled" and _ILLEGAL.sub("_", stem).strip(" .") == "":
        clean = "source"
    return clean + suffix


# ---------------------------------------------------------------- file helpers

def file_matches(path: Path, sha256: str) -> bool:
    """True when the file's sha256 is `sha256` (long-path safe; any OSError -> False)."""
    h = hashlib.sha256()
    try:
        with open(fsops.long_path(Path(path)), "rb") as f:
            while block := f.read(1 << 20):
                h.update(block)
    except OSError:
        return False
    return h.hexdigest() == sha256


def _is_file(p: Path) -> bool:
    try:
        return os.path.isfile(fsops.long_path(p))
    except OSError:
        return False


def is_present(path) -> bool:
    """A cheap existence check of a recorded copy (no hashing; long-path safe)."""
    return bool(path) and _is_file(Path(path))


def _is_part(name: str) -> bool:
    return name.startswith(".") and name.endswith(".part")


# ---------------------------------------------------------------- planning (spec §4.3)

def _occupied(store, target: Path, sha256: str, doc_id: str | None) -> bool | None:
    """None: free (copy there); True: occupied (try the next place); False: the target already holds the content."""
    owner = store.document_by_source_copy(str(target)) if store is not None else None
    if owner is not None and owner["id"] != doc_id:
        return True
    folder = fsops.long_path(target.parent)
    if os.path.exists(folder) and not os.path.isdir(folder):
        return True                                        # a file stands where the folder should be
    if _is_file(target):
        return not file_matches(target, sha256)        # False = already holds the content
    if os.path.isdir(folder):
        try:
            others = [n for n in os.listdir(folder) if n != target.name and not _is_part(n)]
        except OSError:
            others = []
        if others:
            return True
    return None


def plan_path(store, source_root: Path, *, output_dir: str, source_display: str, sha256: str,
              doc_id: str | None) -> Planned:
    """Where this document's copy goes: source/<output dir name>/<file>, else source/<dir>-<sha8>/<file> when the
    first place is occupied; both occupied -> CopyError("collision"). Nothing is written."""
    source_root = fsops.plain_path(Path(source_root))
    name = copy_name(source_display)
    stem = Path(str(output_dir)).name or "source"
    places = [stem, f"{stem}-{sha8(sha256)}"]
    for d in places:
        target = source_root / d / name
        state = _occupied(store, target, sha256, doc_id)
        if state is None:
            return Planned(target, False)
        if state is False:
            return Planned(target, True)
    raise CopyError("collision", f"{source_root / places[-1] / name} is taken")


# ---------------------------------------------------------------- the copy (spec §5.2)

def _dest_error(e: OSError, what: Path) -> CopyError:
    if e.errno == errno.ENOSPC or getattr(e, "winerror", None) in (39, 112):
        return CopyError("no_space", f"{what}: {e.strerror or e}")
    return CopyError("io_error", f"[errno {e.errno}] {e.strerror or e}: {what}")


def _write_part(src: Path, tmp: Path) -> None:
    """Stream `src` into the new temp file `tmp` (COPY_CHUNK blocks, flushed and fsynced). Read errors are
    CopyError("unreadable"), write errors no_space / io_error. Content only: no permission bits are copied, so the
    copy of a read-only original is an ordinary writable file."""
    try:
        fin = open(fsops.long_path(src), "rb")             # noqa: SIM115  closed below
    except OSError as e:
        raise CopyError("unreadable", f"{src}: {e.strerror or e}") from e
    try:
        try:
            fout = open(fsops.long_path(tmp), "xb")         # noqa: SIM115
        except OSError as e:
            raise _dest_error(e, tmp.parent) from e
        with fout:
            while True:
                try:
                    block = fin.read(COPY_CHUNK)
                except OSError as e:
                    raise CopyError("unreadable", f"{src}: {e.strerror or e}") from e
                if not block:
                    break
                try:
                    fout.write(block)
                except OSError as e:
                    raise _dest_error(e, tmp.parent) from e
                del block
            try:
                fout.flush()
                os.fsync(fout.fileno())
            except OSError as e:
                raise _dest_error(e, tmp.parent) from e
    finally:
        fin.close()


def _place(tmp: Path, dst: Path) -> None:
    """Move the verified temp file to `dst` without ever overwriting: a file another writer put there meanwhile
    (a conversion and a backfill planning the same name) makes this a collision and stays untouched (verifier V2).
    A hardlink is atomic and refuses an existing name; where links are not supported, Windows' rename refuses one
    too."""
    ltmp, ldst = fsops.long_path(tmp), fsops.long_path(dst)
    try:
        os.link(ltmp, ldst)
    except FileExistsError:
        raise CopyError("collision", f"{dst} was created by another writer meanwhile") from None
    except OSError:
        if os.name != "nt" and os.path.lexists(ldst):
            raise CopyError("collision", f"{dst} was created by another writer meanwhile") from None
        try:
            fsops.retry_fs(lambda: os.rename(ltmp, ldst))      # Windows: never replaces an existing file
        except FileExistsError:
            raise CopyError("collision", f"{dst} was created by another writer meanwhile") from None
        return
    os.unlink(ltmp)


def copy_verified(src: Path, dst: Path, expected_sha: str) -> tuple[int, int]:
    """Copy `src` to `dst` atomically and verified: a temp file in dst's folder, its sha256 computed by reading the
    written file again, then a non-overwriting placement (_place). Keeps the original's mtime. Returns (size, mtime_ns of dst).
    Raises CopyError(no_space | unreadable | sha_mismatch | io_error); no temp file is ever left behind."""
    src, dst = Path(src), fsops.plain_path(Path(dst))
    try:
        st = os.stat(fsops.long_path(src))
    except OSError as e:
        raise CopyError("unreadable", f"{src}: {e.strerror or e}") from e
    try:
        os.makedirs(fsops.long_path(dst.parent), exist_ok=True)
    except OSError as e:
        raise _dest_error(e, dst.parent) from e
    try:
        free = shutil.disk_usage(fsops.long_path(dst.parent)).free
    except OSError as e:
        raise _dest_error(e, dst.parent) from e
    if free - st.st_size < SOURCE_COPY_RESERVE_BYTES:
        raise CopyError("no_space", f"{dst.parent}: {free} bytes free, {st.st_size} needed "
                                    f"+ {SOURCE_COPY_RESERVE_BYTES} reserve")
    tmp = dst.parent / f".{dst.name}.{os.urandom(4).hex()}.part"
    try:
        _write_part(src, tmp)
        if not file_matches(tmp, expected_sha):
            raise CopyError("sha_mismatch", f"{src} (the copy does not have the document's sha256)")
        try:
            _place(tmp, dst)
            os.utime(fsops.long_path(dst), ns=(st.st_atime_ns, st.st_mtime_ns))
            out = os.stat(fsops.long_path(dst))
        except OSError as e:
            raise _dest_error(e, dst) from e
        return out.st_size, out.st_mtime_ns
    finally:
        try:
            os.unlink(fsops.long_path(tmp))
        except FileNotFoundError:
            pass
        except OSError:
            pass                                           # swept by recovery (purge_stale_parts)


def link_or_copy(existing: Path, dst: Path, expected_sha: str) -> str:
    """A copy at `dst` from another document's verified copy of the same content (spec §4.5): a hardlink when the
    filesystem allows it ("linked"), else a full verified copy ("copied"). `existing` is verified first."""
    existing, dst = Path(existing), fsops.plain_path(Path(dst))
    if not file_matches(existing, expected_sha):
        raise CopyError("sha_mismatch", f"{existing}")
    try:
        os.makedirs(fsops.long_path(dst.parent), exist_ok=True)
    except OSError as e:
        raise _dest_error(e, dst.parent) from e
    try:
        os.link(fsops.long_path(existing), fsops.long_path(dst))
        return "linked"
    except FileExistsError:
        raise CopyError("collision", f"{dst} was created by another writer meanwhile") from None
    except OSError:
        pass                                               # other volume, FAT/exFAT, no permission: copy
    copy_verified(existing, dst, expected_sha)
    return "copied"


def stat_copy(path: Path) -> tuple[int, int]:
    st = os.stat(fsops.long_path(Path(path)))
    return st.st_size, st.st_mtime_ns


def remove_copy(path: Path) -> int:
    """Delete one copy; returns the bytes freed (0 when another hardlink keeps the data, or nothing was there).
    Its folder goes too when it is empty. Any other OSError propagates (the caller reports the copy as kept)."""
    p = fsops.plain_path(Path(path))
    lp = fsops.long_path(p)
    try:
        st = os.stat(lp)
    except FileNotFoundError:
        return 0
    fsops.retry_fs(lambda: os.unlink(lp))
    try:
        os.rmdir(fsops.long_path(p.parent))
    except OSError:
        pass                                               # not empty (or in use): kept
    return st.st_size if st.st_nlink <= 1 else 0


# ---------------------------------------------------------------- one document's copy (conversion, retry, backfill)

@dataclass
class CopyResult:
    path: Path
    size: int
    mtime_ns: int
    created: bool            # this call wrote the file (a rollback may remove it)
    how: str                 # "present" | "copied" | "linked"


def copy_trusted(doc: dict) -> bool:
    """The document's recorded copy is there with its content: size + mtime_ns as recorded, else re-hashed."""
    scp = doc.get("source_copy_path")
    if not scp:
        return False
    try:
        size, mtime_ns = stat_copy(Path(scp))
    except OSError:
        return False
    if size == doc.get("source_copy_size") and mtime_ns == doc.get("source_copy_mtime_ns"):
        return True
    return file_matches(Path(scp), doc["sha256"])


def verified_copy_of(store, sha256: str, exclude_id: str | None = None) -> Path | None:
    """Another document's copy of the same content that still matches (the hardlink source, spec §4.5)."""
    for d in store.documents_by_sha(sha256):
        if d["id"] == exclude_id or d.get("source_copy_status") != "copied":
            continue
        if copy_trusted(d) and file_matches(Path(d["source_copy_path"]), sha256):
            return Path(d["source_copy_path"])
    return None


def make_copy(store, source_root: Path, *, doc_id: str | None, output_dir: str, source_display: str,
              sha256: str, src: Path | None) -> CopyResult:
    """Plan and write one document's copy: reuse a matching target, else hardlink / copy another document's verified
    copy of the same content, else copy `src` (a sha-verified file: the work copy, or a backfill candidate).
    Raises CopyError."""
    planned = plan_path(store, source_root, output_dir=output_dir, source_display=source_display, sha256=sha256,
                        doc_id=doc_id)
    if planned.present:
        size, mtime_ns = stat_copy(planned.path)
        return CopyResult(planned.path, size, mtime_ns, False, "present")
    other = verified_copy_of(store, sha256, exclude_id=doc_id)
    if other is not None:
        how = link_or_copy(other, planned.path, sha256)
    elif src is not None:
        copy_verified(src, planned.path, sha256)
        how = "copied"
    else:
        raise CopyError("unreadable", "no verified source to copy from")
    try:
        size, mtime_ns = stat_copy(planned.path)
    except OSError as e:
        raise _dest_error(e, planned.path) from e
    return CopyResult(planned.path, size, mtime_ns, True, how)


# Every write of a documents.source_copy_* row, and every removal of a copy file this process placed, happens under
# this lock: "who owns this path" is checked and acted on in one step (verifier V5).
RECORD_LOCK = threading.RLock()


def discard_copy(store, result: CopyResult | None) -> bool:
    """Remove a copy this call created — unless a document owns that path by now (another writer adopted the
    verified file meanwhile; then it is theirs and stays). True when the file was removed."""
    if result is None or not result.created:
        return False
    with RECORD_LOCK:
        if store.document_by_source_copy(str(result.path)) is not None:
            return False
        try:
            remove_copy(result.path)
        except OSError:
            return False
        return True


def record_copy(store, doc_id: str, result: CopyResult | None, error: CopyError | None, *,
                adopt: dict | None = None) -> tuple[str, str | None, CopyResult | None]:
    """Write the outcome to the documents row -> (status, error text, the copy finally recorded).

    The path can be taken in the meantime (unique index): another document — a backfill or retry that found this
    very file already present — owns it now. The file is then theirs and is never removed; with `adopt`
    ({source_root, output_dir, source_display, sha256}) this document takes its own path next to it, hardlinked to
    that verified copy (make_copy re-plans: the owned path is occupied). Otherwise, or when adopting fails, it is a
    collision, and a file this call created that nobody owns is removed."""
    import sqlite3
    with RECORD_LOCK:
        if result is not None:
            try:
                store.set_source_copy(doc_id, path=str(fsops.plain_path(result.path)), status="copied", error=None,
                                      size=result.size, mtime_ns=result.mtime_ns)
                return "copied", None, result
            except sqlite3.IntegrityError:
                owner = store.document_by_source_copy(str(result.path))
                error = CopyError("collision", f"{result.path} belongs to another document")
                if owner is not None and owner["id"] != doc_id and adopt is not None:
                    try:
                        mine = make_copy(store, adopt["source_root"], doc_id=doc_id, output_dir=adopt["output_dir"],
                                         source_display=adopt["source_display"], sha256=adopt["sha256"], src=None)
                        store.set_source_copy(doc_id, path=str(fsops.plain_path(mine.path)), status="copied",
                                              error=None, size=mine.size, mtime_ns=mine.mtime_ns)
                        return "copied", None, mine
                    except CopyError as e:
                        error = e
                    except sqlite3.IntegrityError:
                        pass
                if owner is None:
                    discard_copy(store, result)
        text = error.text() if error is not None else "io_error: unknown"
        store.set_source_copy(doc_id, path=None, status="failed", error=text)
        return "failed", text, None


# ---------------------------------------------------------------- sidecar (spec §9.2)

def sidecar_info(source_root: Path, path: Path, sha256: str, size: int) -> dict:
    """{"path": "source/<dir>/<file>" (relative to the Doc4AI folder, POSIX), "sha256", "size"}."""
    root = fsops.plain_path(Path(source_root))
    rel = fsops.plain_path(Path(path)).relative_to(root.parent).as_posix()
    return {"path": rel, "sha256": sha256, "size": int(size)}


def write_sidecar_copy(output_dir: Path, info: dict | None) -> bool:
    """Set only the sidecar's "source_copy" key (read -> change -> atomic write); False when there is no readable
    sidecar (the caller then only updates the DB)."""
    out = Path(output_dir)
    p = out / f"{out.name}.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    data["source_copy"] = info
    try:
        fsops.atomic_write_json(p, data)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------- recovery (spec §8, step 6)

def purge_stale_parts(roots: list[Path], now: float) -> int:
    """Remove `.<file>.<hex>.part` files older than PART_MAX_AGE_S under each root (interrupted copies)."""
    n, seen = 0, set()
    for root in roots:
        root = Path(root)
        key = os.path.normcase(os.path.abspath(str(root)))
        if key in seen:
            continue
        seen.add(key)
        if not os.path.isdir(fsops.long_path(root)):
            continue
        for dirpath, _dirs, files in os.walk(fsops.long_path(root)):
            for name in files:
                if not _is_part(name):
                    continue
                f = os.path.join(dirpath, name)
                try:
                    if now - os.stat(f).st_mtime > PART_MAX_AGE_S:
                        os.unlink(f)
                        n += 1
                except OSError:
                    continue
    return n


# ---------------------------------------------------------------- backfill, retry, summary (spec §7, §10.1)

OUTCOMES = ("copied", "already_present", "missing_original", "sha_mismatch", "unreadable", "failed",
            "skipped_orphaned")


class BackfillRunning(Exception):
    """Another backfill is running (single flight)."""


class InsufficientDisk(Exception):
    def __init__(self, needed: int, free: int):
        super().__init__(f"insufficient disk: needed {needed}, free {free}")
        self.needed, self.free = needed, free


_BACKFILL_LOCK = threading.Lock()


def candidates_for(store, doc: dict) -> list[tuple[Path, str]]:
    """Files that may hold this document's content, in order (spec §7.2): its work copy, its original (an absolute
    source_path), then other documents with the same sha256: their copy, work copy, original. Only paths that exist
    are listed; none of them is trusted until its sha256 is checked."""
    from aidoc.sources import _work_copy
    out: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def add(p: Path | None, kind: str) -> None:
        if p is None:
            return
        key = os.path.normcase(str(p))
        if key in seen or not _is_file(p):
            return
        seen.add(key)
        out.append((Path(p), kind))

    def original(d: dict) -> Path | None:
        s = Path(str(d.get("source_path") or ""))
        return s if s.is_absolute() else None

    add(_work_copy(doc), "work_copy")
    add(original(doc), "original")
    for other in store.documents_by_sha(doc["sha256"]):
        if other["id"] == doc["id"]:
            continue
        add(Path(other["source_copy_path"]) if other.get("source_copy_path") else None, "other_document")
        add(_work_copy(other), "other_document")
        add(original(other), "other_document")
    return out


def _check(path: Path, sha256: str) -> str:
    """match | mismatch | unreadable (locked / no permission: worth retrying later, never a mismatch)."""
    h = hashlib.sha256()
    try:
        with open(fsops.long_path(path), "rb") as f:
            while block := f.read(1 << 20):
                h.update(block)
    except FileNotFoundError:
        return "mismatch"
    except OSError:
        return "unreadable"
    return "match" if h.hexdigest() == sha256 else "mismatch"


def _set_status(store, doc: dict, emit, *, path, status, error, size=None, mtime_ns=None) -> None:
    new_path = None if path is None else str(fsops.plain_path(Path(path)))
    changed = (doc.get("source_copy_status"), doc.get("source_copy_error"), doc.get("source_copy_path")) != \
        (status, error, new_path)
    with RECORD_LOCK:
        store.set_source_copy(doc["id"], path=new_path, status=status, error=error, size=size, mtime_ns=mtime_ns)
    if changed and emit is not None:
        emit("document.source_copy", {"document_id": doc["id"], "status": status, "error": error})


def _fail_row(store, doc, emit, row, outcome: str, error: str, status: str = "failed") -> dict:
    row.update(outcome=outcome, detail=error)
    _set_status(store, doc, emit, path=None, status=status, error=error)
    return row


def ensure_copy(store, config, doc: dict, *, dry_run: bool = False, emit=None) -> dict:
    """One document through rows 0-5 of spec §7.2 -> {id, stem, outcome, from, size, detail}. Without dry_run the
    DB, the sidecar's source_copy key and (on a status change) a document.source_copy event are written. dry_run
    reads and hashes candidates but writes nothing at all."""
    row = {"id": doc["id"], "stem": Path(doc["output_dir"]).name, "source": doc.get("source_path"),
           "outcome": None, "from": None, "size": None, "detail": None}
    if doc.get("status") == "orphaned":
        row["outcome"] = "skipped_orphaned"
        return row
    sha, root = doc["sha256"], config.source_root()
    planned, plan_error = None, None
    try:
        planned = plan_path(store, root, output_dir=doc["output_dir"], source_display=doc["source_path"],
                            sha256=sha, doc_id=doc["id"])
    except CopyError as e:
        plan_error = e
    # 1: the recorded copy (or the planned target) already holds the content
    rec = doc.get("source_copy_path")
    present = Path(rec) if rec and file_matches(Path(rec), sha) else None
    if present is None and planned is not None and planned.present:
        present = planned.path
    if present is not None:
        size, mtime_ns = stat_copy(present)
        row.update(outcome="already_present", size=size)
        if not dry_run:
            _set_status(store, doc, emit, path=present, status="copied", error=None, size=size, mtime_ns=mtime_ns)
        return row
    # 2-5: candidates, each sha-checked; never inferred from names or sizes ("never fabricate")
    # another document's verified copy of the same content comes first: it is the only file a backup may share its
    # data with (a hardlink, spec §4.5). Work copies and originals — the user's own files — are always copied.
    shared = verified_copy_of(store, sha, exclude_id=doc["id"])
    checks = [(p, kind, _check(p, sha)) for p, kind in candidates_for(store, doc)]
    match = (shared, "other_document") if shared is not None else         next(((p, kind) for p, kind, c in checks if c == "match"), None)
    if match is not None:
        src, kind = match
        row["from"] = kind
        row["size"] = os.stat(fsops.long_path(src)).st_size
        if planned is None:
            if dry_run:
                row.update(outcome="failed", detail=plan_error.text())
                return row
            return _fail_row(store, doc, emit, row, "failed", plan_error.text())
        if dry_run:
            row["outcome"] = "copied"
            return row
        try:
            if shared is not None and src == shared:
                link_or_copy(src, planned.path, sha)          # a verified source copy: hardlink allowed
            else:
                copy_verified(src, planned.path, sha)         # never a hardlink to a user's file or a work copy
            size, mtime_ns = stat_copy(planned.path)
        except CopyError as e:
            return _fail_row(store, doc, emit, row, "unreadable" if e.code == "unreadable" else "failed", e.text())
        except OSError as e:
            return _fail_row(store, doc, emit, row, "failed", _dest_error(e, planned.path).text())
        placed = CopyResult(planned.path, size, mtime_ns, True, "copied")
        status, text, final = record_copy(store, doc["id"], placed, None,
                                          adopt={"source_root": root, "output_dir": doc["output_dir"],
                                                 "source_display": doc["source_path"], "sha256": sha})
        if emit is not None and status != doc.get("source_copy_status"):
            emit("document.source_copy", {"document_id": doc["id"], "status": status, "error": text})
        if status != "copied":
            row.update(outcome="failed", detail=text)
            return row
        size = final.size
        row.update(outcome="copied", size=size)
        if not write_sidecar_copy(Path(doc["output_dir"]), sidecar_info(root, final.path, sha, size)):
            row["detail"] = "sidecar not updated (missing or unreadable)"
        return row
    unreadable = [p for p, _k, c in checks if c == "unreadable"]
    if unreadable:
        row.update(outcome="unreadable", detail=f"unreadable: {unreadable[0]}")
        if not dry_run:
            _set_status(store, doc, emit, path=None, status="failed", error=row["detail"])
        return row
    if checks:
        row.update(outcome="sha_mismatch", detail=f"sha_mismatch: {checks[0][0]}")
        if not dry_run:
            _set_status(store, doc, emit, path=None, status="unrecoverable", error=row["detail"])
        return row
    row.update(outcome="missing_original", detail="missing_original")
    if not dry_run:
        _set_status(store, doc, emit, path=None, status="unrecoverable", error="missing_original")
    return row


def free_bytes(p: Path) -> int | None:
    """Free bytes on the volume of `p` (its nearest existing ancestor); None when unknown."""
    anc = Path(p)
    while not anc.exists() and anc.parent != anc:
        anc = anc.parent
    try:
        return shutil.disk_usage(fsops.long_path(anc)).free
    except OSError:
        return None


def unreferenced_files(store, source_root: Path) -> int:
    """Files under the source root that no document references (reported, never deleted)."""
    root = fsops.long_path(Path(source_root))
    if not os.path.isdir(root):
        return 0
    def norm(p) -> str:                       # the same rule as the ownership checks (store.document_by_source_copy)
        return os.path.normcase(os.path.realpath(str(fsops.plain_path(Path(p)))))
    refs = {norm(d["source_copy_path"]) for d in store.list_documents() if d.get("source_copy_path")}
    n = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if _is_part(name):
                continue
            if norm(Path(dirpath) / name) not in refs:
                n += 1
    return n


def backfill(store, config, *, dry_run: bool, progress=None, emit=None) -> dict:
    """spec §7: every document (oldest first) through ensure_copy; the §7.4 report. Single flight
    (BackfillRunning). A real run first checks the disk: bytes to copy + reserve > free -> InsufficientDisk and
    nothing is copied."""
    import time as _time
    if not _BACKFILL_LOCK.acquire(blocking=False):
        raise BackfillRunning()
    try:
        started = _time.time()
        root = config.source_root()
        docs = sorted(store.list_documents(), key=lambda d: (d.get("created_at") or 0, d["id"]))
        if not dry_run:
            plan = [ensure_copy(store, config, d, dry_run=True) for d in docs]
            needed = sum(r["size"] or 0 for r in plan if r["outcome"] == "copied")
            free = free_bytes(root)
            if needed and (free is None or needed + SOURCE_COPY_RESERVE_BYTES > free):
                raise InsufficientDisk(needed + SOURCE_COPY_RESERVE_BYTES, free or 0)
        rows = []
        for i, d in enumerate(docs, 1):
            current = store.get_document(d["id"]) or d
            rows.append(ensure_copy(store, config, current, dry_run=dry_run, emit=emit))
            if progress is not None:
                progress({"dry_run": dry_run, "done": i, "total": len(docs)})
        totals = {k: 0 for k in OUTCOMES}
        for r in rows:
            totals[r["outcome"]] += 1
        moved = sum(r["size"] or 0 for r in rows if r["outcome"] == "copied")
        report = {"dry_run": dry_run, "source_dir": str(root), "started_at": started, "finished_at": _time.time(),
                  "totals": totals, "bytes_needed": moved if dry_run else 0, "bytes_copied": 0 if dry_run else moved,
                  "free_bytes": free_bytes(root), "documents": rows,
                  "unreferenced_files": unreferenced_files(store, root)}
        if progress is not None:
            progress({"dry_run": dry_run, "finished": True, "totals": totals})
        return report
    finally:
        _BACKFILL_LOCK.release()


def not_copied(doc: dict) -> bool:
    """Flag source_not_copied: status is not copied, or it is but the file is gone (orphaned rows never count)."""
    if doc.get("status") == "orphaned":
        return False
    if doc.get("source_copy_status") != "copied" or not doc.get("source_copy_path"):
        return True
    return not _is_file(Path(doc["source_copy_path"]))


def summary(store, config) -> dict:
    """GET /api/sources: counts over the documents that are not orphaned."""
    docs = [d for d in store.list_documents() if d.get("status") != "orphaned"]
    copied = [d for d in docs if not not_copied(d)]
    root = config.source_root()
    return {"root_dir": str(config.library_root()), "source_dir": str(root), "documents": len(docs),
            "copied": len(copied), "not_copied": len(docs) - len(copied),
            "failed": sum(1 for d in docs if d.get("source_copy_status") == "failed"),
            "unrecoverable": sum(1 for d in docs if d.get("source_copy_status") == "unrecoverable"),
            "file_missing": sum(1 for d in docs if d.get("source_copy_status") == "copied" and not_copied(d)),
            "bytes": sum(int(d.get("source_copy_size") or 0) for d in copied), "free_bytes": free_bytes(root)}
