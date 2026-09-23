import json
import os
import shutil
import time

import pytest

from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.sources import SourceError, purge_expired_work_copies, stage_source
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario


def test_stage_hardlink_or_copy(tmp_path, fixtures):
    src = tmp_path / "a.pdf"; shutil.copy(fixtures / "text.pdf", src)
    staged = stage_source(src, file_sha256(src), tmp_path / "work")
    assert staged.name == "src.pdf" and file_sha256(staged) == file_sha256(src)
    assert os.stat(staged).st_nlink == 2 or staged.stat().st_size == src.stat().st_size


def test_stage_is_idempotent_and_survives_moved_original(tmp_path, fixtures):
    src = tmp_path / "A.PDF"; shutil.copy(fixtures / "text.pdf", src); sha = file_sha256(src)
    staged = stage_source(src, sha, tmp_path / "work")
    assert staged.name == "src.pdf" and stage_source(src, sha, tmp_path / "work") == staged
    src.unlink()                                  # original moved away after staging: the work copy is still valid
    assert stage_source(src, sha, tmp_path / "work") == staged


def test_stage_upload_already_in_work_dir(tmp_path, fixtures):
    w = tmp_path / "work"; w.mkdir(); up = w / "src.pdf"; shutil.copy(fixtures / "text.pdf", up)
    assert stage_source(up, file_sha256(up), w) == up
    with pytest.raises(SourceError) as e:
        stage_source(up, "0" * 64, w)
    assert e.value.code == "source_changed"


def test_missing_and_changed(tmp_path, fixtures):
    with pytest.raises(SourceError) as e:
        stage_source(tmp_path / "nope.pdf", "x", tmp_path / "w")
    assert e.value.code == "source_missing"
    src = tmp_path / "a.pdf"; shutil.copy(fixtures / "text.pdf", src)
    with pytest.raises(SourceError) as e:
        stage_source(src, "0" * 64, tmp_path / "w")
    assert e.value.code == "source_changed" and not (tmp_path / "w" / "src.pdf").exists()


def test_pipeline_source_changed(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json")); cfg = load_config()
    store = Store(tmp_root / "data" / "aidoc.db")
    src = tmp_root / "a.pdf"; shutil.copy(fixtures / "text.pdf", src); sha = file_sha256(src)
    job = store.create_job(ConvertOptions(output_dir=tmp_root / "out"), "cli")
    tid, _ = store.create_task(job, str(src), sha, 1, 1.0, "cht", str(tmp_root / "out" / "a"))
    shutil.copy(fixtures / "sample.docx", src)                           # modified after job creation
    assert run_task(store, tid, get_engines(cfg), cfg) == TaskStatus.failed
    assert store.get_task(tid)["error_msg"].startswith("source_changed")
    assert store.get_task(tid)["error_kind"] == "input"
    src.unlink(); tid2, _ = store.create_task(job, str(src), sha, 1, 1.0, "cht", str(tmp_root / "out" / "a2"))
    assert run_task(store, tid2, get_engines(cfg), cfg) == TaskStatus.failed
    assert store.get_task(tid2)["error_msg"].startswith("source_missing")


def test_engine_reads_work_copy_not_original(tmp_root, fixtures, monkeypatch):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json")); cfg = load_config()
    store = Store(tmp_root / "data" / "aidoc.db")
    src = tmp_root / "a.pdf"; shutil.copy(fixtures / "text.pdf", src)
    job = store.create_job(ConvertOptions(output_dir=tmp_root / "out"), "cli")
    tid, _ = store.create_task(job, str(src), file_sha256(src), 1, 1.0, "cht", str(tmp_root / "out" / "a"))
    assert run_task(store, tid, get_engines(cfg), cfg) == TaskStatus.done
    call = json.loads((tmp_root / "calls.jsonl").read_text().splitlines()[0])
    assert call["source"].startswith("src") or call["source"].startswith("seg_")
    doc = store.find_document(file_sha256(src), str(tmp_root / "out" / "a"))
    assert doc["work_copy_path"] and doc["work_copy_expires_at"] > time.time() + 6 * 86400
    assert (tmp_root / "data" / "work" / tid) == __import__("pathlib").Path(doc["work_copy_path"])


def test_purge_expired(tmp_root):
    store = Store(tmp_root / "data" / "aidoc.db"); w = tmp_root / "data" / "work" / "t"; w.mkdir(parents=True)
    (w / "src.pdf").write_bytes(b"x")
    keep = tmp_root / "data" / "work" / "k"; keep.mkdir(); (keep / "src.pdf").write_bytes(b"y")
    store.upsert_document(sha256="a" * 64, source_path="a", output_dir="o", engine="e", quality={}, pages=1,
                          lang="cht", aidoc_version="0.1.0", status="ok", work_copy_path=str(w),
                          work_copy_expires_at=time.time() - 1)
    store.upsert_document(sha256="b" * 64, source_path="b", output_dir="o2", engine="e", quality={}, pages=1,
                          lang="cht", aidoc_version="0.1.0", status="ok", work_copy_path=str(keep),
                          work_copy_expires_at=time.time() + 100)
    assert purge_expired_work_copies(store, time.time()) == 1 and not w.exists() and keep.exists()
    docs = {d["source_path"]: d for d in store.list_documents()}
    assert docs["a"]["work_copy_path"] is None and docs["a"]["work_copy_expires_at"] is None
    assert docs["b"]["work_copy_path"] == str(keep)


def _readonly(p) -> bool:
    import stat
    return not (os.stat(p).st_mode & stat.S_IWRITE)


def test_read_only_source_is_never_made_writable(tmp_path, fixtures):
    """Final review I2: NTFS attributes are shared by hardlinks; cleaning a work copy must not touch the source."""
    import stat

    from aidoc import fsops
    src = tmp_path / "ro.pdf"; shutil.copy(fixtures / "text.pdf", src); os.chmod(src, stat.S_IREAD)
    try:
        stage_source(src, file_sha256(src), tmp_path / "work")
        fsops.remove_tree(tmp_path / "work")
        assert _readonly(src) and not (tmp_path / "work").exists()
        # made read-only only after it was hardlinked: cleanup must fail rather than flip the user's flag
        src2 = tmp_path / "rw.pdf"; shutil.copy(fixtures / "text.pdf", src2)
        staged = stage_source(src2, file_sha256(src2), tmp_path / "work2")
        os.chmod(src2, stat.S_IREAD)
        if os.stat(staged).st_nlink > 1:
            with pytest.raises(OSError):
                fsops.remove_tree(tmp_path / "work2")
            assert _readonly(src2)
    finally:
        for p in (src, tmp_path / "rw.pdf"):
            if p.exists():
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
