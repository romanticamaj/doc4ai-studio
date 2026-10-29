"""Backfill with dry run: copies only what sha256 proves, lists the rest (spec 2026-10-29 §7; plan Task 6: A6, A7)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

from aidoc import source_copy
from aidoc.source_copy import BackfillRunning, InsufficientDisk, backfill, ensure_copy, not_copied, summary
from tests.unit.test_pipeline_source_copy import Lib


def snapshot(root: Path) -> list[tuple[str, int, int]]:
    out = []
    for dirpath, dirs, files in os.walk(root):
        for n in dirs:
            out.append(((Path(dirpath) / n).relative_to(root).as_posix() + "/", 0, 0))
        for n in files:
            p = Path(dirpath) / n
            st = p.stat()
            out.append((p.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns))
    return sorted(out)


def dump_documents(store) -> list[tuple]:
    return [tuple(r) for r in store.con.execute("SELECT * FROM documents ORDER BY id")]


class Built:
    pass


@pytest.fixture
def lib(tmp_root, fixtures, monkeypatch):
    """8 documents converted through the fake pipeline, then put back into the pre-v7 state (spec §14 A6)."""
    lb = Lib(tmp_root, fixtures, monkeypatch)
    pdf = (fixtures / "text.pdf").read_bytes()
    names = {"d1": pdf + b"%1\n", "d2": pdf + b"%2\n", "d3": pdf + b"%1\n", "d4": pdf + b"%4\n", "d5": pdf + b"%5\n",
             "d6": pdf + b"%6\n", "d7": pdf + b"%7\n", "d8": pdf + b"%8\n"}
    docs, srcs = {}, {}
    for key, data in names.items():
        src = lb.place(f"{key}.pdf", content=data)
        _st, t = lb.convert(src)
        docs[key], srcs[key] = lb.doc(t), src
    store = lb.store
    for key, d in docs.items():                                    # back to the pre-v7 state
        out = Path(d["output_dir"])
        side = json.loads((out / f"{out.name}.json").read_text(encoding="utf-8"))
        side.pop("source_copy", None)
        (out / f"{out.name}.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
        if key != "d4" and d["source_copy_path"] and Path(d["source_copy_path"]).exists():
            os.unlink(d["source_copy_path"])
            try:
                Path(d["source_copy_path"]).parent.rmdir()
            except OSError:
                pass
        store.set_source_copy(d["id"], path=None, status=None, error=None)

    def purge_work(key):
        shutil.rmtree(docs[key]["work_copy_path"])
        store.update_document(docs[key]["id"], work_copy_path=None, work_copy_expires_at=None)
    for key in ("d1", "d3", "d4", "d5", "d6", "d7"):
        purge_work(key)
    for key in ("d2", "d3", "d5", "d6"):
        srcs[key].unlink()                                         # originals gone
    srcs["d7"].write_bytes(pdf + b"% rewritten after conversion\n")
    store.set_document_status(docs["d8"]["id"], "orphaned")
    b = Built()
    b.lb, b.store, b.cfg, b.root, b.docs, b.srcs = lb, store, lb.cfg, tmp_root, docs, srcs
    b.ids = {d["id"]: k for k, d in docs.items()}
    yield b
    lb.store.close()


def _outcomes(report, lib) -> dict:
    return {lib.ids[r["id"]]: (r["outcome"], r["from"]) for r in report["documents"]}


def test_backfill_totals(lib):
    r = backfill(lib.store, lib.cfg, dry_run=False)
    assert r["totals"] == {"copied": 3, "already_present": 1, "missing_original": 2, "sha_mismatch": 1,
                           "unreadable": 0, "failed": 0, "skipped_orphaned": 1}            # A6
    got = _outcomes(r, lib)
    assert got["d1"] == ("copied", "original") and got["d2"] == ("copied", "work_copy")
    assert got["d3"] == ("copied", "other_document") and got["d4"] == ("already_present", None)
    assert got["d7"][0] == "sha_mismatch" and got["d8"][0] == "skipped_orphaned"
    for key in ("d1", "d2", "d3", "d4"):
        d = lib.store.get_document(lib.docs[key]["id"])
        assert d["source_copy_status"] == "copied" and source_copy.file_matches(Path(d["source_copy_path"]),
                                                                               d["sha256"])
    assert r["source_dir"] == str(lib.cfg.source_root()) and r["bytes_copied"] > 0 and not r["dry_run"]


def test_dry_run_writes_nothing(lib):
    tree, dump = snapshot(lib.root), dump_documents(lib.store)
    dry = backfill(lib.store, lib.cfg, dry_run=True)
    assert snapshot(lib.root) == tree and dump_documents(lib.store) == dump                   # A6
    assert dry["dry_run"] and dry["bytes_needed"] > 0 and dry["bytes_copied"] == 0
    real = backfill(lib.store, lib.cfg, dry_run=False)
    assert [(d["id"], d["outcome"]) for d in dry["documents"]] == [(d["id"], d["outcome"]) for d in real["documents"]]


def test_never_fabricates(lib):
    before = snapshot(lib.cfg.source_root()) if lib.cfg.source_root().exists() else []
    row = ensure_copy(lib.store, lib.cfg, lib.store.get_document(lib.docs["d7"]["id"]))
    assert row["outcome"] == "sha_mismatch"
    after = snapshot(lib.cfg.source_root()) if lib.cfg.source_root().exists() else []
    assert after == before                                                                    # A7: 0 new files
    assert lib.store.get_document(lib.docs["d7"]["id"])["source_copy_path"] is None


def test_rerun_is_idempotent(lib):
    backfill(lib.store, lib.cfg, dry_run=False)
    tree = snapshot(lib.cfg.source_root())
    r = backfill(lib.store, lib.cfg, dry_run=False)
    assert r["totals"]["copied"] == 0 and r["totals"]["already_present"] == 4
    assert r["totals"]["missing_original"] == 2 and r["totals"]["sha_mismatch"] == 1
    assert snapshot(lib.cfg.source_root()) == tree


def test_unrecoverable_status_written(lib):
    backfill(lib.store, lib.cfg, dry_run=False)
    for key in ("d5", "d6"):
        d = lib.store.get_document(lib.docs[key]["id"])
        assert (d["source_copy_status"], d["source_copy_error"]) == ("unrecoverable", "missing_original")
        assert not_copied(d)
    d7 = lib.store.get_document(lib.docs["d7"]["id"])
    assert d7["source_copy_status"] == "unrecoverable" and d7["source_copy_error"] == f"sha_mismatch: {lib.srcs['d7']}"
    assert lib.store.get_document(lib.docs["d8"]["id"])["source_copy_status"] is None        # orphaned: untouched


def test_sidecars_updated(lib):
    def side(key):
        out = Path(lib.docs[key]["output_dir"])
        return (out / f"{out.name}.json").read_bytes()
    before = {k: side(k) for k in lib.docs}
    backfill(lib.store, lib.cfg, dry_run=False)
    for key in ("d1", "d2", "d3"):
        info = json.loads(side(key))["source_copy"]
        assert info["path"].startswith("source/") and info["sha256"] == lib.docs[key]["sha256"]
    for key in ("d4", "d5", "d6", "d7", "d8"):
        assert side(key) == before[key]


def test_insufficient_disk_copies_nothing(lib, monkeypatch):
    real = source_copy.shutil.disk_usage
    monkeypatch.setattr(source_copy.shutil, "disk_usage", lambda p: type(real(p))(1, 1, 1000))
    tree = snapshot(lib.root)
    with pytest.raises(InsufficientDisk) as e:
        backfill(lib.store, lib.cfg, dry_run=False)
    assert e.value.free == 1000 and e.value.needed > source_copy.SOURCE_COPY_RESERVE_BYTES
    assert snapshot(lib.root) == tree


def test_single_flight(lib, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real = source_copy.ensure_copy

    def slow(*a, **k):
        entered.set()
        release.wait(10)
        return real(*a, **k)
    monkeypatch.setattr(source_copy, "ensure_copy", slow)
    th = threading.Thread(target=lambda: backfill(lib.store, lib.cfg, dry_run=True))
    th.start()
    assert entered.wait(10)
    try:
        with pytest.raises(BackfillRunning):
            backfill(lib.store, lib.cfg, dry_run=True)
    finally:
        release.set()
        th.join(30)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share modes")
def test_locked_candidate_is_unreadable(lib):
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(lib.srcs["d1"]), 0x80000000, 0, None, 3, 0x80, None)
    try:
        row = ensure_copy(lib.store, lib.cfg, lib.store.get_document(lib.docs["d1"]["id"]))
    finally:
        k32.CloseHandle(h)
    assert row["outcome"] == "unreadable"
    d = lib.store.get_document(lib.docs["d1"]["id"])
    assert d["source_copy_status"] == "failed" and d["source_copy_error"].startswith("unreadable")
    assert ensure_copy(lib.store, lib.cfg, d)["outcome"] == "copied"                       # retry works later


def test_report_counts_unreferenced_files(lib):
    stray = lib.cfg.source_root() / "mine" / "notes.txt"
    stray.parent.mkdir(parents=True)
    stray.write_text("the user's own file", encoding="utf-8")
    r = backfill(lib.store, lib.cfg, dry_run=False)
    assert r["unreferenced_files"] == 1 and stray.is_file()


def test_progress_events(lib):
    seen = []
    backfill(lib.store, lib.cfg, dry_run=True, progress=seen.append)
    assert [p["done"] for p in seen[:-1]] == list(range(1, 9)) and all(p["total"] == 8 for p in seen[:-1])
    assert seen[-1]["finished"] is True and seen[-1]["totals"]["copied"] == 3


def test_events_on_status_change(lib):
    events = []
    backfill(lib.store, lib.cfg, dry_run=False, emit=lambda k, p: events.append((k, p)))
    assert {lib.ids[p["document_id"]] for k, p in events if k == "document.source_copy"} == \
        {"d1", "d2", "d3", "d4", "d5", "d6", "d7"}
    events.clear()
    backfill(lib.store, lib.cfg, dry_run=False, emit=lambda k, p: events.append((k, p)))
    assert events == []                                                                       # nothing changed


def test_summary(lib):
    backfill(lib.store, lib.cfg, dry_run=False)
    s = summary(lib.store, lib.cfg)
    assert (s["documents"], s["copied"], s["not_copied"], s["unrecoverable"], s["failed"], s["file_missing"]) == \
        (7, 4, 3, 3, 0, 0)
    os.unlink(lib.store.get_document(lib.docs["d2"]["id"])["source_copy_path"])
    s = summary(lib.store, lib.cfg)
    assert s["copied"] == 3 and s["file_missing"] == 1 and s["source_dir"] == str(lib.cfg.source_root())
