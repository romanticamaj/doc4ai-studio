"""source_copy core (spec 2026-10-29 §4-§5; plan Task 3)."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

from doc4ai import fsops, source_copy
from doc4ai.source_copy import (
    CopyError,
    copy_name,
    copy_verified,
    file_matches,
    link_or_copy,
    plan_path,
    purge_stale_parts,
    remove_copy,
    sidecar_info,
    write_sidecar_copy,
)
from doc4ai.store import Store


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "db" / "aidoc.db")
    yield s
    s.close()


def _doc(store, n: int, sha: str, out: str) -> str:
    return store.upsert_document(sha256=sha, source_path=f"C:\\x{n}.pdf", output_dir=out, engine="fake",
                                 quality={"level": "ok"}, pages=1, lang="cht", aidoc_version="0.1.0", status="ok")


def test_constants():
    from doc4ai.config import ROOT_DIR_MAX
    from doc4ai.store import SCHEMA_VERSION
    assert source_copy.SOURCE_COPY_RESERVE_BYTES == 256 * 1024 * 1024
    assert source_copy.COPY_CHUNK == 8 * 1024 * 1024
    assert source_copy.PART_MAX_AGE_S == 3600
    assert ROOT_DIR_MAX == 200 and fsops.LONG_PATH_AT == 240 and source_copy.NAME_SUFFIX_MAX == 16
    assert SCHEMA_VERSION == 8            # v7 added source_copy_*; v8 the OAuth tables (spec 2026-11-05)


@pytest.mark.parametrize("display, expected", [
    ("第一章 測試（草稿）.pdf", "第一章 測試（草稿）.pdf"),
    ("a:b?.pdf", "a_b_.pdf"), ("CON.docx", "CON_.docx"), (".hidden.pdf", "_hidden.pdf"),
    ("x" * 300 + ".pdf", "x" * 150 + ".pdf"), ("", "source"), ("report." + "e" * 20, "report." + "e" * 20),
    ("C:\\Users\\x\\Downloads\\報告.docx", "報告.docx"), ("/home/u/a b.PDF", "a b.PDF"), ("???.pdf", "___.pdf")])
def test_copy_name(display, expected):
    assert copy_name(display) == expected


def test_copy_verified_is_atomic_and_verified(tmp_path):
    src = tmp_path / "in" / "a.pdf"
    src.parent.mkdir()
    data = os.urandom(300_000)
    src.write_bytes(data)
    os.utime(src, ns=(1_600_000_000_000_000_000, 1_600_000_000_123_456_700))
    dst = tmp_path / "lib" / "source" / "a" / "a.pdf"
    size, mtime_ns = copy_verified(src, dst, _sha(data))
    assert dst.read_bytes() == data and size == len(data)
    assert mtime_ns == dst.stat().st_mtime_ns == src.stat().st_mtime_ns
    assert [p.name for p in dst.parent.iterdir()] == ["a.pdf"]          # no .part left
    assert os.access(dst, os.W_OK)


def test_verify_rereads_the_written_file(tmp_path, monkeypatch):
    """Review Focus 2: the check reads the written temp file back, it does not hash the bytes on their way in."""
    src = tmp_path / "a.pdf"
    data = b"%PDF-1.4 " + os.urandom(10_000)
    src.write_bytes(data)
    real = source_copy._write_part

    def corrupting(s, tmp):
        real(s, tmp)                                    # written, flushed and fsynced ...
        with open(tmp, "r+b") as f:                     # ... then one byte flips on disk
            f.seek(5)
            b = f.read(1)
            f.seek(5)
            f.write(bytes([b[0] ^ 0xFF]))
    monkeypatch.setattr(source_copy, "_write_part", corrupting)
    dst = tmp_path / "source" / "a" / "a.pdf"
    with pytest.raises(CopyError) as e:
        copy_verified(src, dst, _sha(data))
    assert e.value.code == "sha_mismatch"
    assert not dst.exists() and list(dst.parent.iterdir()) == []


def test_wrong_expected_sha_is_a_mismatch(tmp_path):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"abc")
    with pytest.raises(CopyError) as e:
        copy_verified(src, tmp_path / "s" / "a.pdf", _sha(b"other"))
    assert e.value.code == "sha_mismatch" and not (tmp_path / "s" / "a.pdf").exists()


def test_no_space(tmp_path, monkeypatch):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"x" * 1000)
    real = source_copy.shutil.disk_usage

    def usage(p):
        u = real(p)
        return type(u)(u.total, u.used, 1000 + source_copy.SOURCE_COPY_RESERVE_BYTES - 1)
    monkeypatch.setattr(source_copy.shutil, "disk_usage", usage)
    dst = tmp_path / "s" / "a" / "a.pdf"
    with pytest.raises(CopyError) as e:
        copy_verified(src, dst, _sha(b"x" * 1000))
    assert e.value.code == "no_space" and not dst.exists()


def test_read_only_original_gives_a_writable_copy(tmp_path):
    src = tmp_path / "ro.pdf"
    src.write_bytes(b"read only")
    os.chmod(src, stat.S_IREAD)
    try:
        dst = tmp_path / "s" / "ro" / "ro.pdf"
        copy_verified(src, dst, _sha(b"read only"))
        assert os.access(dst, os.W_OK)
        dst.unlink()                                    # deletable without touching attributes
        assert not os.access(src, os.W_OK)              # the original is untouched
    finally:
        os.chmod(src, stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share modes")
def test_locked_original_is_unreadable(tmp_path):
    import ctypes
    from ctypes import wintypes
    src = tmp_path / "locked.pdf"
    src.write_bytes(b"locked")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(src), 0x80000000, 0, None, 3, 0x80, None)     # GENERIC_READ, share 0, OPEN_EXISTING
    assert h not in (None, wintypes.HANDLE(-1).value)
    try:
        with pytest.raises(CopyError) as e:
            copy_verified(src, tmp_path / "s" / "l" / "locked.pdf", _sha(b"locked"))
        assert e.value.code == "unreadable"
    finally:
        k32.CloseHandle(h)
    assert not list((tmp_path / "s" / "l").glob("*"))


def test_destination_is_a_file_is_io_error(tmp_path):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"a")
    (tmp_path / "source").write_text("not a folder", encoding="utf-8")
    with pytest.raises(CopyError) as e:
        copy_verified(src, tmp_path / "source" / "a" / "a.pdf", _sha(b"a"))
    assert e.value.code == "io_error" and "errno" in e.value.text()


def test_long_path_over_300_chars(tmp_path):
    src = tmp_path / "a.pdf"
    data = b"long path " * 100
    src.write_bytes(data)
    deep = tmp_path / "source" / ("d" * 150) / ("f" * 150 + ".pdf")
    assert len(str(deep)) >= 300
    size, _ = copy_verified(src, deep, _sha(data))
    assert size == len(data) and file_matches(deep, _sha(data))
    assert file_matches(fsops.long_path(deep), _sha(data))
    assert not str(deep).startswith("\\\\?\\")
    planned = plan_path(None, tmp_path / "source", output_dir=str(tmp_path / "out" / ("d" * 150)),
                        source_display="f" * 150 + ".pdf", sha256=_sha(data), doc_id=None)
    assert planned.present and planned.path == deep and not str(planned.path).startswith("\\\\?\\")
    assert remove_copy(deep) == len(data)


def test_plan_path_mirrors_output_dir(tmp_path, store):
    root = tmp_path / "lib" / "source"
    p = plan_path(store, root, output_dir=str(tmp_path / "lib" / "out" / "報告"), source_display="C:\\in\\報告.pdf",
                  sha256="a" * 64, doc_id=None)
    assert p == source_copy.Planned(root / "報告" / "報告.pdf", False)
    assert not root.exists()                                     # planning writes nothing


def test_plan_reuses_matching_target(tmp_path, store):
    root = tmp_path / "source"
    t = root / "a" / "a.pdf"
    t.parent.mkdir(parents=True)
    t.write_bytes(b"same")
    before = t.stat().st_mtime_ns
    p = plan_path(store, root, output_dir="/out/a", source_display="a.pdf", sha256=_sha(b"same"), doc_id=None)
    assert p.present and p.path == t and t.stat().st_mtime_ns == before


@pytest.mark.parametrize("taken", ["other_file", "other_sha", "db_owner", "dir_is_file"])
def test_plan_uses_sha8_dir_when_taken(tmp_path, store, taken):
    root = tmp_path / "source"
    sha = _sha(b"mine")
    if taken == "other_file":
        (root / "a").mkdir(parents=True)
        (root / "a" / "user.txt").write_text("x", encoding="utf-8")
    elif taken == "other_sha":
        (root / "a").mkdir(parents=True)
        (root / "a" / "a.pdf").write_bytes(b"different content")
    elif taken == "db_owner":
        other = _doc(store, 1, sha, "/other/a")
        store.set_source_copy(other, path=str(root / "a" / "a.pdf"), status="copied", error=None)
    else:
        root.mkdir(parents=True)
        (root / "a").write_text("a file named like the folder", encoding="utf-8")
    p = plan_path(store, root, output_dir="/out/a", source_display="a.pdf", sha256=sha, doc_id="me")
    assert p == source_copy.Planned(root / f"a-{sha[:8]}" / "a.pdf", False)


def test_plan_ignores_part_files_and_own_db_path(tmp_path, store):
    root = tmp_path / "source"
    sha = _sha(b"mine")
    (root / "a").mkdir(parents=True)
    (root / "a" / ".a.pdf.0123abcd.part").write_bytes(b"half")
    me = _doc(store, 1, sha, "/out/a")
    store.set_source_copy(me, path=str(root / "a" / "a.pdf"), status="failed", error=None)
    p = plan_path(store, root, output_dir="/out/a", source_display="a.pdf", sha256=sha, doc_id=me)
    assert p.path == root / "a" / "a.pdf" and not p.present


def test_plan_collision_only_when_every_place_is_taken(tmp_path, store, monkeypatch):
    """A collision remains only for the impossible case: all PLACE_TRIES names taken (here 3)."""
    monkeypatch.setattr(source_copy, "PLACE_TRIES", 3)
    root = tmp_path / "source"
    sha = _sha(b"mine")
    for d in ("a", f"a-{sha[:8]}", f"a-{sha[:8]}-2"):
        (root / d).mkdir(parents=True)
        (root / d / "a.pdf").write_bytes(b"someone else")
    with pytest.raises(CopyError) as e:
        plan_path(store, root, output_dir="/out/a", source_display="a.pdf", sha256=sha, doc_id=None)
    assert e.value.code == "collision"
    assert all((root / d / "a.pdf").read_bytes() == b"someone else" for d in ("a", f"a-{sha[:8]}", f"a-{sha[:8]}-2"))


def test_link_dedup_shares_data_on_ntfs(tmp_path):
    data = b"same content twice"
    first = tmp_path / "source" / "one" / "one.pdf"
    src = tmp_path / "one.pdf"
    src.write_bytes(data)
    copy_verified(src, first, _sha(data))
    second = tmp_path / "source" / "copy" / "copy.pdf"
    assert link_or_copy(first, second, _sha(data)) == "linked"
    a, b = os.stat(first), os.stat(second)
    assert a.st_ino == b.st_ino and a.st_nlink == 2 == b.st_nlink


def test_link_falls_back_to_copy(tmp_path, monkeypatch):
    data = b"no links here"
    first = tmp_path / "source" / "one" / "one.pdf"
    first.parent.mkdir(parents=True)
    first.write_bytes(data)

    def no_link(*a, **k):
        raise OSError(1, "links not supported")
    monkeypatch.setattr(source_copy.os, "link", no_link)
    second = tmp_path / "source" / "two" / "two.pdf"
    assert link_or_copy(first, second, _sha(data)) == "copied"
    assert file_matches(second, _sha(data)) and os.stat(second).st_nlink == 1


def test_link_refuses_an_unverified_source(tmp_path):
    first = tmp_path / "one.pdf"
    first.write_bytes(b"changed")
    with pytest.raises(CopyError) as e:
        link_or_copy(first, tmp_path / "s" / "x.pdf", _sha(b"original"))
    assert e.value.code == "sha_mismatch" and not (tmp_path / "s" / "x.pdf").exists()


def test_remove_copy_keeps_other_link(tmp_path):
    data = b"shared"
    a = tmp_path / "source" / "a" / "a.pdf"
    a.parent.mkdir(parents=True)
    a.write_bytes(data)
    b = tmp_path / "source" / "b" / "b.pdf"
    b.parent.mkdir(parents=True)
    os.link(a, b)
    assert remove_copy(b) == 0                                   # the data is still referenced by a
    assert a.read_bytes() == data and not b.parent.exists()      # empty folder removed
    assert remove_copy(a) == len(data) and not a.parent.exists()
    assert remove_copy(a) == 0                                   # already gone


def test_sidecar_update_touches_only_source_copy(tmp_path):
    out = tmp_path / "out" / "doc"
    out.mkdir(parents=True)
    side = {"source": "C:\\a.pdf", "sha256": "f" * 64, "pages": 3, "quality": {"level": "ok", "score": 0.9},
            "engine": "docling", "tried": [{"engine": "docling"}], "aidoc_version": "0.1.0"}
    (out / "doc.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
    info = sidecar_info(tmp_path / "lib" / "source", tmp_path / "lib" / "source" / "doc" / "a.pdf", "f" * 64, 12)
    assert info == {"path": "source/doc/a.pdf", "sha256": "f" * 64, "size": 12}
    assert write_sidecar_copy(out, info)
    after = json.loads((out / "doc.json").read_text(encoding="utf-8"))
    assert after.pop("source_copy") == info and after == side
    assert write_sidecar_copy(out, None)
    assert json.loads((out / "doc.json").read_text(encoding="utf-8"))["source_copy"] is None
    assert not write_sidecar_copy(tmp_path / "out" / "missing", info)


def test_purge_stale_parts(tmp_path):
    root = tmp_path / "source"
    (root / "a").mkdir(parents=True)
    old = root / "a" / ".a.pdf.00000000.part"
    fresh = root / "a" / ".a.pdf.11111111.part"
    real = root / "a" / "a.pdf"
    for f in (old, fresh, real):
        f.write_bytes(b"x")
    past = time.time() - source_copy.PART_MAX_AGE_S - 60
    os.utime(old, (past, past))
    os.utime(real, (past, past))
    assert purge_stale_parts([root, root / "a", tmp_path / "missing"], time.time()) == 1
    assert not old.exists() and fresh.exists() and real.exists()


def test_memory_is_bounded(tmp_path):
    src = tmp_path / "big.bin"
    block = os.urandom(1 << 20)
    h = hashlib.sha256()
    with open(src, "wb") as f:
        for i in range(64):
            chunk = i.to_bytes(4, "little") + block[4:]
            f.write(chunk)
            h.update(chunk)
    tracemalloc.start()
    try:
        copy_verified(src, tmp_path / "s" / "big" / "big.bin", h.hexdigest())
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 32 * 1024 * 1024, peak


def test_plain_and_long_path_round_trip():
    p = Path("C:\\" + "a" * 260)
    lp = fsops.long_path(p)
    if sys.platform == "win32":
        assert str(lp).startswith("\\\\?\\")
    assert fsops.plain_path(lp) == p and fsops.long_path(Path("C:\\short")) == Path("C:\\short")


def test_copy_never_overwrites_a_file_that_appeared_meanwhile(tmp_path, monkeypatch):
    """Verifier V2: another writer placed the target between planning and writing -> collision, theirs untouched."""
    src = tmp_path / "a.pdf"
    src.write_bytes(b"mine")
    dst = tmp_path / "source" / "a" / "a.pdf"
    real = source_copy._write_part

    def racing(s, tmp):
        real(s, tmp)
        dst.write_bytes(b"theirs")                   # lands after our temp file, before our placement
    monkeypatch.setattr(source_copy, "_write_part", racing)
    with pytest.raises(CopyError) as e:
        copy_verified(src, dst, _sha(b"mine"))
    assert e.value.code == "collision" and dst.read_bytes() == b"theirs"
    assert [p.name for p in dst.parent.iterdir()] == ["a.pdf"]          # no temp left


def test_link_never_overwrites_either(tmp_path):
    a = tmp_path / "s" / "one" / "one.pdf"
    a.parent.mkdir(parents=True)
    a.write_bytes(b"data")
    dst = tmp_path / "s" / "two" / "two.pdf"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"theirs")
    with pytest.raises(CopyError) as e:
        link_or_copy(a, dst, _sha(b"data"))
    assert e.value.code == "collision" and dst.read_bytes() == b"theirs"
