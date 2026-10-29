"""Every conversion keeps a verified source copy; failures are visible, never fatal (spec 2026-10-29 §5;
plan Task 5: A3, A5, A8)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from aidoc import source_copy
from aidoc.batch import register_source
from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.output import read_sidecar
from aidoc.pipeline import run_task, stage_paths
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario


class Lib:
    def __init__(self, root: Path, fixtures: Path, monkeypatch):
        self.root, self.fixtures = root, fixtures
        fake_env(monkeypatch, write_scenario(root / "sc.json"))
        self.cfg = load_config()
        self.store = Store(root / "data" / "aidoc.db")
        self.events: list[tuple[str, dict]] = []

    def emit(self, kind, payload):
        self.events.append((kind, payload))

    def place(self, name: str, fixture: str = "text.pdf", content: bytes | None = None) -> Path:
        p = self.root / "in" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if content is not None:
            p.write_bytes(content)
        else:
            shutil.copy(self.fixtures / fixture, p)
        return p

    def convert(self, src: Path, **opt) -> tuple[TaskStatus, dict]:
        cfg = load_config()                                 # the root at the time of the run (AIDOC_LIBRARY_ROOT)
        opts = ConvertOptions(output_dir=cfg.output_root(), **opt)
        job = self.store.create_job(opts, "cli")
        tid, _ = register_source(self.store, job, src, opts)
        st = run_task(self.store, tid, get_engines(cfg), cfg, emit=self.emit)
        return st, self.store.get_task(tid)

    def upload(self, display: str, fixture: str = "text.pdf") -> tuple[TaskStatus, dict]:
        cfg = load_config()
        data = (self.fixtures / fixture).read_bytes()
        opts = ConvertOptions(output_dir=cfg.output_root())
        job = self.store.create_job(opts, "web")
        import hashlib
        sha = hashlib.sha256(data).hexdigest()
        from aidoc.output import planned_output_dir
        out = planned_output_dir(self.store, opts.output_dir, Path(display), sha)
        tid, _ = self.store.create_task(job, display, sha, len(data), time.time(), opts.lang, str(out))
        work = stage_paths(tid)[0]
        work.mkdir(parents=True, exist_ok=True)
        f = work / f"src{Path(display).suffix.lower()}"
        f.write_bytes(data)
        self.store.update_task(tid, work_path=str(f))
        st = run_task(self.store, tid, get_engines(cfg), cfg, emit=self.emit)
        return st, self.store.get_task(tid)

    def doc(self, task: dict) -> dict:
        return self.store.find_document(task["sha256"], task["output_dir"])


@pytest.fixture
def lib(tmp_root, fixtures, monkeypatch):
    lb = Lib(tmp_root, fixtures, monkeypatch)
    yield lb
    lb.store.close()


def test_six_conversions_all_copied(lib):
    src_root = lib.cfg.source_root()
    assert src_root == lib.root / "source"
    p1 = lib.place("第一章 測試（草稿）.pdf")
    pdf = (lib.fixtures / "text.pdf").read_bytes()
    p2 = lib.place("n" * 196 + ".pdf", content=pdf + b"\n% two\n")    # a 200-character file name
    p3 = lib.place("X/a.pdf", content=pdf + b"\n% three\n")
    p4 = lib.place("Y/a.pdf", content=pdf + b"\n% four: same name, different content\n")
    p5 = lib.place("copy.pdf")                                          # same content as #1
    p6 = lib.place("ro.pdf", fixture="sample.docx")
    os.chmod(p6, stat.S_IREAD)
    try:
        tasks = [lib.convert(p)[1] for p in (p1, p2, p3, p4, p5, p6)]
    finally:
        os.chmod(p6, stat.S_IWRITE | stat.S_IREAD)
    tasks.append(lib.upload("上傳的檔案.pdf", fixture="sample.docx"))
    tasks[-1] = tasks[-1][1]
    docs = [lib.doc(t) for t in tasks]
    assert all(t["status"] == "done" for t in tasks)
    for d in docs:                                                     # A3: 7/7 copied, 7/7 sha match
        assert d["source_copy_status"] == "copied", d
        assert file_sha256(Path(d["source_copy_path"])) == d["sha256"]
        assert Path(d["source_copy_path"]).is_relative_to(src_root)
    assert Path(docs[0]["source_copy_path"]) == src_root / "第一章 測試（草稿）" / "第一章 測試（草稿）.pdf"
    a3, a4 = Path(docs[2]["source_copy_path"]), Path(docs[3]["source_copy_path"])
    assert a3 == src_root / "a" / "a.pdf"
    assert a4.parent.name == f"a-{docs[3]['sha256'][:8]}" and a4.name == "a.pdf"
    s1, s5 = os.stat(docs[0]["source_copy_path"]), os.stat(docs[4]["source_copy_path"])
    assert s1.st_ino == s5.st_ino and s1.st_nlink == 2                 # same content: one hardlinked file
    assert os.access(docs[5]["source_copy_path"], os.W_OK)             # the read-only original's copy is writable
    assert Path(docs[6]["source_copy_path"]).name == "上傳的檔案.pdf"
    assert sum(1 for k, _ in lib.events if k == "document.source_copy") == 7


def test_sidecar_records_relative_copy_path(lib):
    _st, t = lib.convert(lib.place("報告.pdf"))
    side = read_sidecar(Path(t["output_dir"]))
    assert side["source_copy"] == {"path": "source/報告/報告.pdf", "sha256": t["sha256"],
                                   "size": (lib.fixtures / "text.pdf").stat().st_size}


def test_copy_failure_keeps_conversion(lib, monkeypatch):
    real = source_copy.shutil.disk_usage
    monkeypatch.setattr(source_copy.shutil, "disk_usage",
                        lambda p: type(real(p))(real(p).total, real(p).used, 10))
    st, t = lib.convert(lib.place("full.pdf"))
    assert st == TaskStatus.done and t["status"] == "done"             # A5
    d = lib.doc(t)
    assert d["status"] == "ok" == t["quality"]["level"]
    assert d["source_copy_status"] == "failed" and d["source_copy_error"].startswith("no_space")
    assert d["source_copy_path"] is None
    ev = [p for k, p in lib.events if k == "document.source_copy"]
    assert len(ev) == 1 and ev[0]["status"] == "failed" and ev[0]["document_id"] == d["id"]
    assert ev[0]["task_id"] == t["id"] and ev[0]["error"].startswith("no_space")
    assert read_sidecar(Path(t["output_dir"]))["source_copy"] is None
    assert not list(lib.cfg.source_root().rglob("*.part"))
    logs = [p["line"] for k, p in lib.events if k == "task.log"]
    assert any(line.startswith("source copy failed (no_space)") for line in logs)


def test_output_write_failure_rolls_back_new_copy(lib, monkeypatch):
    from aidoc import output

    def boom(self, final_dir):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(output.OutputWriter, "finalize", boom)
    st, t = lib.convert(lib.place("w.pdf"))
    assert st == TaskStatus.failed and "output write failed" in t["error_msg"]
    assert not (lib.cfg.source_root() / "w" / "w.pdf").exists()
    assert not (lib.cfg.source_root() / "w").exists()


def test_output_write_failure_keeps_an_existing_copy(lib, monkeypatch):
    from aidoc import output
    _st, t = lib.convert(lib.place("k.pdf"))
    copy = Path(lib.doc(t)["source_copy_path"])
    monkeypatch.setattr(output.OutputWriter, "finalize", lambda self, d: (_ for _ in ()).throw(OSError(5, "io")))
    st, _t = lib.convert(lib.place("k.pdf"), force=True)
    assert st == TaskStatus.failed and file_sha256(copy) == t["sha256"]


def test_reconvert_reuses_copy(lib):
    src = lib.place("again.pdf")
    _st, t = lib.convert(src)
    copy = Path(lib.doc(t)["source_copy_path"])
    before = copy.stat().st_mtime_ns
    for _ in range(2):
        st, t2 = lib.convert(src, force=True)
        assert st == TaskStatus.done
        assert lib.doc(t2)["source_copy_path"] == str(copy) and lib.doc(t2)["source_copy_status"] == "copied"
    assert copy.stat().st_mtime_ns == before
    assert [p.name for p in copy.parent.iterdir()] == ["again.pdf"]


def test_cache_hit_backfills_missing_copy(lib):
    src = lib.place("hit.pdf")
    _st, t = lib.convert(src)
    d = lib.doc(t)
    os.unlink(d["source_copy_path"])
    lib.store.set_source_copy(d["id"], path=None, status=None, error=None)   # a pre-v7 document
    side = read_sidecar(Path(t["output_dir"]))
    side.pop("source_copy")
    (Path(t["output_dir"]) / "hit.json").write_text(json.dumps(side), encoding="utf-8")
    st, t2 = lib.convert(src)
    assert st == TaskStatus.skipped
    d2 = lib.doc(t2)
    assert d2["source_copy_status"] == "copied" and file_sha256(Path(d2["source_copy_path"])) == d2["sha256"]
    assert read_sidecar(Path(t["output_dir"]))["source_copy"]["path"] == "source/hit/hit.pdf"


def test_copy_goes_to_current_root(lib, monkeypatch, tmp_path):
    _st, t1 = lib.convert(lib.place("one.pdf"))
    other = tmp_path / "lib2"
    monkeypatch.setenv("AIDOC_LIBRARY_ROOT", str(other))
    _st, t2 = lib.convert(lib.place("two.pdf", fixture="sample.docx"))
    c1, c2 = Path(lib.doc(t1)["source_copy_path"]), Path(lib.doc(t2)["source_copy_path"])
    assert c1.is_relative_to(lib.root / "source") and c2.is_relative_to(other / "source")
    assert Path(t2["output_dir"]).is_relative_to(other / "out") and c1.is_file()


def test_retention_never_touches_copies(lib):
    from aidoc.recovery import recover_on_startup
    from aidoc.server.maintenance import Maintenance
    from aidoc.sources import purge_expired_work_copies
    tasks = [lib.convert(lib.place(n, fixture=f))[1]
             for n, f in (("r1.pdf", "text.pdf"), ("r2.docx", "sample.docx"), ("r3.pdf", "big.pdf"))]
    docs = [lib.doc(t) for t in tasks]
    for d in docs:
        lib.store.update_document(d["id"], work_copy_expires_at=time.time() - 10)
    assert purge_expired_work_copies(lib.store, time.time()) == 3

    from aidoc.server.app import build_context
    ctx = build_context(load_config(), token=None, start_workers=False)
    try:
        ctx.config.general.work_retention_days = 0                     # every work dir is past retention
        Maintenance(ctx).run_once()
    finally:
        ctx.close()
    recover_on_startup(lib.store, lib.cfg, log=lambda s: None)
    for d in docs:                                                     # A8: 3/3 still there and matching
        assert file_sha256(Path(d["source_copy_path"])) == d["sha256"]


def test_expired_work_copy_is_kept_while_the_document_has_no_copy(lib, monkeypatch):
    """Verifier V4: retention purged the work copy of a document whose source copy had failed: its last source."""
    from aidoc.sources import purge_expired_work_copies
    real = source_copy.shutil.disk_usage
    monkeypatch.setattr(source_copy.shutil, "disk_usage", lambda p: type(real(p))(real(p).total, real(p).used, 10))
    src = lib.place("only.pdf")
    _st, t = lib.convert(src)
    monkeypatch.setattr(source_copy.shutil, "disk_usage", real)
    d = lib.doc(t)
    assert d["source_copy_status"] == "failed"
    src.unlink()
    lib.store.update_document(d["id"], work_copy_expires_at=time.time() - 10)
    assert purge_expired_work_copies(lib.store, time.time()) == 0
    assert Path(d["work_copy_path"]).is_dir()                       # kept: the only file with this content
    from aidoc.source_copy import ensure_copy
    assert ensure_copy(lib.store, lib.cfg, lib.store.get_document(d["id"]))["outcome"] == "copied"
    assert purge_expired_work_copies(lib.store, time.time()) == 1      # once copied, retention applies again
