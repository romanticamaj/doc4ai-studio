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


def copy_verified(src: Path, dst: Path, expected_sha: str) -> tuple[int, int]:
    """Copy `src` to `dst` atomically and verified: a temp file in dst's folder, its sha256 computed by reading the
    written file again, then os.replace. Keeps the original's mtime. Returns (size, mtime_ns of dst).
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
            fsops.retry_fs(lambda: os.replace(fsops.long_path(tmp), fsops.long_path(dst)))
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


def record_copy(store, doc_id: str, result: CopyResult | None, error: CopyError | None) -> tuple[str, str | None]:
    """Write the outcome to the documents row; (status, error text). A path another document already owns (a race
    the planner could not see) is recorded as a collision."""
    import sqlite3
    if result is not None:
        try:
            store.set_source_copy(doc_id, path=str(fsops.plain_path(result.path)), status="copied", error=None,
                                  size=result.size, mtime_ns=result.mtime_ns)
            return "copied", None
        except sqlite3.IntegrityError:
            error = CopyError("collision", f"{result.path} belongs to another document")
    text = error.text() if error is not None else "io_error: unknown"
    store.set_source_copy(doc_id, path=None, status="failed", error=text)
    return "failed", text


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
