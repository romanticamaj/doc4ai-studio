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


# ---- fix round (verifier V1): a backup is never a hardlink to a user's file or to someone's work copy

def _pre_v7_doc(store, d):
    if d["source_copy_path"] and Path(d["source_copy_path"]).exists():
        os.unlink(d["source_copy_path"])
    store.set_source_copy(d["id"], path=None, status=None, error=None)
    if d.get("work_copy_path"):
        shutil.rmtree(d["work_copy_path"], ignore_errors=True)
    store.update_document(d["id"], work_copy_path=None, work_copy_expires_at=None)


def test_backfill_never_hardlinks_another_documents_original(tmp_root, fixtures, monkeypatch):
    lb = Lib(tmp_root, fixtures, monkeypatch)
    data = (fixtures / "text.pdf").read_bytes() + b"%same\n"
    a_src = lb.place("alpha.pdf", content=data)
    b_src = tmp_root / "user-docs" / "beta.pdf"
    b_src.parent.mkdir()
    b_src.write_bytes(data)
    _, ta = lb.convert(a_src)
    _, tb = lb.convert(b_src)
    a, b = lb.doc(ta), lb.doc(tb)
    for d in (a, b):
        _pre_v7_doc(lb.store, lb.store.get_document(d["id"]))
    a_src.unlink()                                   # A's own original is gone; B's original (the user's file) is not
    row = ensure_copy(lb.store, lb.cfg, lb.store.get_document(a["id"]))
    assert row["outcome"] == "copied" and row["from"] == "other_document"
    cp = Path(lb.store.get_document(a["id"])["source_copy_path"])
    assert not os.path.samefile(cp, b_src) and os.stat(b_src).st_nlink == 1
    before = b_src.read_bytes()
    with open(cp, "ab") as f:                        # editing the backup never reaches the user's file
        f.write(b"edit in source folder\n")
    assert b_src.read_bytes() == before
    lb.store.close()


def test_backfill_never_hardlinks_another_documents_work_copy(tmp_root, fixtures, monkeypatch):
    lb = Lib(tmp_root, fixtures, monkeypatch)
    data = (fixtures / "text.pdf").read_bytes() + b"%wc\n"
    _, ta = lb.convert(lb.place("one.pdf", content=data))
    _, tb = lb.convert(lb.place("two.pdf", content=data))
    a, b = lb.doc(ta), lb.doc(tb)
    _pre_v7_doc(lb.store, lb.store.get_document(a["id"]))
    os.unlink(lb.store.get_document(b["id"])["source_copy_path"])     # B keeps only its work copy
    lb.store.set_source_copy(b["id"], path=None, status=None, error=None)
    (tmp_root / "in" / "one.pdf").unlink()
    (tmp_root / "in" / "two.pdf").unlink()
    row = ensure_copy(lb.store, lb.cfg, lb.store.get_document(a["id"]))
    assert row["outcome"] == "copied" and row["from"] == "other_document"
    cp = Path(lb.store.get_document(a["id"])["source_copy_path"])
    wc = next(Path(b["work_copy_path"]).glob("src.*"))
    assert not os.path.samefile(cp, wc) and os.stat(cp).st_nlink == 1
    lb.store.close()


def test_backfill_hardlinks_to_another_documents_verified_copy(tmp_root, fixtures, monkeypatch):
    """spec §4.5: a retry / backfill for content that already has a verified copy shares that copy's data."""
    lb = Lib(tmp_root, fixtures, monkeypatch)
    data = (fixtures / "text.pdf").read_bytes() + b"%dedup\n"
    _, ta = lb.convert(lb.place("first.pdf", content=data))
    _, tb = lb.convert(lb.place("second.pdf", content=data))
    a, b = lb.doc(ta), lb.doc(tb)
    os.unlink(b["source_copy_path"])
    lb.store.set_source_copy(b["id"], path=None, status=None, error=None)    # B lost its copy; its original is there
    row = ensure_copy(lb.store, lb.cfg, lb.store.get_document(b["id"]))
    assert row["outcome"] == "copied" and row["from"] == "other_document"
    cb = lb.store.get_document(b["id"])["source_copy_path"]
    assert os.path.samefile(cb, a["source_copy_path"]) and os.stat(cb).st_nlink == 2
    lb.store.close()


def test_backfill_racing_a_conversion_for_the_same_target_keeps_the_conversions_copy(tmp_root, fixtures, monkeypatch):
    """Verifier V2: B (backfill, another out root, same stem) plans source/rep/rep.pdf; before B writes, conversion A
    lands its copy there. B must neither overwrite nor delete A's file."""
    lb = Lib(tmp_root, fixtures, monkeypatch)
    pdf = (fixtures / "text.pdf").read_bytes()
    monkeypatch.setenv("AIDOC_LIBRARY_ROOT", str(tmp_root / "old"))
    _, tb = lb.convert(lb.place("rep.pdf", content=pdf + b"%B\n"))
    b = lb.doc(tb)
    _pre_v7_doc(lb.store, lb.store.get_document(b["id"]))
    monkeypatch.setenv("AIDOC_LIBRARY_ROOT", str(tmp_root))
    from aidoc.config import load_config
    cfg = load_config()
    a_src = tmp_root / "in2" / "rep.pdf"
    a_src.parent.mkdir()
    a_src.write_bytes(pdf + b"%A\n")
    real_plan = source_copy.plan_path
    state = {}

    def plan(store, root, **kw):
        res = real_plan(store, root, **kw)
        if kw.get("doc_id") == b["id"] and not state:
            _, ta = lb.convert(a_src)                    # another worker converts A meanwhile
            state["a"] = lb.doc(ta)
        return res
    monkeypatch.setattr(source_copy, "plan_path", plan)
    row = ensure_copy(lb.store, cfg, lb.store.get_document(b["id"]))
    a = lb.store.get_document(state["a"]["id"])
    assert a["source_copy_status"] == "copied" and source_copy.file_matches(Path(a["source_copy_path"]), a["sha256"])
    assert row["outcome"] == "failed" and row["detail"].startswith("collision")
    assert lb.store.get_document(b["id"])["source_copy_status"] == "failed"         # B retries later
    lb.store.close()


def test_one_physical_file_has_one_owner_even_through_an_unnormalized_root(tmp_root, fixtures, monkeypatch):
    """Verifier P4: a record made under `lib` and a lookup through `lib\\x\\..` name the same file."""
    lb = Lib(tmp_root, fixtures, monkeypatch)
    monkeypatch.setenv("AIDOC_LIBRARY_ROOT", str(tmp_root / "lib"))
    _, ta = lb.convert(lb.place("same.pdf", content=(fixtures / "text.pdf").read_bytes() + b"%P4\n"))
    a = lb.doc(ta)
    cp = Path(a["source_copy_path"])
    alias = os.path.join(str(cp.parent.parent), "x", "..", cp.parent.name, cp.name)
    assert lb.store.document_by_source_copy(alias)["id"] == a["id"]
    lb.store.close()


def test_unreferenced_files_compare_real_paths(tmp_root, fixtures, monkeypatch):
    """Verifier V8: a copy recorded under another spelling of the same path is referenced, not stray."""
    from aidoc.source_copy import unreferenced_files
    lb = Lib(tmp_root, fixtures, monkeypatch)
    _, t = lb.convert(lb.place("spelled.pdf"))
    d = lb.doc(t)
    cp = Path(d["source_copy_path"])
    alias = os.path.join(str(cp.parent.parent), "x", "..", cp.parent.name, cp.name.upper())
    lb.store.set_source_copy(d["id"], path=alias, status="copied", error=None,
                             size=d["source_copy_size"], mtime_ns=d["source_copy_mtime_ns"])
    assert unreferenced_files(lb.store, lb.cfg.source_root()) == 0
    lb.store.close()


def test_already_present_is_rechecked_under_the_lock_before_recording(tmp_root, fixtures, monkeypatch):
    """Verifier V9/P6: B's backfill finds X already present; before B records it, A (whose output write failed)
    discards X — nobody owned it yet. B must not record 'copied' for a file that is gone."""
    lb = Lib(tmp_root, fixtures, monkeypatch)
    pdf = (fixtures / "text.pdf").read_bytes() + b"%P6\n"
    monkeypatch.setenv("AIDOC_LIBRARY_ROOT", str(tmp_root / "old"))
    _, tb = lb.convert(lb.place("rep.pdf", content=pdf))
    b = lb.doc(tb)
    _pre_v7_doc(lb.store, lb.store.get_document(b["id"]))
    monkeypatch.setenv("AIDOC_LIBRARY_ROOT", str(tmp_root))
    from aidoc.config import load_config
    cfg = load_config()
    a_src = tmp_root / "in2" / "rep.pdf"
    a_src.parent.mkdir()
    a_src.write_bytes(pdf)
    placed = source_copy.make_copy(lb.store, cfg.source_root(), doc_id=None, output_dir=str(cfg.output_root() / "rep"),
                                   source_display=str(a_src), sha256=b["sha256"], src=a_src)
    real_set = source_copy._set_status
    state = {}

    def set_status(store, doc, emit, **kw):
        if "x" not in state:
            state["x"] = source_copy.discard_copy(store, placed)        # A's output write failed meanwhile
        return real_set(store, doc, emit, **kw)
    monkeypatch.setattr(source_copy, "_set_status", set_status)
    ensure_copy(lb.store, cfg, lb.store.get_document(b["id"]))
    bb = lb.store.get_document(b["id"])
    assert state["x"] is True
    assert bb["source_copy_status"] != "copied" or source_copy.file_matches(Path(bb["source_copy_path"]), bb["sha256"])
    lb.store.close()


def test_adopting_does_its_copy_outside_the_record_lock(tmp_root, fixtures, monkeypatch):
    """Verifier V9 (stall): the adopt path's make_copy (hashing, maybe a cross-volume copy) must not hold
    RECORD_LOCK; only the final check + record does."""
    import threading as _t

    from aidoc.source_copy import CopyResult, record_copy
    lb = Lib(tmp_root, fixtures, monkeypatch)
    _, ta = lb.convert(lb.place("own.pdf"))
    a = lb.doc(ta)
    _, tb = lb.convert(lb.place("other.pdf", fixture="sample.docx"))
    b = lb.doc(tb)
    free = {}
    real_make = source_copy.make_copy

    def make(*args, **kw):
        def probe():
            free["ok"] = source_copy.RECORD_LOCK.acquire(timeout=0.5)
            if free["ok"]:
                source_copy.RECORD_LOCK.release()
        th = _t.Thread(target=probe)
        th.start()
        th.join()
        return real_make(*args, **kw)
    monkeypatch.setattr(source_copy, "make_copy", make)
    # B tries to record A's path (collision with an owner) and adopts
    st = os.stat(a["source_copy_path"])
    taken = CopyResult(Path(a["source_copy_path"]), st.st_size, st.st_mtime_ns, False, "present")
    record_copy(lb.store, b["id"], taken, None, adopt={"source_root": lb.cfg.source_root(), "output_dir": b["output_dir"],
                                                       "source_display": b["source_path"], "sha256": b["sha256"]})
    assert free.get("ok") is True
    assert source_copy.file_matches(Path(a["source_copy_path"]), a["sha256"])   # theirs, untouched
    lb.store.close()
