import sqlite3
import time
from pathlib import Path

import pytest

from aidoc.models import Attempt, ConvertOptions, JobStatus, TaskStatus
from aidoc.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "aidoc.db")
    yield s
    s.close()


def test_schema_and_pragmas(store, tmp_path):
    con = sqlite3.connect(tmp_path / "aidoc.db")
    names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert {"jobs", "tasks", "segments", "documents", "uploads", "events", "meta"} <= names
    assert con.execute("pragma journal_mode").fetchone()[0] == "wal"
    con.close()


def test_job_task_lifecycle(store):
    job = store.create_job(ConvertOptions(output_dir=Path("out")), "cli")
    tid, reused = store.create_task(job, "C:/a.pdf", "ab" * 32, 10, 1.0, "cht", "C:/out/a")
    assert not reused and store.get_task(tid)["status"] == "queued"
    store.update_task(tid, status=TaskStatus.converting, engine="docling")
    t = store.get_task(tid)
    assert t["status"] == "converting" and t["engine"] == "docling" and t["updated_at"] >= t["created_at"]
    store.append_attempt(tid, Attempt(engine="docling", attempt=1, score=0.2, reasons=["garbage_ratio"]))
    assert store.get_task(tid)["tried"][0]["engine"] == "docling"
    assert store.refresh_job_status(job) == JobStatus.running
    store.update_task(tid, status=TaskStatus.done)
    assert store.refresh_job_status(job) == JobStatus.done and store.get_job(job)["status"] == "done"


def test_task_reuse_non_terminal(store):
    j1 = store.create_job(ConvertOptions(output_dir=Path("out")), "cli")
    tid, _ = store.create_task(j1, "a.pdf", "x" * 64, 1, 1.0, "cht", "out/a")
    store.update_task(tid, status=TaskStatus.converting)
    j2 = store.create_job(ConvertOptions(output_dir=Path("out")), "cli")
    tid2, reused = store.create_task(j2, "a.pdf", "x" * 64, 1, 1.0, "cht", "out/a")
    assert reused and tid2 == tid and store.get_task(tid)["job_id"] == j2


def test_task_terminal_replaced(store):
    j = store.create_job(ConvertOptions(output_dir=Path("out")), "cli")
    tid, _ = store.create_task(j, "a.pdf", "x" * 64, 1, 1.0, "cht", "out/a")
    store.create_segments(tid, [(1, 40), (41, 45)])
    store.update_task(tid, status=TaskStatus.done)
    tid2, reused = store.create_task(j, "a.pdf", "x" * 64, 1, 1.0, "cht", "out/a")
    assert not reused and tid2 != tid and store.get_task(tid) is None and store.list_segments(tid) == []


def test_segments(store):
    j = store.create_job(ConvertOptions(output_dir=Path("out")), "cli")
    tid, _ = store.create_task(j, "a.pdf", "y" * 64, 1, 1.0, "cht", "out/b")
    ids = store.create_segments(tid, [(1, 40), (41, 45)])
    segs = store.list_segments(tid)
    assert [s["idx"] for s in segs] == [0, 1] and segs[1]["page_end"] == 45 and segs[0]["status"] == "queued"
    store.update_segment(ids[0], status="done", output_path="p")
    store.reset_segments(tid)
    assert all(s["status"] == "queued" and s["output_path"] is None for s in store.list_segments(tid))


def test_documents(store):
    did = store.upsert_document(sha256="z" * 64, source_path="a.pdf", output_dir="out/a", engine="mineru",
                                quality={"score": 1, "level": "ok", "reasons": []}, pages=3, lang="cht",
                                aidoc_version="0.1.0", status="ok", work_copy_path=None, work_copy_expires_at=None)
    assert store.find_document("z" * 64, "out/a")["id"] == did
    did2 = store.upsert_document(sha256="w" * 64, source_path="b.pdf", output_dir="out/a", engine="docling",
                                 quality={}, pages=1, lang="cht", aidoc_version="0.1.0", status="low",
                                 work_copy_path=None, work_copy_expires_at=None)
    assert did2 == did and store.get_document(did)["engine"] == "docling"
    assert [d["id"] for d in store.list_documents(status="low")] == [did]
    assert store.known_output_dirs() == {"out/a"}
    store.set_document_status(did, "orphaned")
    assert store.delete_documents("orphaned") == 1


def test_uploads_and_events(store):
    uid = store.create_upload("f.pdf", 100, "q" * 64)
    store.update_upload(uid, received=50)
    assert store.get_upload(uid)["received"] == 50
    assert store.stale_uploads(time.time() + 1) == [store.get_upload(uid)]
    s1 = store.append_event("task.updated", "t1", {"a": 1})
    s2 = store.append_event("task.log", "t1", {"line": "x"})
    assert s2 == s1 + 1 and [e["seq"] for e in store.events_since(s1)] == [s2]
    for _ in range(20):
        store.append_event("x", None, {})
    store.prune_events(keep=5)
    assert store.oldest_event_seq() == s2 + 16


def test_failed_and_cancelled_reused_keeping_segments(store):
    j = store.create_job(ConvertOptions(output_dir=Path("out")), "cli")
    tid, _ = store.create_task(j, "a.pdf", "k" * 64, 1, 1.0, "cht", "out/k")
    ids = store.create_segments(tid, [(1, 40), (41, 45)]); store.update_segment(ids[0], status="done", output_path="p")
    store.update_task(tid, status=TaskStatus.cancelled, pid=123)
    tid2, reused = store.create_task(j, "a.pdf", "k" * 64, 1, 1.0, "cht", "out/k")
    assert reused and tid2 == tid and store.get_task(tid)["status"] == "queued" and store.get_task(tid)["pid"] is None
    assert store.list_segments(tid)[0]["status"] == "done"
    store.requeue_task(tid, reset_segments=True, new_sha="m" * 64)
    assert store.list_segments(tid)[0]["status"] == "queued" and store.get_task(tid)["sha256"] == "m" * 64


def test_failed_input_reused_with_history(store):
    j = store.create_job(ConvertOptions(output_dir=Path("out")), "cli")
    tid, _ = store.create_task(j, "a.pdf", "f" * 64, 1, 1.0, "cht", "out/f")
    store.append_attempt(tid, Attempt(engine="docling", attempt=1, score=None, reasons=[], error_kind="input"))
    store.update_task(tid, status=TaskStatus.failed, error_kind="input", error_msg="source_missing: a.pdf", attempt=1)
    j2 = store.create_job(ConvertOptions(output_dir=Path("out")), "cli")
    tid2, reused = store.create_task(j2, "a.pdf", "f" * 64, 1, 1.0, "cht", "out/f")
    t = store.get_task(tid)
    assert reused and tid2 == tid and t["job_id"] == j2 and t["status"] == "queued"
    assert t["error_kind"] is None and t["error_msg"] is None and t["attempt"] == 1 and len(t["tried"]) == 1
    store.requeue_task(tid, reset_segments=False)
    assert store.get_task(tid)["status"] == "queued"


def test_store_is_safe_to_share_between_threads(store):
    """Watcher threads poll the Store the pipeline writes to (final-review follow-up: flaky InterfaceError)."""
    import threading
    j = store.create_job(ConvertOptions(output_dir=Path("out")), "cli")
    tid, _ = store.create_task(j, "a.pdf", "t" * 64, 1, 1.0, "cht", "out/t")
    errors = []

    def hammer(k):
        try:
            for i in range(300):
                store.update_task(tid, error_msg=f"{k}-{i}")
                assert store.get_task(tid)["id"] == tid
                store.list_tasks(status_in=["queued"])
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=hammer, args=(k,)) for k in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert errors == []
